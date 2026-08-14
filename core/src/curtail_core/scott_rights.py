"""Load the Scott rights record, placed by the Board's own curtailment group.

**Separate from `rights_record` on purpose, and the reason is the schema rather than
tidiness.** The Shasta record carries priority dates and date bands, and its loader
rebuilds an `AttachmentReport` and re-runs the placement conversion. The Scott record
carries a stated group per right and nothing to infer from. Forcing one loader to serve
both would mean a union shape whose every field is optional, which is the kind of change
that earns its risk long after a submission freeze rather than days before one.

**Why the stated group is load-bearing and not a convenience.** Placing Addendum 12's 384
rights from attributes alone agrees with the Board on **8 of them**: inference puts all
384 in Group 1, which is curtailed first, while the Board puts 258 in Group 8, which is
curtailed nearly last. That is 376 rights curtailed at an extent that does not reach
them. The Board's column is not a hint to cross-check, it is the authority, and reading
it is the only honest way to load this table.

Same refusal discipline as the Shasta loader: every failure is named, and an empty rights
list is never returned, because a recommendation reaching nobody reads exactly like a
real answer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Any

from curtail_core.adjudications import WaterRight
from curtail_core.basins import Basin
from curtail_core.rights_record import RightsRecordUnavailableError

#: Inside the package first, because a container has no repository. Same reason the fact
#: sheet and the console page ship packaged.
PACKAGED = Path(__file__).resolve().parent / "data" / "rights_scott_addendum12.json"
IN_REPO = Path(__file__).resolve().parents[3] / "data" / "rights_scott_addendum12.json"

#: The nine groupings 875.5(a)(1)(A) defines.
VALID_GROUPS = range(1, 10)


@dataclass(frozen=True, slots=True)
class LoadedScottRights:
    """The rights, and where they came from."""

    rights: tuple[WaterRight, ...]
    document: str
    issued: date
    source_sha256: str
    rows_parsed: int

    @property
    def provenance(self) -> str:
        return (
            f"{self.document}, issued {self.issued.isoformat()}. "
            f"{self.rows_parsed} rights, grouped by the State Water Board's own column "
            "rather than by inference."
        )


def record_path() -> Path:
    return PACKAGED if PACKAGED.exists() else IN_REPO


def _require_a_readable_file(source: Path) -> None:
    """Every way this can fail, named. Copied in spirit from the Shasta loader, whose
    comments record why each branch exists: a directory, a named pipe that HUNG the
    suite for ten minutes, and a parent whose mode denies traversal."""
    try:
        info = source.stat()
    except FileNotFoundError as exc:
        raise RightsRecordUnavailableError(
            f"the Scott rights record is not at {source}. Either it has not been "
            "generated (scripts/extract_scott_attachment_a.py) or this package was "
            "built without it. Refusing to continue with no rights: an empty list "
            "would produce a recommendation that reaches nobody, which reads exactly "
            "like a real answer."
        ) from exc
    except OSError as exc:
        raise RightsRecordUnavailableError(
            f"the Scott rights record at {source} could not even be inspected: {exc}. "
            "That is not the same as absent, and saying so would send a reader looking "
            "for something that may well be present."
        ) from exc

    if not S_ISREG(info.st_mode):
        what = "a directory" if S_ISDIR(info.st_mode) else "not a regular file"
        raise RightsRecordUnavailableError(
            f"there is something at {source}, but it is {what} rather than the Scott rights record."
        )


def _digest(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_scott_rights(path: Path | None = None) -> LoadedScottRights:
    """Read the Scott record and build placed rights from the Board's stated groups.

    Raises:
        RightsRecordUnavailableError: absent, unreadable, uninspectable, unparseable, or
            internally inconsistent, each named. Loudly, because the alternative is an
            empty rights list that reads as a valid answer.
    """
    source = path or record_path()
    _require_a_readable_file(source)

    try:
        raw: dict[str, Any] = json.loads(source.read_text())
        meta = raw["source"]
        counts = raw["counts"]
        rows = raw["rights"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("the record lists no rights")

        # **The same digest CI checks, verified again at load.** A record edited between
        # the commit and the run would otherwise be trusted by the service even though
        # CI would have rejected it. Cheap, and it closes the window.
        if _digest(rows) != counts["rights_sha256"]:
            raise ValueError(
                "the rights array does not match its committed digest, so this record "
                "was edited without re-running the extractor"
            )
        if len(rows) != counts["rows_parsed"]:
            raise ValueError(
                f"the record lists {len(rows)} rights and claims {counts['rows_parsed']}"
            )

        rights = []
        for row in rows:
            group = row["curtailment_group"]
            if group not in VALID_GROUPS:
                raise ValueError(
                    f"{row['application_number']} states group {group}, outside the nine "
                    "groupings 875.5(a)(1)(A) defines"
                )
            rights.append(
                WaterRight(
                    right_id=row["application_number"],
                    basin=Basin.SCOTT,
                    # right_class deliberately unset: the attachment does not state one,
                    # and a right carrying a stated group never consults it.
                    stated_group=group,
                    source="scott_river",
                )
            )
        issued = date.fromisoformat(meta["issued"])
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        raise RightsRecordUnavailableError(
            f"the Scott rights record at {source} could not be read: {exc}"
        ) from exc

    return LoadedScottRights(
        rights=tuple(rights),
        document=meta["document"],
        issued=issued,
        source_sha256=meta["sha256"],
        rows_parsed=counts["rows_parsed"],
    )
