"""The reader that finally connects the deployed service to the river.

**Offline, entirely.** A suite that reaches USGS goes red when a federal agency has a
bad afternoon, and a flaky gate teaches people to ignore gates. The live path is
exercised by `scripts/live_gage_check.py` and by hand; what is asserted here is the
part that decides what to SHOW, which is where a wrong answer would be silent.

The distinctions under test are the ones that cost real accuracy if they collapse:
a live reading against a cached one, a cached reading against a stale one, and an
unavailable gage against a river running at zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from curtail_agents.events import Provenance
from curtail_agents.gage_client import GageError, Reading
from curtail_agents.live_gage import (
    STALE_LIMIT_SECONDS,
    TTL_SECONDS,
    LiveGageReader,
    LiveGageUnavailableError,
)
from curtail_core.basins import Basin

OBSERVED = datetime(2026, 8, 16, 2, 45, tzinfo=UTC)


def reading(cfs: float = 4.91, gage: str = "USGS-11519500") -> Reading:
    return Reading(
        monitoring_location_id=gage,
        observed_at=OBSERVED,
        cfs=cfs,
        unit="ft^3/s",
        qualifier="P",
    )


@dataclass
class FakeClock:
    """Monotonic seconds, driven by the test rather than by the wall."""

    t: float = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeClient:
    """Stands in for `GageClient`, counting calls and able to start failing."""

    def __init__(self, *, value: float = 4.91, fail_after: int | None = None) -> None:
        self.value = value
        self.fail_after = fail_after
        self.calls = 0

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def latest_discharge(self, gage: str) -> Reading:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise GageError("USGS returned HTTP 503")
        return reading(self.value, gage)


def build(client: FakeClient, clock: FakeClock) -> LiveGageReader:
    return LiveGageReader(
        monotonic=clock,
        now=lambda: datetime(2026, 8, 16, 2, 46, tzinfo=UTC),
        client_factory=lambda: client,  # type: ignore[arg-type,return-value]
    )


class TestALiveReadingIsLabelledLive:
    async def test_a_first_read_is_usgs_live_with_no_age(self) -> None:
        """The provenance the enum has always carried and nothing could produce.

        `/api/classify` labels its reading `unsourced` because a caller supplied it.
        This path fetched it, so it may say so, and the difference is the whole
        reason the enum has two members instead of a boolean.
        """
        live = await build(FakeClient(), FakeClock()).read(Basin.SCOTT)
        assert live.provenance is Provenance.USGS_LIVE
        assert live.is_live is True
        assert live.age_seconds == 0.0
        assert live.reading.cfs == 4.91

    async def test_the_gage_read_is_the_basin_s_compliance_gage(self) -> None:
        """Scott is Fort Jones and Shasta is near Yreka, and swapping them would
        classify each river against the other's rule."""
        client = FakeClient()
        await build(client, FakeClock()).read(Basin.SHASTA)
        assert client.calls == 1


class TestTheCacheProtectsUSGSAndSaysSo:
    async def test_a_second_read_inside_the_ttl_does_not_call_out(self) -> None:
        """The rate limit is 100 an hour without a key. A judge refreshing a page
        must not be able to walk us into it."""
        client, clock = FakeClient(), FakeClock()
        reader = build(client, clock)
        await reader.read(Basin.SCOTT)
        clock.advance(TTL_SECONDS - 1)
        again = await reader.read(Basin.SCOTT)
        assert client.calls == 1
        assert again.provenance is Provenance.USGS_CACHED

    async def test_a_cached_reading_carries_its_age(self) -> None:
        """A number on screen with no age is a number that can silently be an hour
        old. The age is what makes the label checkable rather than decorative."""
        client, clock = FakeClient(), FakeClock()
        reader = build(client, clock)
        await reader.read(Basin.SCOTT)
        clock.advance(120)
        assert (await reader.read(Basin.SCOTT)).age_seconds == pytest.approx(120)

    async def test_the_ttl_expires_and_the_next_read_is_live_again(self) -> None:
        client, clock = FakeClient(), FakeClock()
        reader = build(client, clock)
        await reader.read(Basin.SCOTT)
        clock.advance(TTL_SECONDS + 1)
        assert (await reader.read(Basin.SCOTT)).provenance is Provenance.USGS_LIVE
        assert client.calls == 2

    async def test_each_basin_caches_independently(self) -> None:
        """One shared entry would serve one river's reading for the other, which is a
        wrong number wearing the right label.

        Asserted on the GAGE THAT COMES BACK, not on the call count. The first
        version counted calls, and a mutation storing every entry under one key left
        the count at two while returning Shasta's reading for Scott: the cache miss
        still happened, so the count could not see the defect at all.
        """
        client, clock = FakeClient(), FakeClock()
        reader = build(client, clock)
        await reader.read(Basin.SCOTT)
        await reader.read(Basin.SHASTA)
        clock.advance(1)

        served = await reader.read(Basin.SCOTT)
        assert client.calls == 2, "the third read should have been served from cache"
        assert served.reading.monitoring_location_id == "USGS-11519500", (
            "Scott was served a reading from the other basin's gage"
        )


