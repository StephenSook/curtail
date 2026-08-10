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

    def test_at_least_eighty_four_addenda(self, manifest: dict[str, Any]) -> None:
        """84, corrected DOWN from 86 on 2026-08-10, and the direction matters.

        Early research estimated the Shasta 2021 series at 16 addenda. The
        Board's own shasta_addendums.html links addenda numbered to 14 and no
        further, confirmed two ways: parsing that page, and a separate archive
        sweep that found 14 with no gaps. The 16 was never verified against a
        Board page.

        A count that moves down is the case worth guarding hardest, because the
        instinct is to treat a smaller denominator as a loss and quietly keep the
        larger one. The larger one was wrong.
        """
        assert manifest["totals"]["addenda"] >= 84

    def test_the_shasta_2021_correction_is_recorded_not_silent(
        self, manifest: dict[str, Any]
    ) -> None:
        shasta = next(s for s in manifest["series"] if s["id"] == "shasta_2021")
        assert shasta["addenda_count"] == 14
        assert "count_correction_note" in shasta

    def test_the_declared_total_is_computed_from_the_series(self, manifest: dict[str, Any]) -> None:
        """The headline document count must follow its own parts.

        A total that stops tracking the series it sums is how a corrected count
        gets reported as the old one.
        """
        totals = manifest["totals"]
        expected = (
            totals["base_orders"]
            + totals["addenda"]
            + totals["temporary_amendments"]
            + totals["supporting_determinations"]
        )
        assert totals["documents_total"] == expected
        assert manifest["extraction_status"]["documents_total_declared"] == expected

    def test_the_2021_addenda_are_now_individually_enumerated(
        self, manifest: dict[str, Any]
    ) -> None:
        """They were declared by count for weeks, on a premise that was wrong.

        The Board does publish per-addendum links for the 2021 series, on two
        index pages the main drought index does not surface prominently.
        """
        for sid, expected in (("scott_2021", 51), ("shasta_2021", 14)):
            series = next(s for s in manifest["series"] if s["id"] == sid)
            assert len(series["addenda"]) == expected, sid
            assert series.get("index_page"), f"{sid} has no index page recorded"

    def test_most_enumerated_2021_addenda_carry_a_url(self, manifest: dict[str, Any]) -> None:
        """Not all of them, and the exceptions are honest.

        The earliest addenda in both series use descriptive filenames rather
        than a number, so the number cannot be mapped from the URL and must be
        confirmed by reading the document. Those records carry a note instead of
        a guessed URL.
        """
        for sid in ("scott_2021", "shasta_2021"):
            series = next(s for s in manifest["series"] if s["id"] == sid)
            with_url = [a for a in series["addenda"] if a.get("url")]
            assert len(with_url) >= len(series["addenda"]) - 4, sid
            for record in series["addenda"]:
                assert record.get("url") or record.get("data_quality_note"), (
                    f"{sid} addendum {record['n']} has neither a URL nor a reason"
                )

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
        assert "2022-0162" in note
        assert "index page is internally inconsistent" in note
        # Resolved 2026-08-10, and the resolution must cite the documents rather
        # than simply asserting a winner. Each order prints its own number on its
        # own face; the index page's 2021 reference points at the parent order
        # both of them amend.
        assert "RESOLVED" in note
        assert "August 2, 2022" in note

    def test_the_unnumbered_scott_order_is_flagged(self, manifest: dict[str, Any]) -> None:
        scott = next(s for s in manifest["series"] if s["id"] == "scott_2021")
        unnumbered = [o for o in scott["base_orders"] if o["order_number"] == "UNNUMBERED_ON_INDEX"]
        assert len(unnumbered) == 1
        note = unnumbered[0]["data_quality_note"]
        # Resolved by reading the document: it carries no WR identifier anywhere,
        # so this was never an index-page omission. The placeholder key stays
        # because the order genuinely has no number, and inventing one would put
        # a fabricated identifier into a legal record.
        assert "RESOLVED" in note
        assert "no WR identifier" in note
        assert unnumbered[0]["signatory_role"] == "Deputy Director"

    def test_the_1912_cutoff_conflict_is_resolved_from_the_document(
        self, manifest: dict[str, Any]
    ) -> None:
        """Resolved 2026-08-10 by reading Addendum 6, which states it twice in bold.

        The cutoff is November 25, 1912. Several research hauls carried November 1.
        A resolution note stays in open_items so the provenance of the answer is
        visible rather than the conflict simply vanishing.
        """
        assert any(
            "November 25, 1912" in item and "RESOLVED" in item for item in manifest["open_items"]
        )

    def test_addendum_6_figures_came_from_the_document(self, manifest: dict[str, Any]) -> None:
        """The one record marked document_read must carry its read values.

        A 39.3 cfs figure propagated from research into four artifacts including
        a public README and appears NOWHERE in the Addendum. The document records
        45.3 and 46.5 cfs.
        """
        shasta = next(s for s in manifest["series"] if s["id"] == "shasta_2024")
        add6 = next(a for a in shasta["addenda"] if a["n"] == 6)
        assert add6["document_read"] is True
        assert add6["priority_cutoff"] == "1912-11-25"
        assert 46.5 in add6["gage_readings_cfs"]
        assert 45.3 in add6["gage_readings_cfs"]
        assert 39.3 not in add6["gage_readings_cfs"]

    def test_sources_are_cited(self, manifest: dict[str, Any]) -> None:
        """Six now: the four original index pages plus the two 2021 addendum
        pages found on 2026-08-10, which is where the per-addendum URLs live."""
        assert len(manifest["verified_against"]) == 6
        assert all(
            u.startswith("https://www.waterboards.ca.gov") for u in manifest["verified_against"]
        )


