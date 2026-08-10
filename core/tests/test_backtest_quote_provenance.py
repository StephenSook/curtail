"""Every backtest quote must be traceable to the document it names.

`data/corpus/` is gitignored, because a repository is not a document archive, so
CI cannot open the PDFs. A test that skipped when the corpus was absent would be
a false green, and this project has already been bitten by exactly that.

So the check is split, the same way `docs/FACTS.md` is. A local script greps each
quote against real bytes and writes `data/quote_verification.json` with a SHA256
per document. This file checks that record is complete, internally consistent and
covers every case. Neither half suffices alone: the record proves someone ran the
check against real documents, and these tests prove nobody quietly dropped a case
from it.

The finding this closes was raised as UNCERTAIN by an adversarial review and
turned out to be real. On its FIRST run the script failed, on the very case
created to correct the retired 39.3 figure: the quote there was a sentence I had
composed containing correct numbers, not a sentence the Board wrote, and the two
dates in it were swapped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "data" / "backtest_cases.json"
RECORD = ROOT / "data" / "quote_verification.json"


@pytest.fixture(scope="module")
def cases() -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = json.loads(CASES.read_text())["cases"]
    return payload


@pytest.fixture(scope="module")
def record() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(RECORD.read_text())
    return payload


class TestTheVerificationRecordExistsAndIsComplete:
    def test_the_record_is_committed(self) -> None:
        assert RECORD.exists(), (
            "run scripts/verify_backtest_quotes.py; without its output nothing "
            "ties a scored case to the document it claims to quote"
        )

    def test_every_case_appears_in_the_record(
        self, cases: list[dict[str, Any]], record: dict[str, Any]
    ) -> None:
        """A case absent from the record is a case nobody checked.

        This is the assertion that makes dropping a case impossible rather than
        merely discouraged.
        """
        missing = {c["id"] for c in cases} - set(record["cases"])
        assert not missing, f"cases never checked against their documents: {sorted(missing)}"

    def test_the_record_names_no_case_that_does_not_exist(
        self, cases: list[dict[str, Any]], record: dict[str, Any]
    ) -> None:
        """A stale entry would let a removed case keep vouching for the total."""
        extra = set(record["cases"]) - {c["id"] for c in cases}
        assert not extra, f"record vouches for cases that no longer exist: {sorted(extra)}"

    def test_no_quote_failed_verification(self, record: dict[str, Any]) -> None:
        assert record["failures"] == [], record["failures"]

    def test_the_summary_agrees_with_its_own_entries(self, record: dict[str, Any]) -> None:
        """A summary that drifts from its entries is how a failure gets buried."""
        entries = record["cases"].values()
        assert record["summary"]["total"] == len(record["cases"])
        assert record["summary"]["verified"] == sum(1 for e in entries if e.get("verified"))
        assert record["summary"]["failed"] == len(record["failures"])


class TestATextLayerCaseIsMachineVerified:
    def test_every_text_layer_case_was_found_in_its_document(
        self, cases: list[dict[str, Any]], record: dict[str, Any]
    ) -> None:
        for case in cases:
            if case.get("read_method") != "text_layer":
                continue
            entry = record["cases"][case["id"]]
            assert entry["verified"] is True, (
                f"{case['id']}: the quote was not found in the document it names"
            )

    def test_every_verified_case_records_the_bytes_it_checked(self, record: dict[str, Any]) -> None:
        """A verification with no document hash cannot be repeated or trusted."""
        for case_id, entry in record["cases"].items():
            if entry.get("verified"):
                assert len(entry.get("sha256", "")) == 64, case_id

    def test_the_reading_appears_inside_its_own_quote(
        self, cases: list[dict[str, Any]], record: dict[str, Any]
    ) -> None:
        """Belt and braces with the case-file test.

        A quote can be verbatim and still be the wrong sentence. Requiring the
        scored number to appear inside it closes that gap.
        """
        for case in cases:
            if case.get("read_method") != "text_layer":
                continue
            assert record["cases"][case["id"]]["reading_in_quote"] is True, case["id"]


class TestAVisionCaseIsRecordedAsUnverifiableNotVerified:
    """A scan has no text layer to grep, and saying otherwise would be a lie.

    Two cases were read from rendered pages. Recording them as "verified" would
    claim a machine check that never happened, so they are recorded as
    unverifiable WITH the document hash, and their transcription notes in the
    corpus manifest carry the quoted sentences.
    """

    def test_a_vision_case_is_never_marked_verified(
        self, cases: list[dict[str, Any]], record: dict[str, Any]
    ) -> None:
        for case in cases:
            if case.get("read_method") != "vision":
                continue
            entry = record["cases"][case["id"]]
            assert entry["verified"] is False
            assert "no text layer" in entry["reason"]

    def test_a_vision_case_still_records_the_document_hash(
        self, cases: list[dict[str, Any]], record: dict[str, Any]
    ) -> None:
        for case in cases:
            if case.get("read_method") == "vision":
                assert len(record["cases"][case["id"]].get("sha256", "")) == 64

    def test_the_unverifiable_count_is_not_hidden(self, record: dict[str, Any]) -> None:
        """It appears in the summary, so a reader sees that 2 of 6 rest on a
        human transcription rather than a grep."""
        assert record["summary"]["vision_unverifiable"] >= 1
