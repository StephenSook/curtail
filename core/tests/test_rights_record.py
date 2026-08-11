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
        """Every bucket adds up to the number of application numbers actually seen.

        This is the whole claim. A parser that reads most of a table and says nothing
        about the rest produces the same file as one that read all of it.

        **The first version of this could not fail.** `application_numbers_seen` was
        computed as `parsed + imprecise + unparsed`, so the assertion compared a sum to
        itself, and it hid a real gap: `ambiguous` reached no bucket at all, so a row
        refused because two adjacent lines both offered it a priority would have
        vanished from the accounting without changing a single number. It read as clean
        only because no row in the document was ambiguous yet. The total is now measured
        in the parser as each application number is encountered, before any outcome is
        decided.
        """
        a = record["accounting"]
        buckets = a["parsed"] + a["imprecise"] + a["ambiguous"] + a["unparsed"]
        assert buckets == a["application_numbers_seen"], (
            f"{a['application_numbers_seen']} application numbers were seen but "
            f"{buckets} reached a bucket, so rows are vanishing between the page and "
            "the record"
        )

    def test_every_refusal_category_is_present_in_the_record(self, record: dict[str, Any]) -> None:
        """A category absent from the record cannot be reconciled against, however
        carefully the parser fills it. `ambiguous` was missing exactly this way."""
        for category in ("imprecise", "ambiguous", "unparsed", "blank_source"):
            assert category in record["not_read"], f"{category} is missing from not_read"
            assert category in record["accounting"] or category == "blank_source"

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

    def test_rows_the_ladder_must_not_place_are_named_and_excluded(
        self, record: dict[str, Any]
    ) -> None:
        """The record must not quietly place a right the document cannot support.

        `adjudication=None` is a positive claim, and the Shasta ladder reads it as
        evidence for Tier A under 875.5(b)(1)(A). Fourteen undated rights were once
        placed in the first grouping curtailed on the strength of a default.
        """
        unplaceable = record["unplaceable"]
        assert unplaceable, "no row was refused placement, which the source cannot support"
        placed = sum(record["ladder_placement_counts"].values())
        assert placed + len(unplaceable) == record["accounting"]["parsed"], (
            "placed rights plus refused rights do not add up to the rows parsed"
        )
        for note in unplaceable:
            assert re.search(r"\b(?:SG|A|D|C)\d{4,7}\b", note), note

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


