"""The corpus manifest is the denominator of the headline metric, so it is tested.

The claim this project makes is "Curtail independently reproduces N of M
historical curtailment actions." M comes from this file. A manifest whose counts
drift from its own contents, or that lets an unread document be scored, corrupts
the metric in a way no amount of engine correctness can fix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

MANIFEST = Path(__file__).resolve().parents[2] / "data" / "corpus_manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(MANIFEST.read_text())
    return data


class TestManifestIntegrity:
    def test_it_exists(self) -> None:
        assert MANIFEST.exists()

    def test_every_series_names_its_basin_and_era(self, manifest: dict[str, Any]) -> None:
        for series in manifest["series"]:
            assert series["basin"] in {"scott", "shasta"}
            assert series["era"]

    def test_declared_base_order_total_matches_the_series(self, manifest: dict[str, Any]) -> None:
        """A headline denominator that disagrees with its own contents is worthless."""
        counted = sum(len(s["base_orders"]) for s in manifest["series"])
        assert counted == manifest["totals"]["base_orders"], (
            f"manifest declares {manifest['totals']['base_orders']} base orders but lists {counted}"
        )

    def test_declared_addenda_total_matches_the_series(self, manifest: dict[str, Any]) -> None:
        counted = sum(s["addenda_count"] for s in manifest["series"])
        assert counted == manifest["totals"]["addenda"], (
            f"manifest declares {manifest['totals']['addenda']} addenda but lists {counted}"
        )

    def test_enumerated_addenda_match_their_declared_count(self, manifest: dict[str, Any]) -> None:
        """Where addenda are listed individually, the list must match the count."""
        for series in manifest["series"]:
            if "addenda" not in series:
                continue
            enumerated = len(series["addenda"]) + len(series.get("addenda_to_2026_0008", []))
            assert enumerated == series["addenda_count"], (
                f"{series['id']}: declares {series['addenda_count']} addenda, lists {enumerated}"
            )

    def test_addendum_numbers_are_contiguous_from_one(self, manifest: dict[str, Any]) -> None:
        """A gap means a document was missed, which silently shrinks the denominator."""
        for series in manifest["series"]:
            if "addenda" not in series:
                continue
            numbers = sorted(a["n"] for a in series["addenda"])
            assert numbers == list(range(1, len(numbers) + 1)), (
                f"{series['id']}: addendum numbering has a gap: {numbers}"
            )


class TestCorpusScale:
    """The corpus was undercounted by roughly half in early research.

    Guarding the corrected figure so it cannot silently regress.
    """

    def test_at_least_fourteen_base_orders(self, manifest: dict[str, Any]) -> None:
        assert manifest["totals"]["base_orders"] >= 14

    def test_at_least_eighty_six_addenda(self, manifest: dict[str, Any]) -> None:
        assert manifest["totals"]["addenda"] >= 86

    def test_both_basins_and_both_regulatory_eras_are_represented(
        self, manifest: dict[str, Any]
    ) -> None:
        """A backtest on one era would apply the wrong flow schedule to the other."""
        ids = {s["id"] for s in manifest["series"]}
        assert ids == {"scott_2021", "scott_2024", "shasta_2021", "shasta_2024"}


class TestNothingIsScoredBeforeItIsRead:
    """The two-tier honesty rule.

    `index_verified` means the Board's index page listed it. `document_read`
    means the PDF was parsed. A record can be the first without the second, and
    an expected action taken from anywhere other than the document itself is
    exactly the drift this project exists to prevent.
    """

    def test_every_base_order_declares_both_tiers(self, manifest: dict[str, Any]) -> None:
        for series in manifest["series"]:
            for order in series["base_orders"]:
                assert "index_verified" in order, order
                assert "document_read" in order, order

    def test_no_order_claims_to_be_read_without_being_index_verified(
        self, manifest: dict[str, Any]
    ) -> None:
        """Reading a document you never located is not a state that can exist."""
        for series in manifest["series"]:
            for order in series["base_orders"]:
                if order["document_read"]:
                    assert order["index_verified"], order

    def test_open_items_are_recorded_while_documents_remain_unread(
        self, manifest: dict[str, Any]
    ) -> None:
        unread = sum(
            1 for s in manifest["series"] for o in s["base_orders"] if not o["document_read"]
        )
        if unread:
            assert manifest["open_items"], f"{unread} unread documents but no open items recorded"


class TestKnownConflictsAreRecordedNotResolved:
    """Three real inconsistencies, two of them on the Board's own pages.

    Recording a conflict is the honest state. Silently picking a side would put
    a guessed identifier into a legal record.
    """

    def test_the_shasta_order_number_year_conflict_is_recorded(
        self, manifest: dict[str, Any]
    ) -> None:
        shasta = next(s for s in manifest["series"] if s["id"] == "shasta_2021")
        note = shasta["data_quality_note"]
        assert "2022-0162" in note or "2021-0162" in note
        assert "index page is internally inconsistent" in note

    def test_the_unnumbered_scott_order_is_flagged(self, manifest: dict[str, Any]) -> None:
        scott = next(s for s in manifest["series"] if s["id"] == "scott_2021")
        unnumbered = [o for o in scott["base_orders"] if o["order_number"] == "UNNUMBERED_ON_INDEX"]
        assert len(unnumbered) == 1
        assert "data_quality_note" in unnumbered[0]

    def test_the_1912_cutoff_conflict_is_carried_as_open(self, manifest: dict[str, Any]) -> None:
        assert any("November 1 versus November 25" in item for item in manifest["open_items"])

    def test_sources_are_cited(self, manifest: dict[str, Any]) -> None:
        assert len(manifest["verified_against"]) == 4
        assert all(
            u.startswith("https://www.waterboards.ca.gov") for u in manifest["verified_against"]
        )
