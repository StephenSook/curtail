"""The four adjudications named by 23 CCR 875.5(d), and the right classes they carry.

Section 875.5(d) defines FOUR relevant adjudications, each including all
supplements. Three of them govern the Scott River watershed and one governs the
Shasta. That distinction is load-bearing rather than trivia: the Scott priority
ladder at 875.5(a)(1)(A)(iii) covers "all post-1914 in the three adjudications"
and grouping (viii) covers "French Creek plus Shackleford plus Schedule B", so
a model carrying only Scott and Shasta cannot place a Shackleford or French
Creek right on the ladder at all.

Both decrees behind the Scott and Shasta schedules are scanned imagery with
imperfect OCR layers, so priority-defining fields are never trusted from raw
extraction. That is an ingestion rule, enforced at M1, not something this
module can check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Basin(StrEnum):
    """The two watersheds under the emergency regulation."""

    SCOTT = "scott"
    SHASTA = "shasta"


class AdjudicationId(StrEnum):
    SCOTT = "scott"
    SHACKLEFORD = "shackleford"
    FRENCH_CREEK = "french_creek"
    SHASTA = "shasta"


@dataclass(frozen=True, slots=True)
class Adjudication:
    """A court decree that fixes priorities for a set of rights."""

    id: AdjudicationId
    name: str
    court_number: str
    decree_date: date
    basin: Basin


#: 23 CCR 875.5(d). Each includes all supplements.
ADJUDICATIONS: dict[AdjudicationId, Adjudication] = {
    AdjudicationId.SCOTT: Adjudication(
        id=AdjudicationId.SCOTT,
        name="Scott River",
        court_number="Siskiyou County Superior Court No. 30662",
        decree_date=date(1980, 1, 30),
        basin=Basin.SCOTT,
    ),
    AdjudicationId.SHACKLEFORD: Adjudication(
        id=AdjudicationId.SHACKLEFORD,
        name="Shackleford",
        court_number="No. 13775",
        decree_date=date(1950, 4, 3),
        basin=Basin.SCOTT,
    ),
    AdjudicationId.FRENCH_CREEK: Adjudication(
        id=AdjudicationId.FRENCH_CREEK,
        name="French Creek (Mason v. Bemrod)",
        court_number="No. 14478",
        decree_date=date(1959, 7, 1),
        basin=Basin.SCOTT,
    ),
    AdjudicationId.SHASTA: Adjudication(
        id=AdjudicationId.SHASTA,
        name="Shasta River",
        court_number="No. 7035",
        decree_date=date(1932, 12, 29),
        basin=Basin.SHASTA,
    ),
}

#: The three that the Scott ladder's "three adjudications" phrase refers to.
SCOTT_ADJUDICATIONS: frozenset[AdjudicationId] = frozenset(
    {AdjudicationId.SCOTT, AdjudicationId.SHACKLEFORD, AdjudicationId.FRENCH_CREEK}
)


class Schedule(StrEnum):
    """Scott decree schedules, from the decree's own structure.

    Schedule B is subdivided by tributary (B1 through B38 and beyond) and
    Schedule D by priority (D1 through D4). Only the top-level letter and the
    D-rank matter for the 875.5 ladder.
    """

    A = "A"  # Special Class rights
    B = "B"  # Independent tributary streams
    C = "C"  # Ground water interconnected with the Scott River
    D1 = "D1"  # Natural flow of Scott River, by priority
    D2 = "D2"
    D3 = "D3"
    D4 = "D4"
    E = "E"  # Post-1914 appropriative rights


class RightClass(StrEnum):
    """How a right relates to the land and the source.

    The distinction decides which end of the ladder a right sits on. Riparian
    and overlying groundwater rights are correlative and sit at the senior end;
    appropriative rights are ranked by priority date.
    """

    APPROPRIATIVE = "appropriative"
    RIPARIAN = "riparian"
    OVERLYING_GROUNDWATER = "overlying_groundwater"


@dataclass(frozen=True, slots=True)
class OverlyingLandFacts:
    """The three-prong test at 875.5(b)(1)(A) for the Shasta.

    A groundwater diversion is APPROPRIATIVE rather than overlying if ANY of
    these is true: the diverter does not own the overlying land, or uses the
    water on non-overlying land, or sells or distributes it to another party.

    Modeled as explicit facts rather than a precomputed boolean so the audit
    record shows which prong decided it. "Because a flag said so" is not a
    justification a signing official can review.
    """

    owns_overlying_land: bool
    uses_water_on_overlying_land: bool
    sells_or_distributes_to_another: bool

    def is_appropriative(self) -> bool:
        return (
            not self.owns_overlying_land
            or not self.uses_water_on_overlying_land
            or self.sells_or_distributes_to_another
        )

    def deciding_prongs(self) -> tuple[str, ...]:
        """Which prongs fired, for the per-right justification ledger."""
        reasons: list[str] = []
        if not self.owns_overlying_land:
            reasons.append("diverter does not own the overlying land")
        if not self.uses_water_on_overlying_land:
            reasons.append("water is used on non-overlying land")
        if self.sells_or_distributes_to_another:
            reasons.append("water is sold or distributed to another party")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class WaterRight:
    """A single water right as the priority ladder needs to see it.

    Deliberately not the full database row. This carries only what 875.5 keys
    on, so the ladder cannot accidentally depend on an unrelated field.
    """

    right_id: str
    basin: Basin
    right_class: RightClass
    #: None means the right is not in any of the four decrees, which for the
    #: Scott means post-Adjudication and for the Shasta means initiated after
    #: the Shasta Adjudication.
    adjudication: AdjudicationId | None = None
    schedule: Schedule | None = None
    priority_date: date | None = None
    #: True when the priority date is genuinely unknown. Riparian and pre-1914
    #: claims are self-reported Statements, not permits reviewed by the
    #: Division, so a missing date is recorded as missing and never imputed.
    priority_date_missing: bool = False
    is_surplus_class: bool = False
    is_post_1914: bool = False
    #: Shared-source key. Monotonicity is only meaningful within one source.
    source: str = "main_stem"
    #: Annual diversion in acre-feet, where known. Feeds the 875.5(c)
    #: discretion over groundwater under two acre-feet per annum.
    annual_acre_feet: float | None = None
    overlying_facts: OverlyingLandFacts | None = None

    @property
    def is_in_decree(self) -> bool:
        return self.adjudication is not None
