"""Load the committed rights record into the records the ladder consumes.

The corpus PDFs are fetched rather than vendored, so a deployed service cannot re-parse
Attachment A. It reads this instead: the record a local run wrote, hashed against the
document it came from.

**The record travels INSIDE the package.** That is not tidiness. This project has twice
shipped an asset resolved relative to the repository, which passes every local test and
is absent from every container, and a service that 503s on its own data is worse than
one that never claimed to have it. The generator writes both copies and its `--check`
verifies both, so the packaged one cannot drift from the judge-facing one.

**Rows are rebuilt and re-converted rather than read off as rights.** The record holds
all 85 parsed rows, including the ones whose decree membership cannot be established,
and `to_water_rights` is what decides which of those may be placed. Going through it
again here means the deployed service and a local parse cannot disagree about which
rights exist: there is one function that answers that, not two.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Any

from curtail_core.attachment_a import (
    AttachedRight,
    AttachmentReport,
    ConversionResult,
    DateBand,
    to_water_rights,
)

#: Packaged first, because that is the copy that exists when installed.
PACKAGED = Path(__file__).resolve().parent / "data" / "rights_shasta_addendum6.json"
#: Development fallback only. A checkout has both; a wheel has only the first.
IN_REPO = Path(__file__).resolve().parents[3] / "data" / "rights_shasta_addendum6.json"


class RightsRecordUnavailableError(RuntimeError):
    """Raised when the record is absent. Never falls back to an empty rights list.

    An empty list is a perfectly valid input to `recommend`, which would answer that no
    right is reached and hand an official a recommendation to curtail nobody. A missing
    data file must never be able to produce a confident answer.
    """


@dataclass(frozen=True, slots=True)
class LoadedRights:
    """The rights, what could not be placed, and where it all came from."""

    converted: ConversionResult
    #: The document these rights were read out of, for citation.
    document: str
    issued: date
    source_sha256: str
    rows_parsed: int
    application_numbers_seen: int

    @property
    def provenance(self) -> str:
        return (
            f"{self.document}, issued {self.issued.isoformat()}. "
            f"{self.application_numbers_seen} application numbers seen, "
            f"{self.rows_parsed} rows parsed, {len(self.converted.rights)} placed on the "
            f"ladder, {len(self.converted.unplaceable)} refused placement."
        )


def _require_a_readable_file(source: Path) -> None:
    """Refuse before reading, with a message that says what is actually wrong.

    **Four states, four sentences, and conflating them was the defect.** Switching
    the check from `exists` to `is_file` stopped a directory raising
    IsADirectoryError, but sent it down the ABSENT branch, so the refusal announced
    that the record "is not at" a path where something plainly was. An operator would
    go looking for a missing file and find a directory sitting there. Reporting the
    wrong cause is its own failure, distinct from reporting no cause.

    The stat calls are guarded too. `exists` and `is_file` raise OSError when a parent
    directory denies traversal, which would have escaped the read guard entirely by
    happening before it.
    """
    # ONE stat, branched on its own exception, rather than exists() and is_file().
    #
    # Those two suppress OSError and return False on some Python versions and propagate
    # it on others: 3.12 swallows a PermissionError, this project's pinned 3.13.13
    # raises it. A review flagged this code as misreporting an untraversable parent as
    # absent, which was NOT true here, precisely because the pinned interpreter
    # propagates. It would have been true on 3.12, and a guard whose correctness depends
    # on which minor version is running is not a guard anyone can reason about.
    #
    # `stat` has one behaviour everywhere: it raises, and the exception says which
    # failure it was.
    try:
        info = source.stat()
    except FileNotFoundError as exc:
        raise RightsRecordUnavailableError(
            f"the rights record is not at {source}. Either it has not been generated "
            "(scripts/extract_attachment_a.py) or this package was built without it. "
            "Refusing to continue with no rights: an empty list would produce a "
            "recommendation that reaches nobody, which reads exactly like a real answer."
        ) from exc
    except OSError as exc:
        raise RightsRecordUnavailableError(
            f"the rights record at {source} could not even be inspected: {exc}. "
            "This is not the same as the file being absent, and saying so would send a "
            "reader looking for something that may well be present."
        ) from exc

    # REGULAR FILE, and this is a liveness check as much as a diagnostic one. Removing
    # it to see what the test caught HUNG the suite for ten minutes: opening a named
    # pipe blocks until a writer appears, and there is none, so a path of the wrong type
    # does not fail here, it stops the process. The message matters; not blocking
    # matters more.
    if not S_ISREG(info.st_mode):
        what = "a directory" if S_ISDIR(info.st_mode) else "not a regular file"
        raise RightsRecordUnavailableError(
            f"there is something at {source}, but it is {what} rather than the rights "
            "record. The file has not been generated over it, and reporting it as "
            "absent would send a reader looking for something that is present."
        )


def record_path() -> Path:
    if PACKAGED.exists():
        return PACKAGED
    return IN_REPO


def load_rights(path: Path | None = None) -> LoadedRights:
    """Read the record and re-run the conversion the local parse ran.

    Raises:
        RightsRecordUnavailableError: for every way this can fail, and each with a
            message naming which one: absent, a directory, unreadable, uninspectable,
            unparseable, or internally inconsistent. Loudly, because the alternative is
            an empty rights list that reads as a valid answer.

            Read as a specification rather than a description. The earlier version said
            "missing or unreadable" and only guarded the parse, so the case its own
            second word named was the one that escaped as a 500.
    """
    source = path or record_path()
    _require_a_readable_file(source)

    try:
        raw: dict[str, Any] = json.loads(source.read_text())
        _validate(raw, source)
        report = _rebuild(raw)
        issued = date.fromisoformat(raw["source"]["issued"])
        # Converted INSIDE the guard. It ran after it, so any error raised while
        # placing rights escaped untranslated and became a 500. The guard has to
        # cover every step that touches the file's contents, not only the parse.
        converted = to_water_rights(report)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        # OSError covers the file system itself: a directory at this path, a mode that
        # denies reading, a truncated mount. The docstring promised that an unreadable
        # record becomes this error and only the PARSE was guarded, so the one case the
        # word "unreadable" most obviously names was the one that escaped as a 500.
        raise RightsRecordUnavailableError(
            f"the rights record at {source} could not be read: {exc}"
        ) from exc

    return LoadedRights(
        converted=converted,
        document=raw["source"]["document"],
        issued=issued,
        source_sha256=raw["source"]["sha256"],
        rows_parsed=raw["accounting"]["parsed"],
        application_numbers_seen=raw["accounting"]["application_numbers_seen"],
    )


#: Every refusal category the record must carry, and the accounting key each one is
#: counted under. A category missing from the file cannot be reconciled against however
#: carefully the generator filled it.
_REFUSAL_CATEGORIES = {"imprecise": "imprecise", "ambiguous": "ambiguous", "unparsed": "unparsed"}

_ROW_FIELDS = {
    "application_number": str,
    "source_as_printed": str,
    "priority_date_missing": bool,
    "band": str,
    "page": int,
}


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """A section, proved to BE a section before anything reads it.

    `raw["source"]` being JSON null made `meta.get(...)` raise AttributeError, which the
    caller did not translate, so a corrupt file surfaced as a 500 rather than as a
    stated refusal. Checking the type here is the real fix; widening the caught tuple
    is the backstop behind it, because the next malformed shape will not be this one.
    """
    value = raw[key]
    if not isinstance(value, dict):
        raise ValueError(f"the record's {key!r} section is a {type(value).__name__}, not an object")
    return value


def _string_list(holder: dict[str, Any], key: str) -> list[str]:
    """A list of strings, or a refusal naming what arrived instead."""
    value = holder[key]
    if not isinstance(value, list):
        raise ValueError(
            f"the record's {key!r} entry is a {type(value).__name__}, not a list. "
            "Counting one is not the same as reading one: len() answers for a string "
            "and for an object too."
        )
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{key}[{index}] is a {type(item).__name__}, not text")
    return value


def _validate(raw: dict[str, Any], source: Path) -> None:
    """Refuse a record that does not hold together, BEFORE any of it is converted.

    **This was found by an adversarial pass and every case below was reproduced.** The
    loader used to rebuild whatever rows happened to be present. Truncating the rights
    array while leaving the accounting alone loaded a partial table and reported the
    original count as provenance; erasing a refusal category lost it silently; and a
    missing `issued` escaped as a bare KeyError, so a corrupt file surfaced as a 500
    rather than as a stated refusal.

    A partial rights table is the most dangerous shape this system can load. It does not
    look broken. It produces a normal recommendation over fewer rights than the order
    covers, carrying provenance that describes the whole document.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"the record is a {type(raw).__name__}, not an object")
    for key in ("source", "accounting", "not_read", "rights"):
        if key not in raw:
            raise ValueError(f"the record has no {key!r} section")

    meta = _section(raw, "source")
    if not isinstance(meta.get("document"), str) or not meta["document"].strip():
        raise ValueError("the record names no source document")
    if not re.fullmatch(r"[0-9a-f]{64}", str(meta.get("sha256", ""))):
        raise ValueError("the record carries no valid source sha256")
    date.fromisoformat(meta["issued"])  # raises ValueError or KeyError, both caught above

    accounting = _section(raw, "accounting")
    for key in ("application_numbers_seen", "parsed", *_REFUSAL_CATEGORIES.values()):
        value = accounting.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"accounting.{key} is not a count: {value!r}")

    rows = raw["rights"]
    if not isinstance(rows, list):
        raise ValueError("the record's rights section is not a list")
    if len(rows) != accounting["parsed"]:
        raise ValueError(
            f"the record holds {len(rows)} rows but claims {accounting['parsed']} were "
            "parsed. A partial table produces a normal recommendation over fewer rights "
            "than the order covers, under provenance describing the whole document."
        )

    unread = _section(raw, "not_read")
    for category in (
        *_REFUSAL_CATEGORIES,
        "blank_source",
        "recovered_from_neighbour",
        "unrecoverable",
    ):
        if category not in unread:
            raise ValueError(f"the record has no {category!r} section")
        # A LIST OF STRINGS, proved before it is counted or joined. `len` works on a
        # string and on a dict, so `"X"` satisfied a count of one and a dict of one key
        # satisfied it too, after which `tuple(...)` turned the string into its
        # characters. A list holding a non-string then raised inside a join, after the
        # guard, and escaped as a 500.
        _string_list(unread, category)
    for category, counted_as in _REFUSAL_CATEGORIES.items():
        if len(unread[category]) != accounting[counted_as]:
            raise ValueError(
                f"{category} lists {len(unread[category])} entries but the accounting "
                f"says {accounting[counted_as]}"
            )

    buckets = accounting["parsed"] + sum(accounting[k] for k in _REFUSAL_CATEGORIES.values())
    if buckets != accounting["application_numbers_seen"]:
        raise ValueError(
            f"{accounting['application_numbers_seen']} application numbers were seen but "
            f"{buckets} reached a bucket, so the record does not account for its own rows"
        )

    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"row {index} is not a record")
        for field, kind in _ROW_FIELDS.items():
            if not isinstance(row.get(field), kind):
                raise ValueError(f"row {index} has no valid {field}")
        number = row["application_number"]
        if number in seen:
            raise ValueError(
                f"{number} appears twice. A duplicate doubles a right's weight in every "
                "count taken off this table."
            )
        seen.add(number)
        if row["priority_date"] is not None:
            date.fromisoformat(row["priority_date"])
        # Reaches `date(year, 1, 1)` during conversion, so a string here raised a
        # TypeError from inside to_water_rights rather than from the parse.
        year = row.get("priority_year_only")
        if year is not None and (not isinstance(year, int) or isinstance(year, bool)):
            raise ValueError(f"{number} has a priority_year_only that is not a year: {year!r}")
        if row["priority_date"] is not None and row["priority_date_missing"]:
            raise ValueError(f"{number} states a date and is also marked missing")
    if len(seen) != len(rows):  # pragma: no cover - the loop above already refuses
        raise ValueError("duplicate application numbers survived the row scan")


