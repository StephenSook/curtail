"""The Scott rights record: reconciled against its source, and free of personal data.

Two properties matter here and neither is about parsing cleverness.

**Reconciliation.** A parser that reads 300 of 384 rows and reports 300 has chosen its
own denominator, which is the defect this project audits for in metrics and audits for
here too. Every application number in the document must appear in a parsed row.

**Privacy.** The attachment's third column is `Primary Owner`: full names of private
ranchers and irrigation districts. A public order being the source does not make
republishing them acceptable, and the Shasta record never read one either.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "data" / "rights_scott_addendum12.json"
SCRIPT = REPO / "scripts" / "extract_scott_attachment_a.py"


@pytest.fixture(scope="module")
def record() -> dict[str, Any]:
    assert RECORD.exists(), f"{RECORD} is missing. Run scripts/extract_scott_attachment_a.py"
    data: dict[str, Any] = json.loads(RECORD.read_text())
    return data


class TestTheRecordReconcilesWithItsSource:
    def test_every_right_is_accounted_for(self, record: dict[str, Any]) -> None:
        counts = record["counts"]
        assert counts["rows_parsed"] == counts["application_numbers_in_document"], (
            "the parse read fewer rights than the document contains, so the record "
            "reports a denominator it chose rather than the one the Board published"
        )
        assert len(record["rights"]) == counts["rows_parsed"]

    def test_no_right_appears_twice(self, record: dict[str, Any]) -> None:
        """One right in two groups is a contradiction the ladder cannot resolve."""
        ids = [r["application_number"] for r in record["rights"]]
        assert len(ids) == len(set(ids))

    def test_every_group_is_one_the_regulation_defines(self, record: dict[str, Any]) -> None:
        """23 CCR 875.5(a)(1)(A) defines nine groupings. A right outside them was misread,
        and one grouping is the difference between a ranch irrigating and shutting off."""
        for right in record["rights"]:
            assert 1 <= right["curtailment_group"] <= 9, right

    def test_the_source_is_named_with_a_hash(self, record: dict[str, Any]) -> None:
        source = record["source"]
        assert source["document"], "a record that does not name its document is not evidence"
        assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"]), source["sha256"]
        assert source["basin"] == "scott"


class TestNoPersonalDataEntersTheRepository:
    #: The only keys a right may carry. An owner column would arrive as a new key long
    #: before anybody noticed a name in the values, so the shape is asserted first.
    ALLOWED: ClassVar[set[str]] = {"application_number", "curtailment_group", "status_as_printed"}

    def test_a_right_carries_only_the_permitted_fields(self, record: dict[str, Any]) -> None:
        for right in record["rights"]:
            assert set(right) == self.ALLOWED, f"unexpected fields: {set(right) - self.ALLOWED}"

    def test_no_owner_name_survives_anywhere_in_the_record(self, record: dict[str, Any]) -> None:
        """The attachment prints owners in capitals, so two adjacent capitalised words is
        the shape a leaked name takes. The prose fields are excluded because they are
        ours and describe the policy rather than quoting the table."""
        blob = json.dumps(record["rights"])
        leaked = re.findall(r"\b[A-Z]{2,}\s+[A-Z]{2,}\b", blob)
        assert not leaked, f"owner names appear to have leaked: {sorted(set(leaked))[:5]}"

    def test_the_record_states_the_privacy_decision(self, record: dict[str, Any]) -> None:
        """An undocumented omission reads as an oversight to the next maintainer, who
        then 'fixes' it by adding the column."""
        assert "not read" in record["privacy"]


class TestTheRecordCannotGoStale:
    def test_regenerating_it_produces_no_diff(self) -> None:
        """The corpus is fetched rather than vendored, so this skips when the source PDF
        is absent rather than failing for a reason unrelated to the record."""
        source = REPO / "data" / "corpus" / "scott_2024__addenda__12.pdf"
        if not source.exists():
            pytest.skip("the source PDF is not in this checkout, so a fresh parse cannot run")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert result.returncode == 0, result.stdout + result.stderr