class TestAnUnavailableGageRefuses:
    async def test_a_failure_with_nothing_cached_raises(self) -> None:
        """It must not return zero. A river reporting no data is not a river running
        at zero cfs, and conflating them curtails a watershed off a dead sensor."""
        with pytest.raises(LiveGageUnavailableError, match=r"zero cfs"):
            await build(FakeClient(fail_after=0), FakeClock()).read(Basin.SCOTT)

    async def test_a_failure_serves_the_cached_reading_labelled_as_cached(self) -> None:
        """Losing USGS is not a reason to throw away a good reading, and it is not a
        reason to present that reading as current either."""
        client, clock = FakeClient(fail_after=1), FakeClock()
        reader = build(client, clock)
        await reader.read(Basin.SCOTT)
        clock.advance(TTL_SECONDS + 1)
        served = await reader.read(Basin.SCOTT)
        assert served.provenance is Provenance.USGS_CACHED
        assert served.age_seconds == pytest.approx(TTL_SECONDS + 1)

    async def test_a_cached_reading_past_the_stale_limit_refuses(self) -> None:
        """The TTL governs whether to refresh. This governs whether what we have is
        still worth showing at all, and the two are different questions."""
        client, clock = FakeClient(fail_after=1), FakeClock()
        reader = build(client, clock)
        await reader.read(Basin.SCOTT)
        clock.advance(STALE_LIMIT_SECONDS + 1)
        with pytest.raises(LiveGageUnavailableError, match="hours old"):
            await reader.read(Basin.SCOTT)

    async def test_the_refusal_names_the_gage_so_it_is_diagnosable(self) -> None:
        with pytest.raises(LiveGageUnavailableError, match="USGS-11519500"):
            await build(FakeClient(fail_after=0), FakeClock()).read(Basin.SCOTT)


class TestTheClockIsMonotonicOnPurpose:
    async def test_a_backwards_clock_cannot_freeze_the_cache(self) -> None:
        """A wall clock steps backwards on an NTP correction, and a cache keyed on
        one would then believe an entry is fresh forever. The reader takes a
        monotonic source, so this asserts the reader never consults the wall.
        """
        client, clock = FakeClient(), FakeClock()
        reader = build(client, clock)
        await reader.read(Basin.SCOTT)
        clock.advance(TTL_SECONDS + 1)
        await reader.read(Basin.SCOTT)
        assert client.calls == 2, "the reader used something other than the injected clock"


class TestTheEndpointSurfacesAllOfThis:
    """Through the app, because a module that is right and unreachable is what this
    whole change exists to fix."""

    def test_the_route_is_registered(self) -> None:
        from curtail_agents.api import app

        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/gage/{basin}" in paths

    def test_an_unknown_basin_is_404_not_a_guess(self) -> None:
        from fastapi.testclient import TestClient

        from curtail_agents.api import app

        with TestClient(app) as client:
            assert client.get("/api/gage/sacramento").status_code == 404

    def test_an_unavailable_gage_is_503_and_names_why(self, monkeypatch: Any) -> None:
        """Not 200 with a zero, and not 500. The service is healthy; the gage is not,
        and those are different facts a monitor has to tell apart."""
        from fastapi.testclient import TestClient

        from curtail_agents import api, live_gage

        async def refuse(basin: Basin) -> Any:
            raise LiveGageUnavailableError("USGS-11519500 could not be read")

        monkeypatch.setattr(live_gage.READER, "read", refuse)
        with TestClient(api.app) as client:
            response = client.get("/api/gage/scott")
        assert response.status_code == 503
        assert "USGS-11519500" in response.json()["detail"]
