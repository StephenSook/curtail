"""The rights ledger, and the two rights it nearly failed to mention.

The interesting column is PLACED. 14 of the 85 Shasta rows state no priority precise
enough to establish decree membership, so the ladder cannot place them. Defaulting them
into a tier would be the worst wrong answer available: the first grouping is curtailed
FIRST, so a right dropped there by a missing field is curtailed ahead of rights that are
genuinely junior to it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    from curtail_agents.api import app

    return TestClient(app)


@pytest.fixture(scope="module")
def shasta(client: TestClient) -> dict[str, Any]:
    body = client.get("/api/ledger/shasta").json()
    assert isinstance(body, dict)
    return body


@pytest.fixture(scope="module")
def scott(client: TestClient) -> dict[str, Any]:
    body = client.get("/api/ledger/scott").json()
    assert isinstance(body, dict)
    return body


class TestEveryRightTheOrderNamesIsListed:
    """The bug this class exists for, found by driving the page rather than by any
    assertion: the ledger showed 71 rights for an order naming 85.

    `rights_for` returns the rights the ladder can PLACE and names the rest
    separately, so rendering only the first list dropped 14. That is the same failure
    the map's groundwater caveat exists to prevent, one card over.
    """

    def test_shasta_lists_every_right_not_only_the_placeable_ones(
        self, shasta: dict[str, Any]
    ) -> None:
        assert shasta["count"] == 85, (
            f"the order names 85 rights and the ledger lists {shasta['count']}"
        )
        assert shasta["placeable_count"] == 71
        assert shasta["count"] > shasta["placeable_count"], (
            "the unplaceable rights are missing from the rows again"
        )

    def test_the_unplaceable_rights_appear_as_rows(self, shasta: dict[str, Any]) -> None:
        unplaced = [row for row in shasta["rows"] if not row["placed"]]
        assert len(unplaced) == shasta["not_placed_count"] == 14

    def test_each_unplaceable_row_carries_its_own_reason(self, shasta: dict[str, Any]) -> None:
        """A row marked "cannot place" with no reason invites the reader to assume a
        bug rather than a documented limit of the source."""
        for row in shasta["rows"]:
            if not row["placed"]:
                assert row["reason"], f"{row['right_id']} cannot be placed and says nothing"
                assert "priority" in row["reason"]

    def test_a_placed_row_carries_no_reason(self, shasta: dict[str, Any]) -> None:
        """Guards the guard. If every row carried a reason the test above would pass
        against a ledger that had stopped distinguishing anything."""
        assert any(row["placed"] for row in shasta["rows"])
        for row in shasta["rows"]:
            if row["placed"]:
                assert row["reason"] is None

    def test_every_row_id_is_a_right_id_and_not_a_sentence(self, shasta: dict[str, Any]) -> None:
        """`not_placed` holds whole sentences beginning with a right id, and the id has
        to be parsed off the front. A mutation that skipped the parse and keyed by the
        whole sentence still produced 85 rows with 14 unplaced, so every count-based
        assertion in this file passed while the right_id column held a paragraph.

        Checked against the source record, so this cannot be satisfied by any string
        that merely looks id-shaped either.
        """
        import json
        from pathlib import Path as _Path

        raw = json.loads(
            (
                _Path(__file__).resolve().parents[2]
                / "core"
                / "src"
                / "curtail_core"
                / "data"
                / "rights_shasta_addendum6.json"
            ).read_text()
        )
        known = {row["application_number"] for row in raw["rights"]}
        for row in shasta["rows"]:
            assert row["right_id"] in known, (
                f"{row['right_id']!r} is not an application number from the source "
                "record. The id was probably not parsed off its reason sentence."
            )

    def test_the_placed_flag_is_not_computed_by_comparing_an_id_to_a_sentence(
        self, shasta: dict[str, Any]
    ) -> None:
        """The original defect, pinned. `not_placed` holds whole sentences beginning
        with a right id, and the first version tested `right_id not in not_placed`.
        That is True for every row, so the table said all 85 were placed while the
        note beside it said 14 were not: two surfaces contradicting each other on one
        screen.
        """
        assert any(not row["placed"] for row in shasta["rows"]), (
            "no row is marked unplaceable, which is what an id-versus-sentence comparison produces"
        )


class TestScottNeedsNoPlacementAtAll:
    def test_every_scott_right_is_placed(self, scott: dict[str, Any]) -> None:
        """Not an accident of the data. The Board states a curtailment group for every
        Scott right in its own column, so nothing is left to fail placement, and this
        system reads the stated group rather than deriving one. Deriving it matched
        the Board on 8 of 384."""
        assert scott["not_placed_count"] == 0
        assert all(row["placed"] for row in scott["rows"])
        assert scott["count"] == scott["placeable_count"] == 384

    def test_every_scott_row_carries_the_boards_stated_group(self, scott: dict[str, Any]) -> None:
        assert all(row["stated_group"] is not None for row in scott["rows"])


class TestAbsenceIsStatedNotBlank:
    def test_a_missing_priority_date_is_null_with_a_flag_beside_it(
        self, shasta: dict[str, Any]
    ) -> None:
        """Null alone could be rendered as a blank cell, and a blank cell reads as an
        oversight. The flag makes the absence a stated fact, which is what it is: the
        Board's attachment does not carry one."""
        for row in shasta["rows"]:
            if row["priority_date"] is None and row["priority_year_only"] is None:
                assert row["priority_date_missing"] is True

    def test_a_year_only_priority_is_kept_distinct_from_a_full_date(
        self, shasta: dict[str, Any]
    ) -> None:
        """A year cannot settle a boundary that falls mid-year, so it is a third
        state and not a date with unknown precision."""
        years = [row for row in shasta["rows"] if row["priority_year_only"]]
        assert years, "the year-only case has disappeared from the record"
        for row in years:
            assert row["priority_date"] is None