class TestTheLoaderRefusesAnInconsistentRecord:
    """Every case here was reproduced against the loader before it validated anything.

    A partial rights table is the most dangerous shape this system can load. It does not
    look broken: it produces a normal recommendation over fewer rights than the order
    covers, carrying provenance that describes the whole document.
    """

    @staticmethod
    def _write(tmp_path: Path, mutate: Any) -> Path:
        from curtail_core.rights_record import PACKAGED

        raw = json.loads(PACKAGED.read_text())
        mutate(raw)
        target = tmp_path / "record.json"
        target.write_text(json.dumps(raw))
        return target

    def test_the_real_record_still_loads(self) -> None:
        """Non-vacuity. Every refusal below is satisfied by a loader that refuses
        everything, and that loader would take the whole system down silently."""
        from curtail_core.rights_record import load_rights

        loaded = load_rights()
        assert len(loaded.converted.rights) > 50
        assert loaded.rows_parsed == 85

    @pytest.mark.parametrize(
        ("label", "mutate", "expected"),
        [
            (
                "rows truncated but the accounting left alone",
                lambda raw: raw.__setitem__("rights", raw["rights"][:5]),
                "claims 85 were parsed",
            ),
            (
                "a refusal category emptied",
                lambda raw: raw["not_read"].__setitem__("unparsed", []),
                "unparsed lists 0 entries",
            ),
            (
                "a refusal category deleted outright",
                lambda raw: raw["not_read"].pop("ambiguous"),
                "no 'ambiguous' section",
            ),
            (
                "the issue date removed",
                lambda raw: raw["source"].pop("issued"),
                "could not be read",
            ),
            (
                "the source hash corrupted",
                lambda raw: raw["source"].__setitem__("sha256", "nope"),
                "no valid source sha256",
            ),
            (
                "a duplicated row",
                lambda raw: (
                    raw["rights"].append(raw["rights"][0]),
                    raw["accounting"].__setitem__("parsed", raw["accounting"]["parsed"] + 1),
                ),
                "reached a bucket",
            ),
            (
                "a row with no application number",
                lambda raw: raw["rights"][0].pop("application_number"),
                "no valid application_number",
            ),
            (
                "a row claiming both a date and a missing date",
                lambda raw: raw["rights"][0].update(
                    {"priority_date": "2001-01-01", "priority_date_missing": True}
                ),
                "also marked missing",
            ),
        ],
    )
    def test_it_refuses_rather_than_loading_part_of_a_table(
        self, tmp_path: Path, label: str, mutate: Any, expected: str
    ) -> None:
        from curtail_core.rights_record import RightsRecordUnavailableError, load_rights

        target = self._write(tmp_path, mutate)
        with pytest.raises(RightsRecordUnavailableError, match=re.escape(expected)):
            load_rights(target)

    @pytest.mark.parametrize("section", ["source", "accounting", "not_read", "rights"])
    @pytest.mark.parametrize(("shape", "bad"), [("null", None), ("a string", "x"), ("a list", [1])])
    def test_a_section_of_the_wrong_type_is_refused_not_raised_through(
        self, tmp_path: Path, section: str, shape: str, bad: Any
    ) -> None:
        """`raw["source"]` being JSON null made `.get(...)` raise AttributeError, which
        the caller did not translate, so a corrupt file surfaced as a 500 rather than a
        refusal. The escape was found by a review after the validation itself landed:
        the first fix validated CONTENTS and assumed the containers."""
        from curtail_core.rights_record import RightsRecordUnavailableError, load_rights

        target = self._write(tmp_path, lambda raw: raw.__setitem__(section, bad))
        with pytest.raises(RightsRecordUnavailableError):
            load_rights(target)

    @pytest.mark.parametrize(
        "category",
        [
            "imprecise",
            "ambiguous",
            "unparsed",
            "blank_source",
            "recovered_from_neighbour",
            "unrecoverable",
        ],
    )
    @pytest.mark.parametrize(
        ("shape", "bad"),
        [
            ("a string", "X"),
            ("an object", {"a": 1}),
            ("a number", 7),
            ("null", None),
            ("a list of objects", [{"x": 1}]),
            ("a list of numbers", [1]),
        ],
    )
    def test_a_refusal_list_must_be_a_list_of_text(
        self, tmp_path: Path, category: str, shape: str, bad: Any
    ) -> None:
        """Counting one is not the same as reading one.

        `len` answers for a string and for an object, so the string "X" satisfied a
        declared count of one and a one-key dict did too, after which `tuple(...)` turned
        the string into its characters. A list holding a non-string raised inside a join
        that ran AFTER the guard, and escaped as a 500.
        """
        from curtail_core.rights_record import RightsRecordUnavailableError, load_rights

        target = self._write(tmp_path, lambda raw: raw["not_read"].__setitem__(category, bad))
        with pytest.raises(RightsRecordUnavailableError):
            load_rights(target)

    @pytest.mark.parametrize("document", ["null", "5", '"text"', "[1, 2]"])
    def test_a_record_that_is_not_an_object_is_refused(self, tmp_path: Path, document: str) -> None:
        from curtail_core.rights_record import RightsRecordUnavailableError, load_rights

        target = tmp_path / "record.json"
        target.write_text(document)
        with pytest.raises(RightsRecordUnavailableError):
            load_rights(target)

    def test_nothing_escapes_as_anything_other_than_the_stated_error(self, tmp_path: Path) -> None:
        """The property behind every case above, asserted directly.

        A caller catches RightsRecordUnavailableError and returns 503. Anything else
        reaching it is a 500, which tells a judge the service is broken rather than
        that its data is.
        """
        from curtail_core.rights_record import PACKAGED, RightsRecordUnavailableError, load_rights

        raw = json.loads(PACKAGED.read_text())
        shapes: list[Any] = [None, 5, "text", [1]]
        mutations: list[Any] = []
        for section in ("source", "accounting", "not_read", "rights"):
            for bad in shapes:
                mutations.append({**raw, section: bad})
        mutations.append({k: v for k, v in raw.items() if k != "source"})
        mutations.append({**raw, "rights": [None]})
        mutations.append({**raw, "rights": [{"application_number": None}]})

        # Refusal categories too. These are counted with `len` and joined into text, and
        # both operations succeed on shapes that are not lists of strings.
        categories = [
            "imprecise",
            "ambiguous",
            "unparsed",
            "blank_source",
            "recovered_from_neighbour",
            "unrecoverable",
        ]
        for category in categories:
            for bad in ["X", {"a": 1}, 7, None, [{"x": 1}], [1]]:
                mutations.append({**raw, "not_read": {**raw["not_read"], category: bad}})

        assert len(mutations) >= 50, "too few shapes tried for this to mean anything"
        for index, document in enumerate(mutations):
            target = tmp_path / f"record_{index}.json"
            target.write_text(json.dumps(document))
            try:
                load_rights(target)
            except RightsRecordUnavailableError:
                continue
            except Exception as exc:
                pytest.fail(f"shape {index} escaped as {type(exc).__name__}: {exc}")
            else:
                pytest.fail(f"shape {index} was accepted")

    @pytest.mark.parametrize("section", ["source", "accounting"])
    def test_a_null_section_names_itself_rather_than_leaking_an_attribute_error(
        self, tmp_path: Path, section: str
    ) -> None:
        """Two layers stop this, and they are not interchangeable.

        The widened catch turns the AttributeError into the right EXCEPTION TYPE, so
        the escape test above passes with the type checks removed. It cannot turn it
        into a usable message: an operator would read "'NoneType' object has no
        attribute 'get'" and learn nothing about which section of which file is wrong.
        This pins the structural layer independently of its backstop.
        """
        from curtail_core.rights_record import RightsRecordUnavailableError, load_rights

        target = self._write(tmp_path, lambda raw: raw.__setitem__(section, None))
        with pytest.raises(RightsRecordUnavailableError) as caught:
            load_rights(target)
        message = str(caught.value)
        assert f"{section!r} section" in message, message
        assert "NoneType' object has no attribute" not in message, (
            f"the raw attribute error leaked into the message: {message}"
        )

    def test_a_directory_at_the_record_path_is_refused(self, tmp_path: Path) -> None:
        """A directory satisfies `exists()` and then fails inside `read_text`, so the
        check passed and the read raised IsADirectoryError, uncaught."""
        from curtail_core.rights_record import RightsRecordUnavailableError, load_rights

        target = tmp_path / "record.json"
        target.mkdir()
        with pytest.raises(RightsRecordUnavailableError):
            load_rights(target)

    def test_an_unreadable_file_is_refused_not_raised_through(self, tmp_path: Path) -> None:
        """The docstring promised an unreadable record becomes this error, and only the
        PARSE was guarded, so the case the word "unreadable" most obviously names was
        the one that escaped as a 500."""
        import os

        from curtail_core.rights_record import RightsRecordUnavailableError, load_rights

        target = tmp_path / "record.json"
        target.write_text("{}")
        os.chmod(target, 0o000)
        try:
            if os.access(target, os.R_OK):  # pragma: no cover - running as root
                pytest.fail(
                    "this process can read a mode-000 file, so the check cannot run "
                    "here. Failing rather than skipping: a guard that skips under CI "
                    "is a false green."
                )
            with pytest.raises(RightsRecordUnavailableError):
                load_rights(target)
        finally:
            os.chmod(target, 0o600)

    def test_corrupt_metadata_raises_the_stated_error_not_a_bare_keyerror(
        self, tmp_path: Path
    ) -> None:
        """It escaped the guarded block, so a corrupt file surfaced as a 500 rather
        than as a refusal a caller could report."""
        from curtail_core.rights_record import RightsRecordUnavailableError, load_rights

        target = self._write(tmp_path, lambda raw: raw.pop("source"))
        with pytest.raises(RightsRecordUnavailableError):
            load_rights(target)
