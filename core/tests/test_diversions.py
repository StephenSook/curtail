"""The diversion coordinates, and the three things that went wrong getting them.

Offline, entirely. `scripts/build_diversions.py` needs the network and writes a committed
artifact; this asserts the artifact holds together. Same split as the deployment probe and
the chaos recorder, for the same reason: CI must not depend on a state agency's uptime.

The invariants here are not decoration. Each one corresponds to a defect that was actually
present at some point during the build.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "core" / "src" / "curtail_core" / "data"


@pytest.fixture(scope="module")
def diversions() -> dict[str, Any]:
    loaded = json.loads((DATA / "diversions.json").read_text())
    assert isinstance(loaded, dict)
    return loaded


def rights(basin: str) -> dict[str, Any]:
    name = "rights_scott_addendum12" if basin == "scott" else "rights_shasta_addendum6"
    return {
        r["application_number"]: r
        for r in json.loads((DATA / f"{name}.json").read_text())["rights"]
    }


class TestARightHasManyDiversionPoints:
    """The bug that produced a Scott right plotted near Sacramento.

    The first version kept a dict keyed by right, so every point but the last was
    silently discarded. `S010583` has two: one in Scott, Siskiyou County, and one in
    Lower Sacramento about 200 miles south. The dict kept Sacramento, and the artifact
    reported a curtailed Scott right diverting there, which read as an error in the
    Board's data and was an error in this code.

    The loss was invisible because what survived was perfectly well formed. Same shape
    as any one-key-overwrites-another collision.
    """

    def test_every_located_right_carries_a_list_of_points(self, diversions: Any) -> None:
        for basin, section in diversions["basins"].items():
            for entry in section["located"]:
                assert isinstance(entry.get("points"), list) and entry["points"], (
                    f"{basin}: {entry['application_number']} has no points list"
                )

    def test_the_right_with_two_watersheds_keeps_both(self, diversions: Any) -> None:
        """The specific record that exposed the collision. Named, because a general
        invariant would pass against a build that happened to drop the second point."""
        entry = next(
            e
            for e in diversions["basins"]["scott"]["located"]
            if e["application_number"] == "S010583"
        )
        watersheds = {p["huc8_name"] for p in entry["points"]}
        assert watersheds == {"Scott", "Lower Sacramento"}, (
            f"S010583 diverts in two watersheds and the artifact holds {watersheds}"
        )

    def test_more_points_exist_than_rights(self, diversions: Any) -> None:
        """Guards the guard. If the collision returned, points would equal rights
        exactly and every test above would still pass."""
        for basin, section in diversions["basins"].items():
            points = sum(len(e["points"]) for e in section["located"])
            assert points > len(section["located"]), (
                f"{basin} has exactly one point per right, which means the "
                "one-key-overwrites-another collision is back"
            )


class TestTheCountsReconcile:
    def test_located_plus_missing_equals_the_rights_table(self, diversions: Any) -> None:
        for basin, section in diversions["basins"].items():
            table = rights(basin)
            assert section["rights_total"] == len(table)
            assert len(section["located"]) + section["without_coordinate"] == len(table), (
                f"{basin} does not reconcile against its own rights table"
            )

    def test_every_right_without_a_coordinate_is_groundwater(self, diversions: Any) -> None:
        """The Board's own dataset says it excludes wells, so a miss should always be
        groundwater. A miss that is NOT groundwater is a join failure, which is a
        different problem with a different fix, and conflating them would let a broken
        join hide behind a documented limitation.

        This caught two real cases: one right the Board codes in a neighbouring
        watershed, and one whose second diversion point was being discarded.
        """
        for basin, section in diversions["basins"].items():
            assert section["without_coordinate"] == section["without_coordinate_groundwater"], (
                f"{basin}: {section['without_coordinate']} rights lack a coordinate but "
                f"only {section['without_coordinate_groundwater']} are groundwater"
            )

    def test_every_mapped_right_is_a_curtailed_right(self, diversions: Any) -> None:
        for basin, section in diversions["basins"].items():
            table = rights(basin)
            for entry in section["located"]:
                assert entry["application_number"] in table, (
                    f"{basin}: {entry['application_number']} is mapped and is not curtailed"
                )


class TestCoordinatesAreWGS84AndInCalifornia:
    def test_no_point_is_outside_california(self, diversions: Any) -> None:
        """The bulk GeoJSON download from the same publisher is EPSG:3310 and its
        coordinates are California Albers METRES, like [135564.58, -258250.80]. Fed to
        a map that puts every point off the planet, and the numbers look plausible
        until you check the range. `outSR=4326` is requested explicitly and this is
        what proves it took.
        """
        for basin, section in diversions["basins"].items():
            for entry in section["located"]:
                for point in entry["points"]:
                    assert -125.0 <= point["lon"] <= -113.5, f"{basin} {entry} longitude"
                    assert 32.0 <= point["lat"] <= 42.5, f"{basin} {entry} latitude"

    def test_no_coordinate_is_a_string_or_a_nan(self, diversions: Any) -> None:
        """The Board's master list carries the literal string "NaN" for all 8,000
        groundwater rows, and `"NaN"` is a perfectly non-empty string. An emptiness
        check reported those as having coordinates during research."""
        import math

        for section in diversions["basins"].values():
            for entry in section["located"]:
                for point in entry["points"]:
                    for axis in ("lon", "lat"):
                        assert isinstance(point[axis], float), f"{point[axis]!r} is not a float"
                        assert math.isfinite(point[axis])


class TestNoPersonalDataIsCarried:
    """The rights tables already record this decision verbatim: the Primary Owner
    column is deliberately not read, and a public source does not change that. The
    CalWATRS response carries `primary_owner` on every feature."""

    def test_no_entry_carries_an_owner_or_contact_field(self, diversions: Any) -> None:
        forbidden = {"primary_owner", "owner", "name", "contact", "address", "phone", "email"}
        for basin, section in diversions["basins"].items():
            for entry in section["located"]:
                assert not (forbidden & set(entry)), (
                    f"{basin}: {entry['application_number']} carries personal fields"
                )
                for point in entry["points"]:
                    assert not (forbidden & set(point))

    def test_the_generator_does_not_request_the_owner_field(self) -> None:
        """Asserted on the request, not just the result. Not writing a field you
        fetched is one mistake away from writing it; not fetching it is not."""
        source = (REPO / "scripts" / "build_diversions.py").read_text()
        fields = next(line for line in source.splitlines() if line.startswith("FIELDS = "))
        assert "owner" not in fields, f"the generator requests an owner field: {fields}"


class TestTheDisagreementIsSurfacedNotResolved:
    """One right is listed by the Board in one basin and placed by the Board in
    another. Both statements are the Board's. Reporting the disagreement is the
    product's entire thesis; resolving it is not this system's call."""

    def test_the_known_discrepancy_is_recorded(self, diversions: Any) -> None:
        scott = diversions["basins"]["scott"]
        assert "S027847" in scott["watershed_discrepancies"], (
            "the cross-dataset disagreement is no longer reported, so either the "
            "Board fixed it or the artifact stopped looking"
        )

    def test_a_discrepancy_names_both_sides(self, diversions: Any) -> None:
        entry = next(
            e
            for e in diversions["basins"]["scott"]["located"]
            if e["application_number"] == "S027847"
        )
        gap = entry["watershed_discrepancy"]
        assert gap["order_says"] == "Scott"
        assert gap["diversion_layer_says"], "the other side of the disagreement is unnamed"

    def test_a_right_diverting_in_two_watersheds_is_not_a_discrepancy(
        self, diversions: Any
    ) -> None:
        """Ordinary in California water law, and the distinction matters: a
        discrepancy is 'NO point sits where the order says', not 'some point sits
        elsewhere'. Conflating them would flag S010583, which is simply a right with
        two diversion points."""
        assert "S010583" not in diversions["basins"]["scott"]["watershed_discrepancies"]


class TestTheSourceIsAttributed:
    def test_the_licence_and_attribution_are_recorded(self, diversions: Any) -> None:
        """Public domain data still gets credited, and a repo redistributing somebody
        else's data should say whose it is in the file itself."""
        source = diversions["source"]
        assert "Water Resources Control Board" in source["attribution"]
        assert "domain" in source["licence"].lower()
        assert source["projection"].startswith("EPSG:4326")
