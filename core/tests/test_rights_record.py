"""The committed rights record: complete, reconciled, and free of owner names.

The corpus PDFs are fetched rather than vendored, so CI cannot re-parse the source.
This is the split this repository already uses for quote provenance: a local script
writes the record with a content hash of its source, and CI asserts the record is
internally consistent, names everything the parse could not read, and carries no field
it should not. Neither half is sufficient alone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "data" / "rights_shasta_addendum6.json"

#: Exactly the fields the priority ladder consumes. Anything else is either useless or
#: is the owner column arriving by another name.
ALLOWED_ROW_KEYS = {
    "application_number",
    "source_as_printed",
    "priority_date",
    "priority_date_missing",
    "priority_year_only",
    "band",
    "page",
}


@pytest.fixture(scope="module")
def record() -> dict[str, Any]:
    assert RECORD.exists(), (
        f"{RECORD} is missing. Run scripts/extract_attachment_a.py against the fetched corpus."
    )
    loaded: dict[str, Any] = json.loads(RECORD.read_text())
    return loaded


class TestTheRecordAccountsForEveryRow:
    def test_the_counts_reconcile(self, record: dict[str, Any]) -> None:
        """Parsed plus imprecise plus unparsed equals every application number seen.

        This is the whole claim. A parser that reads most of a table and says nothing
        about the rest produces the same file as one that read all of it.
        """
        a = record["accounting"]
        assert a["parsed"] + a["imprecise"] + a["unparsed"] == a["application_numbers_seen"]

    def test_it_is_not_empty(self, record: dict[str, Any]) -> None:
        """Non-vacuity. Every consistency check above is satisfied by a record of
        nothing, and an absent extractor produces exactly that."""
        assert record["accounting"]["parsed"] >= 50
        assert len(record["rights"]) == record["accounting"]["parsed"]

    def test_everything_unread_is_named(self, record: dict[str, Any]) -> None:
        """A count of failures with no identities attached cannot be followed up."""
        unread = record["not_read"]
        assert len(unread["imprecise"]) == record["accounting"]["imprecise"]
        assert len(unread["unparsed"]) == record["accounting"]["unparsed"]
        for note in unread["imprecise"]:
            assert re.search(r"\b(?:SG|A|D|C)\d{4,7}\b", note), note

    def test_the_colour_only_encoding_is_recorded(self, record: dict[str, Any]) -> None:
        """Addendum 6 puts the additional-conditions subset in ORANGE. A curtailment
        engine cannot read that from text, and the record has to say so rather than
        present its bands as the whole disposition."""
        assert any("orange" in note for note in record["not_read"]["unrecoverable"])

    def test_it_names_its_source_and_hashes_it(self, record: dict[str, Any]) -> None:
        source = record["source"]
        assert source["file"].endswith(".pdf")
        assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
        assert source["issued"] == "2026-06-16"


class TestTheRecordCarriesNoOwnerIdentity:
    """The table's first column is real private parties, several of them named in live
    reconsideration proceedings. The document is public; republishing a structured,
    queryable table of those names in a public repository is a different act."""

    def test_rows_carry_only_the_fields_the_ladder_consumes(self, record: dict[str, Any]) -> None:
        for row in record["rights"]:
            extra = set(row) - ALLOWED_ROW_KEYS
            assert not extra, f"{row['application_number']} carries unexpected fields: {extra}"

    def test_no_row_value_looks_like_a_person(self, record: dict[str, Any]) -> None:
        """A crude shape check, deliberately.

        It cannot prove the absence of a name, and it is not the control that
        guarantees it: the parser never reads the owner column at all. This is the
        second layer, and it would catch a future change that started reading it.
        """
        for row in record["rights"]:
            source = row["source_as_printed"]
            assert not re.search(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", source), (
                f"{row['application_number']} has a title-case multiword source "
                f"{source!r}, which is the shape of a person's name"
            )
            for marker in (" TRUST", " LLC", " INC", " FARMS", " RANCH"):
                assert marker not in source.upper(), (
                    f"{row['application_number']} source {source!r} looks like an entity "
                    "name rather than a water source"
                )