class TestTheOpenQuestionsAreGroupedNotFlattened:
    def test_grouping_loses_no_right(self, shasta: dict[str, Any]) -> None:
        """All 14 entries carry an identical sentence and printing it 14 times is a
        wall a reader skips. Collapsing repetition must not collapse disclosure."""
        grouped = shasta["not_placed_grouped"]
        ids = {right for group in grouped for right in group["rights"]}
        assert len(ids) == shasta["not_placed_count"] == 14
        assert sum(group["count"] for group in grouped) == 14

    def test_each_group_carries_its_reason_and_its_rights(self, shasta: dict[str, Any]) -> None:
        for group in shasta["not_placed_grouped"]:
            assert group["reason"] and group["rights"]
            assert group["count"] == len(group["rights"])

    def test_the_open_questions_are_carried_verbatim(self, shasta: dict[str, Any]) -> None:
        """A reader who sees an empty list assumes there was nothing to say, which is
        a different claim from "we did not look"."""
        assert len(shasta["open_questions"]) >= 5


class TestTheSourceIsNamed:
    def test_the_document_and_its_hash_travel_with_the_rows(self, shasta: dict[str, Any]) -> None:
        assert "Addendum 6" in shasta["document"]
        assert shasta["issued"]
        assert len(shasta["source_sha256"]) == 64

    def test_the_mapped_column_is_none_rather_than_false_when_unknown(
        self, monkeypatch: Any
    ) -> None:
        """`null` is UNKNOWN, not no. Rendering an absent diversion artifact as "no"
        would assert a right has no mapped diversion when the truth is that the page
        could not check."""
        from fastapi import HTTPException

        from curtail_agents import api

        def unavailable() -> dict[str, Any]:
            raise HTTPException(status_code=503, detail="not packaged")

        monkeypatch.setattr(api, "diversions_payload", unavailable)
        with TestClient(api.app) as client:
            body = client.get("/api/ledger/shasta").json()
        assert body["mapped_count"] is None
        assert all(row["mapped"] is None for row in body["rows"])


class TestItRefusesRatherThanGuessing:
    def test_an_unknown_basin_is_404(self, client: TestClient) -> None:
        assert client.get("/api/ledger/sacramento").status_code == 404
