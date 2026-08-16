"""The hydrograph endpoint, and the reason it computes the threshold itself.

**The minimum flow is a STEP FUNCTION and drawing it as a constant is a wrong answer.**
Scott drops from 125 to 90 cfs on June 24, Shasta from 125 to 105 on March 25 and back up
to 75 on September 16. A chart that draws one flat rule across a window containing such a
boundary shows the river crossing a threshold that was not in force on the day it crossed,
which is the exact error a month-keyed table produces and the reason this system encodes
date periods instead.

Offline. The client is stubbed, because what is under test is the shape the endpoint
computes, and a suite that depends on a federal agency's uptime is a flake with a
deadline attached.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest
from fastapi.testclient import TestClient

from curtail_agents.gage_client import GageError, Reading


@pytest.fixture
def client() -> TestClient:
    from curtail_agents.api import app

    return TestClient(app)


class StubClient:
    """A `GageClient` that returns a fixed series, or fails."""

    def __init__(self, readings: list[Reading] | None = None, *, fail: bool = False) -> None:
        self.readings = readings if readings is not None else []
        self.fail = fail

    async def __aenter__(self) -> StubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def discharge_series(self, gage: str, **kwargs: Any) -> list[Reading]:
        if self.fail:
            raise GageError("USGS returned HTTP 503")
        return self.readings


def series(count: int = 4, gage: str = "USGS-11519500") -> list[Reading]:
    start = datetime.now(UTC) - timedelta(days=2)
    return [
        Reading(
            monitoring_location_id=gage,
            observed_at=start + timedelta(hours=i),
            cfs=10.0 + i,
            unit="ft^3/s",
            qualifier="P",
        )
        for i in range(count)
    ]


def stub(monkeypatch: Any, client_obj: StubClient) -> None:
    from curtail_agents import api

    monkeypatch.setattr(api, "GageClient", lambda **kwargs: client_obj)


class TestTheThresholdIsAStepSeries:
    def test_a_window_spanning_a_boundary_emits_both_minimums(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        """Scott's August minimum is 30 cfs and July's is 50. A window covering the
        turn of the month must carry BOTH, or every July reading is judged against
        the August rule or the reverse."""
        stub(monkeypatch, StubClient(series()))
        body = client.get("/api/hydrograph/scott?days=45").json()
        values = [s["minimum_cfs"] for s in body["minimum_steps"]]
        assert 30.0 in values, f"the August minimum is absent: {body['minimum_steps']}"
        assert len(values) >= 2, "a window crossing a schedule boundary emitted one step"

    def test_the_steps_are_in_ascending_date_order(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        """Out of order, a step chart draws the rule going backwards in time."""
        stub(monkeypatch, StubClient(series()))
        steps = client.get("/api/hydrograph/scott?days=120").json()["minimum_steps"]
        assert steps == sorted(steps, key=lambda s: s["from"])

    def test_consecutive_steps_never_repeat_a_value(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        """A step is a CHANGE. Emitting one per day would be 180 identical points
        and would hide the boundaries among them."""
        stub(monkeypatch, StubClient(series()))
        values = [
            s["minimum_cfs"]
            for s in client.get("/api/hydrograph/scott?days=120").json()["minimum_steps"]
        ]
        assert all(a != b for a, b in pairwise(values))

    def test_a_short_window_inside_one_period_emits_exactly_one(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        """Guards the guard. If every window produced many steps, the tests above
        would pass against an endpoint emitting one per day."""
        stub(monkeypatch, StubClient(series()))
        body = client.get("/api/hydrograph/shasta?days=1").json()
        assert len(body["minimum_steps"]) == 1

    def test_the_band_width_is_returned_so_the_client_does_not_hardcode_it(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        stub(monkeypatch, StubClient(series()))
        assert client.get("/api/hydrograph/scott?days=7").json()["near_threshold_band_cfs"] == 10.0


class TestTheSeriesItself:
    def test_readings_come_back_oldest_first_with_their_times(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        stub(monkeypatch, StubClient(series(5)))
        body = client.get("/api/hydrograph/scott?days=7").json()
        assert body["count"] == 5
        times = [p["t"] for p in body["series"]]
        assert times == sorted(times)
        assert body["series"][0]["cfs"] == 10.0

    def test_an_empty_window_is_an_answer_not_an_error(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        """A gage genuinely may not have reported. 200 with count 0 says that; a 500
        would claim this service is broken when it is working perfectly."""
        stub(monkeypatch, StubClient([]))
        response = client.get("/api/hydrograph/scott?days=7")
        assert response.status_code == 200
        assert response.json()["count"] == 0
        assert response.json()["series"] == []

    def test_an_unreachable_usgs_is_503_naming_the_reason(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        """Distinct from the empty window above, and that distinction is the point:
        one is 'the river did not report', the other is 'we could not ask'."""
        stub(monkeypatch, StubClient(fail=True))
        response = client.get("/api/hydrograph/scott?days=7")
        assert response.status_code == 503
        assert "USGS" in response.json()["detail"]

    def test_the_gage_is_the_basin_s_compliance_gage(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        stub(monkeypatch, StubClient(series()))
        assert client.get("/api/hydrograph/shasta?days=7").json()["gage_id"] == "USGS-11517500"


class TestItRefusesRatherThanGuessing:
    def test_an_unknown_basin_is_404(self, client: TestClient) -> None:
        assert client.get("/api/hydrograph/sacramento").status_code == 404

    @pytest.mark.parametrize("days", [0, -1, 181, 10_000])
    def test_a_window_outside_the_bound_is_422(self, client: TestClient, days: int) -> None:
        """An unbounded window is a request this service makes of USGS on a caller's
        behalf, so the bound protects somebody else's rate limit as well as ours."""
        response = client.get(f"/api/hydrograph/scott?days={days}")
        assert response.status_code == 422
        assert "between 1 and" in response.json()["detail"]

    def test_the_bound_itself_is_accepted(self, client: TestClient, monkeypatch: Any) -> None:
        """Off-by-one guard. A bound that rejects its own limit is a different bug."""
        stub(monkeypatch, StubClient(series()))
        assert client.get("/api/hydrograph/scott?days=180").status_code == 200