class TestTheMetricDenominatorCannotBeInflated:
    """The headline claim is "Curtail reproduces N of M historical actions".

    M is the scorable count, not the declared total. 102 documents are known to
    exist; far fewer have been read. Every guard here exists to stop the gap
    between those two numbers from quietly closing in the flattering direction.
    """

    def test_extraction_status_is_recorded(self, manifest: dict[str, Any]) -> None:
        assert "extraction_status" in manifest

    def test_scorable_never_exceeds_documents_actually_read(self, manifest: dict[str, Any]) -> None:
        """Read by either path, but read by some path.

        A document is scorable when the deterministic parser returned an action
        from its text layer, or when a scan was read from its rendered pages and
        the figures transcribed with the source sentence quoted. Nothing else
        counts, and the sum of the two paths is a hard ceiling on the metric.
        """
        status = manifest["extraction_status"]
        actually_read = status["read_via_text_layer"] + status.get("read_via_vision", 0)
        assert status["scorable"] <= actually_read, (
            f"{status['scorable']} scorable but only {actually_read} documents were read"
        )

    def test_read_never_exceeds_pdfs_fetched(self, manifest: dict[str, Any]) -> None:
        status = manifest["extraction_status"]
        actually_read = status["read_via_text_layer"] + status.get("read_via_vision", 0)
        assert actually_read <= status["pdfs_fetched"]

    def test_a_scan_is_either_read_by_vision_or_still_refused(
        self, manifest: dict[str, Any]
    ) -> None:
        """The four scans must be fully accounted for, with none silently lost.

        Two were read from their rendered pages. Two remain unread. A scan that
        stopped being counted in either bucket would vanish from the record
        while the totals still looked consistent.
        """
        status = manifest["extraction_status"]
        scans = status.get("read_via_vision", 0) + status["refused_no_text_layer"]
        assert scans == 4, f"4 scans were measured on disk but {scans} are accounted for"

    def test_fetched_never_exceeds_enumerated_records(self, manifest: dict[str, Any]) -> None:
        status = manifest["extraction_status"]
        assert status["pdfs_fetched"] <= status["records_individually_enumerated"]

    def test_enumerated_records_never_exceed_the_declared_total(
        self, manifest: dict[str, Any]
    ) -> None:
        status = manifest["extraction_status"]
        assert status["records_individually_enumerated"] <= status["documents_total_declared"]

    def test_the_scorable_count_matches_the_records_flagged_read(
        self, manifest: dict[str, Any]
    ) -> None:
        """The summary and the records must agree.

        A summary that drifted above its own records is precisely how a metric
        starts describing documents nothing opened.
        """
        counted = 0
        for series in manifest["series"]:
            counted += sum(1 for o in series["base_orders"] if o.get("document_read"))
            for group in ("addenda", "addenda_to_2026_0008"):
                counted += sum(1 for a in series.get(group, []) if a.get("document_read"))
        assert counted == manifest["extraction_status"]["scorable"], (
            f"extraction_status claims {manifest['extraction_status']['scorable']} scorable "
            f"but {counted} records carry document_read"
        )

    def test_every_record_flagged_read_carries_what_it_read(self, manifest: dict[str, Any]) -> None:
        """`document_read: true` without recorded values is an empty claim.

        Two provenances are permitted and they carry different evidence:

        `text_layer`  the deterministic parser returned an action. The extracted
                      block is the evidence, and it is reproducible by rerunning
                      the script.
        `vision`      the document is a scan with no text layer, so the pages
                      were read and the figures transcribed. That is not
                      reproducible by rerunning a script, so it must carry a
                      transcription note quoting the sentences it took the
                      figures from. A vision read without its quotation is
                      indistinguishable from a value someone remembered.
        """
        for series in manifest["series"]:
            groups: list[dict[str, Any]] = list(series["base_orders"])
            for group in ("addenda", "addenda_to_2026_0008"):
                groups.extend(series.get(group, []))
            for node in groups:
                if not node.get("document_read"):
                    continue
                if node.get("read_method") == "vision":
                    assert node.get("transcription_note"), (
                        f"{node.get('n', node.get('order_number'))} claims a vision read "
                        "but quotes nothing from the document"
                    )
                    assert node.get("action"), "a vision read must record the action it read"
                    continue
                assert "extracted" in node, f"{node} claims to be read but recorded nothing"
                assert node["extracted"]["method"] == "text_layer"
                assert node["extracted"]["action"] != "undetermined"

    def test_a_vision_read_quotes_the_sentence_its_figures_came_from(
        self, manifest: dict[str, Any]
    ) -> None:
        """The July 2025 figures are the ones an earlier draft got wrong.

        Drafts carried "above 75 cfs", taken from the August 5 2025 Executive
        Director's Report paraphrase rather than from the Addendum. The Addendum
        states 78.4. The quotation requirement is what makes that checkable
        without reopening the PDF.
        """
        scott = next(s for s in manifest["series"] if s["id"] == "scott_2024")
        add8 = next(a for a in scott["addenda"] if a["n"] == 8)
        assert add8["gage_reading_cfs"] == 78.4
        assert "78.4 cfs" in add8["transcription_note"]

        add7 = next(a for a in scott["addenda"] if a["n"] == 7)
        assert add7["gage_reading_cfs"] == 48.7
        assert "48.7 cfs" in add7["transcription_note"]

    def test_documents_with_no_text_layer_are_never_marked_read_without_vision(
        self, manifest: dict[str, Any]
    ) -> None:
        """A scan is read by looking at it or it is not read at all.

        A scan marked read with neither a text layer nor a vision transcription
        would be a document scored against an action nothing extracted, which is
        the failure this two-tier scheme exists to prevent.
        """
        blocked = 0
        for series in manifest["series"]:
            nodes: list[dict[str, Any]] = list(series["base_orders"])
            for group in ("addenda", "addenda_to_2026_0008"):
                nodes.extend(series.get(group, []))
            for node in nodes:
                if "extraction_blocked" not in node:
                    continue
                blocked += 1
                assert node["document_read"] is False
        assert blocked >= manifest["extraction_status"]["refused_no_text_layer"]

    def test_the_unenumerated_2021_addenda_are_recorded_as_open(
        self, manifest: dict[str, Any]
    ) -> None:
        """67 addenda exist as a count and not as records.

        They are excluded from the denominator rather than counted as failures,
        and that exclusion has to be stated somewhere a reader will find it.
        """
        joined = " ".join(manifest["open_items"])
        assert "not individually enumerated" in joined
