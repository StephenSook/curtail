"""Local Cooperative Solutions under 23 CCR 875(f).

An LCS lets a watershed or tributary deliver fisheries benefits, flow
contributions, or trackable water savings INSTEAD of curtailment, monitored by
an approved Coordinating Entity. Rights under an approved or pending LCS are
exempt from curtailment.

The important correction: this is not a boolean. Earlier drafts of the build
spec called it a "voluntary conservation agreement exemption" and modeled it as
a flag, which is wrong on both counts. The legal instrument is petitioned,
approved, conditioned, objectionable and revocable, so it is a status with a
lifecycle. A right protected today can lose protection when an LCS is rescinded
for non-performance, and a flag cannot represent that.

Also legally distinct and frequently conflated: the 2021 Shasta River Safe
Harbor Agreement, covering ten private agricultural operators across more than
30,000 contiguous acres through NOAA Fisheries and CDFW. Safe Harbor
participation does NOT exempt anyone from curtailment. That requires a separate
approved LCS or another regulatory exception. This module refuses to treat the
two as interchangeable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from curtail_core.adjudications import Basin


class LcsState(StrEnum):
    """The lifecycle of a Local Cooperative Solution.

    Ordered as the process runs, not alphabetically.
    """

    PETITIONED = "petitioned"
    NOTICED = "noticed"
    OBJECTED = "objected"
    APPROVED = "approved"
    CONDITIONED = "conditioned"
    MONITORED = "monitored"
    RESCINDED = "rescinded"


#: Legal transitions. Anything not listed is rejected rather than silently
#: allowed, because an LCS that jumps from petitioned straight to monitored has
#: skipped the notice and objection window that gives it legitimacy.
VALID_TRANSITIONS: dict[LcsState, frozenset[LcsState]] = {
    LcsState.PETITIONED: frozenset({LcsState.NOTICED, LcsState.RESCINDED}),
    LcsState.NOTICED: frozenset({LcsState.OBJECTED, LcsState.APPROVED, LcsState.RESCINDED}),
    LcsState.OBJECTED: frozenset({LcsState.APPROVED, LcsState.CONDITIONED, LcsState.RESCINDED}),
    LcsState.APPROVED: frozenset({LcsState.CONDITIONED, LcsState.MONITORED, LcsState.RESCINDED}),
    LcsState.CONDITIONED: frozenset({LcsState.MONITORED, LcsState.RESCINDED}),
    LcsState.MONITORED: frozenset({LcsState.CONDITIONED, LcsState.RESCINDED}),
    LcsState.RESCINDED: frozenset(),
}

#: States in which a covered right is protected from curtailment. The
#: regulation protects rights under an APPROVED OR PENDING solution, so the
#: review states count and only rescission removes protection.
PROTECTIVE_STATES: frozenset[LcsState] = frozenset(
    {
        LcsState.PETITIONED,
        LcsState.NOTICED,
        LcsState.OBJECTED,
        LcsState.APPROVED,
        LcsState.CONDITIONED,
        LcsState.MONITORED,
    }
)

#: Coordinating Entities approved to monitor an LCS.
COORDINATING_ENTITIES: frozenset[str] = frozenset(
    {
        "California Department of Fish and Wildlife",
        "Scott River Water Trust",
        "Shasta Valley Resource Conservation District",
        "Siskiyou Resource Conservation District",
    }
)

#: Baseline years a reduction may be measured against, 23 CCR 875(f)(4).
BASELINE_YEARS: frozenset[int] = frozenset({2020, 2021, 2022, 2023})


class LcsTransitionError(ValueError):
    """Raised on an illegal lifecycle transition."""


@dataclass(frozen=True, slots=True)
class ReductionRequirement:
    """The water-use reduction an LCS must deliver.

    Scott, per 875(f)(4)(D)(v)a: "A net reduction of water use of 30 percent
    throughout the irrigation season (April 1 - October 31)", plus a monthly
    30 percent reduction July through October. Shasta's threshold is 15 percent.
    """

    basin: Basin
    season_percent: float
    season_start: tuple[int, int]
    season_end: tuple[int, int]
    monthly_percent: float | None = None
    monthly_months: tuple[int, ...] = ()

    def applies_on(self, when: date) -> bool:
        start = date(when.year, *self.season_start)
        end = date(when.year, *self.season_end)
        return start <= when <= end

    def monthly_applies_on(self, when: date) -> bool:
        return self.monthly_percent is not None and when.month in self.monthly_months


SCOTT_REQUIREMENT = ReductionRequirement(
    basin=Basin.SCOTT,
    season_percent=30.0,
    season_start=(4, 1),
    season_end=(10, 31),
    monthly_percent=30.0,
    monthly_months=(7, 8, 9, 10),
)

SHASTA_REQUIREMENT = ReductionRequirement(
    basin=Basin.SHASTA,
    season_percent=15.0,
    season_start=(4, 1),
    season_end=(10, 31),
)

REQUIREMENTS: dict[Basin, ReductionRequirement] = {
    Basin.SCOTT: SCOTT_REQUIREMENT,
    Basin.SHASTA: SHASTA_REQUIREMENT,
}


@dataclass(frozen=True, slots=True)
class LocalCooperativeSolution:
    """One LCS and the rights it covers."""

    lcs_id: str
    basin: Basin
    state: LcsState
    coordinating_entity: str
    covered_right_ids: frozenset[str]
    baseline_year: int
    effective_from: date | None = None
    effective_to: date | None = None

    def __post_init__(self) -> None:
        if self.coordinating_entity not in COORDINATING_ENTITIES:
            raise ValueError(
                f"{self.coordinating_entity!r} is not an approved Coordinating Entity. "
                f"Approved: {sorted(COORDINATING_ENTITIES)}"
            )
        if self.baseline_year not in BASELINE_YEARS:
            raise ValueError(
                f"baseline year {self.baseline_year} is not permitted. "
                f"23 CCR 875(f)(4) allows {sorted(BASELINE_YEARS)}"
            )

    @property
    def requirement(self) -> ReductionRequirement:
        return REQUIREMENTS[self.basin]

    def protects(self, right_id: str, *, on: date | None = None) -> bool:
        """Whether this LCS shields a right from curtailment right now."""
        if right_id not in self.covered_right_ids:
            return False
        if self.state not in PROTECTIVE_STATES:
            return False
        if on is not None:
            if self.effective_from is not None and on < self.effective_from:
                return False
            if self.effective_to is not None and on > self.effective_to:
                return False
        return True

    def transition(self, to_state: LcsState) -> LocalCooperativeSolution:
        """Move to a new lifecycle state, or refuse.

        Raises:
            LcsTransitionError: on an illegal transition.
        """
        allowed = VALID_TRANSITIONS[self.state]
        if to_state not in allowed:
            raise LcsTransitionError(
                f"{self.lcs_id}: cannot move from {self.state} to {to_state}. "
                f"Permitted from {self.state}: {sorted(allowed) or 'none, terminal state'}"
            )
        return LocalCooperativeSolution(
            lcs_id=self.lcs_id,
            basin=self.basin,
            state=to_state,
            coordinating_entity=self.coordinating_entity,
            covered_right_ids=self.covered_right_ids,
            baseline_year=self.baseline_year,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
        )


def safe_harbor_confers_exemption() -> bool:
    """The Shasta River Safe Harbor Agreement does NOT exempt from curtailment.

    A function rather than a constant so the answer is greppable and carries
    its reasoning. Safe Harbor is administered through NOAA Fisheries and CDFW
    and is legally distinct from an LCS under 875(f). Exemption requires a
    separate approved LCS or another regulatory exception. Conflating the two
    in any artifact would be a false statement about a real program.
    """
    return False
