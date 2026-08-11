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
from dataclasses import dataclass
from datetime import date
from pathlib import Path
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


def record_path() -> Path:
    if PACKAGED.exists():
        return PACKAGED
    return IN_REPO


def load_rights(path: Path | None = None) -> LoadedRights:
    """Read the record and re-run the conversion the local parse ran.

    Raises:
        RightsRecordUnavailableError: when the record is missing or unreadable. Loudly,
            because the alternative is an empty rights list that reads as a valid
            answer.
    """
    source = path or record_path()
    if not source.exists():
        raise RightsRecordUnavailableError(
            f"the rights record is not at {source}. Either it has not been generated "
            "(scripts/extract_attachment_a.py) or this package was built without it. "
            "Refusing to continue with no rights: an empty list would produce a "
            "recommendation that reaches nobody, which reads exactly like a real answer."
        )

    try:
        raw: dict[str, Any] = json.loads(source.read_text())
        report = _rebuild(raw)
    except (ValueError, KeyError, TypeError) as exc:
        raise RightsRecordUnavailableError(
            f"the rights record at {source} could not be read: {exc}"
        ) from exc

    return LoadedRights(
        converted=to_water_rights(report),
        document=raw["source"]["document"],
        issued=date.fromisoformat(raw["source"]["issued"]),
        source_sha256=raw["source"]["sha256"],
        rows_parsed=raw["accounting"]["parsed"],
        application_numbers_seen=raw["accounting"]["application_numbers_seen"],
    )


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