def _rebuild(raw: dict[str, Any]) -> AttachmentReport:
    """Rebuild the parse report from the record, losing nothing that matters.

    The refusal lists are carried across, not dropped, so a service reading the record
    can still say what the parse could not read. A loader that kept only the successes
    would present an incomplete table as a complete one, which is the failure the parser
    was written to avoid in the first place.
    """
    rows = tuple(
        AttachedRight(
            application_number=row["application_number"],
            source_as_printed=row["source_as_printed"],
            priority_date=(
                date.fromisoformat(row["priority_date"]) if row["priority_date"] else None
            ),
            priority_date_missing=row["priority_date_missing"],
            band=DateBand(row["band"]),
            page=row["page"],
            priority_year_only=row["priority_year_only"],
        )
        for row in raw["rights"]
    )
    unread = raw["not_read"]
    return AttachmentReport(
        application_numbers_seen=raw["accounting"]["application_numbers_seen"],
        rights=rows,
        unparsed=tuple(unread["unparsed"]),
        blank_source=tuple(unread["blank_source"]),
        imprecise=tuple(unread["imprecise"]),
        ambiguous=tuple(unread["ambiguous"]),
        recovered_from_neighbour=tuple(unread["recovered_from_neighbour"]),
        unrecoverable=tuple(unread["unrecoverable"]),
    )