class TestTheVendoredBundle:
    """Self-hosted, so no request from the console reaches a third party."""

    def test_the_bundle_is_served_from_this_origin(self, client: TestClient) -> None:
        response = client.get("/vendor/echarts.js")
        assert response.status_code == 200
        assert len(response.content) > 100_000, "that is not the charting bundle"
        assert b"Apache" in response.content[:2000], "the licence notice was stripped"

    def test_it_is_an_allowlist_and_not_a_path_map(self, client: TestClient) -> None:
        """A route that maps a request path onto the filesystem is how an asset
        endpoint becomes an arbitrary file read."""
        assert client.get("/vendor/nope.js").status_code == 404
        assert client.get("/vendor/..%2F..%2Fapi.py").status_code == 404

    def test_the_console_loads_it_from_this_origin_and_not_a_cdn(self) -> None:
        """Asserted on the source. A CDN reference would make a judge's visit
        observable to a third party and would put the page at the mercy of somebody
        else's uptime through a month of judging."""
        from curtail_agents.api import CONSOLE

        page = CONSOLE.read_text()
        assert 'import("/vendor/echarts.js")' in page
        for host in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.", "//cdn."):
            assert host not in page, f"the console fetches from {host}"


class TestTheVendorExclusionCannotGrow:
    """The em-dash gate excludes the vendor directory, so this bounds it.

    ECharts is 1.1 MB of minified upstream code containing the character the
    constitution bans, and it cannot be edited. Excluding it scopes the guard to what
    it governs. But an exclusion is a hole, and a hole that can widen without anyone
    noticing is how a rule quietly stops applying: a prose file dropped into that
    directory would escape the gate entirely.

    So the directory's contents are asserted against the same allowlist the route
    serves from. Adding a bundle means adding it to `VENDOR`, which is a visible code
    change; dropping anything else in there fails here.
    """

    def test_the_directory_holds_only_the_allowlisted_bundles(self) -> None:
        from pathlib import Path

        from curtail_agents.api import VENDOR

        directory = (
            Path(__file__).resolve().parents[1] / "src" / "curtail_agents" / "data" / "vendor"
        )
        present = {p.name for p in directory.iterdir() if p.is_file()}
        assert present == set(VENDOR.values()), (
            f"the vendor directory holds files the allowlist does not name: "
            f"{present - set(VENDOR.values())}. That directory is excluded from the "
            f"em-dash gate, so anything unlisted there is unguarded prose."
        )

    def test_every_allowlisted_bundle_is_third_party_and_carries_its_licence(self) -> None:
        """A file is only exempt from our prose rules because it is somebody else's
        work. This asserts that is still true of each one."""
        from pathlib import Path

        from curtail_agents.api import VENDOR

        directory = (
            Path(__file__).resolve().parents[1] / "src" / "curtail_agents" / "data" / "vendor"
        )
        for filename in VENDOR.values():
            head = (directory / filename).read_bytes()[:4000]
            assert b"Licensed to the Apache Software Foundation" in head or b"License" in head, (
                f"{filename} carries no upstream licence notice, so it does not look "
                "like a vendored third-party bundle"
            )

    def test_the_exclusion_path_in_ci_matches_the_real_directory(self) -> None:
        """A guard exclusion written against a path that does not exist protects
        nothing and hides nothing, and nobody would notice either way."""
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        workflow = (repo / ".github" / "workflows" / "ci.yml").read_text()
        excluded = "agents/src/curtail_agents/data/vendor/"
        assert excluded in workflow, "the em-dash gate no longer excludes the vendor path"
        assert (repo / excluded).is_dir(), f"{excluded} is excluded in CI but does not exist"
