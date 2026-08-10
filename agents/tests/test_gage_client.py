"""Tests for the USGS gage client.

All offline. Live network calls do not belong in a suite that must pass
deterministically in CI, so responses are stubbed with real payload shapes
captured from the API. The live smoke check lives in scripts/, not here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from curtail_agents.gage_client import GageClient, GageError, Reading


def _client(handler: httpx.MockTransport) -> GageClient:
    return GageClient(client=httpx.AsyncClient(transport=handler))


def _feature(time: str, value: float, qualifier: str | None = None) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "time": time,
            "value": value,
            "unit_of_measure": "ft^3/s",
            "qualifier": qualifier,
        },
    }


class TestLatestDischarge:
    @pytest.mark.asyncio
    async def test_parses_a_real_shaped_response(self) -> None:
        """Payload shape captured live from the Shasta gage on 2026-08-10."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["f"] == "json"
            assert request.url.params["parameter_code"] == "00060"
            return httpx.Response(
                200, json={"features": [_feature("2026-08-10T18:00:00+00:00", 49.1)]}
            )

        async with _client(httpx.MockTransport(handler)) as client:
            reading = await client.latest_discharge("USGS-11517500")

        assert reading.cfs == 49.1
        assert reading.unit == "ft^3/s"
        assert reading.observed_at == datetime(2026, 8, 10, 18, 0, tzinfo=UTC)
        assert reading.monitoring_location_id == "USGS-11517500"

    @pytest.mark.asyncio
    async def test_empty_features_raises_rather_than_returning_zero(self) -> None:
        """An absent reading is not a reading of zero.

        This is the single most dangerous confusion available in this codebase.
        A river reporting no data treated as a river at 0 cfs would curtail an
        entire watershed off a dead sensor.
        """

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": []})

        async with _client(httpx.MockTransport(handler)) as client:
            with pytest.raises(GageError, match="not a reading of zero"):
                await client.latest_discharge("USGS-11519500")

    @pytest.mark.asyncio
    async def test_non_200_raises(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={})

        async with _client(httpx.MockTransport(handler)) as client:
            with pytest.raises(GageError, match="HTTP 503"):
                await client.latest_discharge("USGS-11519500")

    @pytest.mark.asyncio
    async def test_transport_failure_raises_rather_than_returning_stale(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection reset")

        async with _client(httpx.MockTransport(handler)) as client:
            with pytest.raises(GageError, match="request failed"):
                await client.latest_discharge("USGS-11519500")

    @pytest.mark.asyncio
    async def test_missing_value_raises(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"features": [{"properties": {"time": "2026-08-10T18:00:00+00:00"}}]}
            )

        async with _client(httpx.MockTransport(handler)) as client:
            with pytest.raises(GageError, match="incomplete reading"):
                await client.latest_discharge("USGS-11519500")


class TestProvisionalFlag:
    def test_provisional_qualifier_is_surfaced(self) -> None:
        r = Reading("USGS-11519500", datetime(2026, 8, 10, tzinfo=UTC), 6.28, "ft^3/s", "P")
        assert r.is_provisional

    def test_approved_reading_is_not_provisional(self) -> None:
        r = Reading("USGS-11519500", datetime(2026, 8, 10, tzinfo=UTC), 6.28, "ft^3/s", "A")
        assert not r.is_provisional

    def test_absent_qualifier_is_not_provisional(self) -> None:
        r = Reading("USGS-11519500", datetime(2026, 8, 10, tzinfo=UTC), 6.28, "ft^3/s", None)
        assert not r.is_provisional


class TestRevisions:
    @pytest.mark.asyncio
    async def test_parses_revision_feed(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "properties": {
                                "time": "2025-07-22T00:00:00+00:00",
                                "explanation": "Rating curve shifted after field measurements.",
                            }
                        }
                    ]
                },
            )

        async with _client(httpx.MockTransport(handler)) as client:
            revs = await client.revisions("USGS-11519500")

        assert len(revs) == 1
        assert revs[0].revised_at == datetime(2025, 7, 22, tzinfo=UTC)
        assert revs[0].explanation is not None
        assert "Rating curve" in revs[0].explanation
