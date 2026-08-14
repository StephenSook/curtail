"""One way to ask for a basin's rights, so no caller has to know there are two records.

**Why a dispatcher rather than one loader.** The two records have genuinely different
shapes: Shasta's carries priority dates and is placed by inference, Scott's carries the
Board's own curtailment group and is placed by reading it. Merging them would mean a
union whose every field is optional, which loses the property that makes each record
checkable. Keeping the loaders apart and the ENTRY POINT single gives callers one
question to ask and keeps each record honest about its own shape.

**The failure mode this closes.** Every call site used to run `load_rights()` and then
filter by basin, so asking for a basin the record did not contain returned an EMPTY LIST
rather than an error. An empty rights list produces a recommendation that reaches nobody,
which reads exactly like a real answer: the Core says "no action" and an official reads
that as "nothing to do" rather than "we have no data". Three call sites each carried
their own guard against that, at three different levels of care. Now there is one, and it
refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from curtail_core.adjudications import WaterRight
from curtail_core.basins import Basin
from curtail_core.rights_record import RightsRecordUnavailableError, load_rights
from curtail_core.scott_rights import load_scott_rights


@dataclass(frozen=True, slots=True)
class BasinRights:
    """A basin's rights and the document they were read from."""

    rights: tuple[WaterRight, ...]
    document: str
    #: When the source document was issued. Callers cite it, so it travels with the
    #: rights rather than being looked up separately and risking a mismatch.
    issued: date
    source_sha256: str
    provenance: str
    #: Rights the ladder could not place. Empty for Scott by construction: the Board
    #: states every grouping, so nothing is left to fail placement.
    not_placed: tuple[str, ...] = ()
    #: What this record does NOT settle. Carried rather than dropped, because a reader
    #: who sees an empty list assumes there was nothing to say.
    open_questions: tuple[str, ...] = ()


def rights_for(basin: Basin) -> BasinRights:
    """Every right on record for one basin.

    Raises:
        RightsRecordUnavailableError: when the record is absent, unreadable or holds no
            right for this basin. **Never returns an empty tuple.** A caller handed one
            would compute a recommendation reaching nobody and present it as an answer.
    """
    if basin is Basin.SCOTT:
        loaded_scott = load_scott_rights()
        rights = loaded_scott.rights
        document, issued = loaded_scott.document, loaded_scott.issued
        sha, provenance = loaded_scott.source_sha256, loaded_scott.provenance
        not_placed: tuple[str, ...] = ()
        open_questions: tuple[str, ...] = (
            "The attachment states a curtailment group and an application number and "
            "does not state a right class, so class is recorded as unknown rather than "
            "inferred. Placement does not depend on it here: the Board's stated group "
            "is used directly.",
        )
    else:
        loaded = load_rights()
        rights = tuple(r for r in loaded.converted.rights if r.basin is basin)
        document, issued = loaded.document, loaded.issued
        sha, provenance = loaded.source_sha256, loaded.provenance
        not_placed = tuple(loaded.converted.unplaceable)
        open_questions = tuple(loaded.converted.open_questions)

    if not rights:
        raise RightsRecordUnavailableError(
            f"the record for the {basin.value} basin holds no rights. The loaded "
            f"document is {document}. Refusing rather than returning an empty list: a "
            "recommendation computed over no rights reaches nobody and reads exactly "
            "like a finding that nobody needs to be curtailed."
        )
    return BasinRights(
        rights=rights,
        document=document,
        issued=issued,
        source_sha256=sha,
        provenance=provenance,
        not_placed=not_placed,
        open_questions=open_questions,
    )
