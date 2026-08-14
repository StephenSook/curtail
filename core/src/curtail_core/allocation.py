"""The Allocation Core. It recommends; the official determines.

This is the deterministic heart of the system and no language model touches it.
The reasoning is inspectable line by line, which is what makes the fleet's
output auditable rather than merely plausible.

The single most important thing this module does NOT do is decide how far
curtailment extends. Section 875(b) says the Deputy Director "may issue a
curtailment order upon a determination that without curtailment of diversions,
flows are likely to be reduced below the drought emergency minimum flows", and
875(b)(3) allows that official to decline to issue, to use a smaller priority
grouping, or to suspend orders already issued, weighing CDFW fisheries
information, weather forecasting, ramping needs, voluntary flow contributions
and future flow needs.

So the output separates two categories and never blends them:

  DETERMINISTIC FACTS   the reading against the operative minimum, the computed
                        shortfall, each right's placement on the ladder with its
                        statutory citation, LCS protection status
  JUDGMENT INPUTS       every "may" in the regulation, plus every place the data
                        is incomplete enough that the arithmetic cannot close

The locked claim wording this supports, and the only wording any artifact may
use: Curtail "independently determines the priority grouping/tier to which
curtailment must extend to provide reasonable assurance of meeting the drought
emergency minimum flow at the compliance gage." Never "derives the cutoff date".
The cutoffs are decree-defined tiers, not computed marginal dates.

On incomplete data. Extending curtailment far enough to close a shortfall
requires knowing how much water each right actually diverts, and that is
frequently unknown: riparian and pre-1914 claims are self-reported Statements,
not permits reviewed by the Division. When volumes are missing this module
reports the extent it can justify AND says the arithmetic did not close. It
does not extrapolate. A recommendation that quietly guessed at the volumes
would be indistinguishable from one that knew them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from curtail_core.adjudications import Basin, WaterRight
from curtail_core.clocks import SignatoryRole
from curtail_core.flow_minimums import FlowPeriod, is_near_threshold, minimum_flow
from curtail_core.lcs import LocalCooperativeSolution
from curtail_core.priority import Placement, place


class RecommendedAction(StrEnum):
    """What the Core puts in front of the official. Never self-executing."""

    NO_ACTION = "no_action"
    CONSIDER_CURTAILMENT = "consider_curtailment"
    CONSIDER_SUSPENSION = "consider_suspension"
    FIELD_VERIFICATION_FIRST = "field_verification_first"


@dataclass(frozen=True, slots=True)
class RightLedgerEntry:
    """One line of the per-right justification ledger.

    The ledger is the artifact that makes a signed order reviewable. An official
    reading it can follow, for any single right, why it landed where it did and
    under which subdivision.
    """

    right_id: str
    placement: Placement
    reached_by_extent: bool
    lcs_protected: bool
    lcs_id: str | None
    diversion_cfs: Decimal | None
    note: str
    #: The right's priority date, or None when the record does not state one.
    #:
    #: A ledger without it is missing the single most important fact about a
    #: water right, and it had a second, sharper consequence: the routing guard
    #: validates a drafted order's asserted priority dates against this ledger,
    #: and with no dates here it rejected every draft that named one. A draft
    #: citing the CORRECT cutoff, November 25 1912, was refused. A guard that
    #: blocks lawful orders is worse than no guard, because it will be switched
    #: off.
    #:
    #: None is a real state and is never filled in. Riparian and pre-1914 claims
    #: are self-reported Statements of Water Diversion and Use whose priority
    #: dates are not verified as a condition of the record existing, so a missing
    #: date is reported as missing.
    priority_date: date | None = None
    #: The priority year where the record states one and no full date. An official
    #: reading a ledger line is owed the difference between "we know the year" and "the
    #: record says nothing", and those rendered identically before.
    priority_year_only: int | None = None

    @property
    def would_be_curtailed(self) -> bool:
        """Reached by the extent AND not shielded by a Local Cooperative Solution."""
        return self.reached_by_extent and not self.lcs_protected


@dataclass(frozen=True, slots=True)
class Recommendation:
    """The Core's output. A recommendation, with its reasoning attached."""

    basin: Basin
    evaluated_for: date
    action: RecommendedAction

    # ---- deterministic facts -------------------------------------------------
    observed_cfs: Decimal
    operative_minimum_cfs: Decimal
    shortfall_cfs: Decimal
    near_threshold: bool
    recommended_extent_rank: int | None
    ledger: tuple[RightLedgerEntry, ...]

    # ---- surfaced for the official, never resolved here ----------------------
    judgment_inputs: tuple[str, ...] = field(default_factory=tuple)
    data_quality_flags: tuple[str, ...] = field(default_factory=tuple)

    #: Who the regulation assigns this determination to.
    determination_belongs_to: SignatoryRole = SignatoryRole.DEPUTY_DIRECTOR

    #: True when the extent could be justified arithmetically from known
    #: diversion volumes. False means the extent is a floor, not a solution.
    shortfall_arithmetic_closed: bool = True

    @property
    def rights_reached(self) -> tuple[str, ...]:
        return tuple(e.right_id for e in self.ledger if e.would_be_curtailed)

    @property
    def rights_protected_by_lcs(self) -> tuple[str, ...]:
        return tuple(e.right_id for e in self.ledger if e.lcs_protected)

    @property
    def needs_official_review(self) -> bool:
        """Always true. Stated as a property so no caller can forget it.

        Nothing this module produces is self-executing. The regulation vests
        the determination in a named human official, and the value of the
        deterministic core is precisely that it hands that official a
        reviewable basis rather than an answer.
        """
        return True


def _protecting_lcs(
    right_id: str, solutions: Sequence[LocalCooperativeSolution], on: date
) -> LocalCooperativeSolution | None:
    for lcs in solutions:
        if lcs.protects(right_id, on=on):
            return lcs
    return None


def recommend(
    *,
    basin: Basin,
    when: date,
    observed_cfs: Decimal | float,
    rights: Sequence[WaterRight],
    diversion_rates: Mapping[str, Decimal | float] | None = None,
    lcs_solutions: Sequence[LocalCooperativeSolution] = (),
    flow_override: tuple[FlowPeriod, ...] | None = None,
) -> Recommendation:
    """Produce a curtailment recommendation with a per-right ledger.

    Args:
        basin: Which watershed.
        when: The date being evaluated. Flow minimums are date-period bounded.
        observed_cfs: Discharge at the compliance gage.
        rights: The rights under consideration. Each is placed on the ladder.
        diversion_rates: Known diversion in cfs per right_id. Absent entries are
            treated as UNKNOWN and reported, never as zero.
        lcs_solutions: Local Cooperative Solutions in force.
        flow_override: A CDFW alternative flow schedule under 875(c)(2)(B)-(D).

    Raises:
        ValueError: on a negative reading. A negative discharge is a sensor
            sentinel, not a river.
    """
    observed = Decimal(str(observed_cfs))
    if observed < 0:
        raise ValueError(
            f"observed discharge cannot be negative ({observed}). "
            "A negative value is a sensor sentinel, not a river."
        )

    # Priority dates, keyed by right, so the ledger can carry each one. A right
    # whose record states no date maps to None and stays None.
    _priority_dates: dict[str, date | None] = {r.right_id: r.priority_date for r in rights}
    _priority_years: dict[str, int | None] = {r.right_id: r.priority_year_only for r in rights}

    minimum = Decimal(str(minimum_flow(basin, when, override=flow_override)))
    shortfall = max(Decimal("0"), minimum - observed)
    near = is_near_threshold(basin, when, float(observed), override=flow_override)

    rates = {k: Decimal(str(v)) for k, v in (diversion_rates or {}).items()}

    # Place every right first. Placement raises rather than guessing, so an
    # unplaceable right aborts the whole recommendation instead of quietly
    # producing a set that is missing someone.
    placements = {r.right_id: place(r) for r in rights}

    judgment: list[str] = []
    data_flags: list[str] = []
    for p in placements.values():
        judgment.extend(p.judgment_inputs)
        data_flags.extend(p.data_quality_flags)

    # The near-threshold note belongs on BOTH branches. An action of
    # FIELD_VERIFICATION_FIRST without a recorded reason is an instruction with
    # no basis, and the basis is the whole point of the ledger.
    if near:
        judgment.append(
            f"The reading of {observed} cfs is within 10 cfs of the operative minimum "
            f"of {minimum} cfs. Gage accuracy alone separates action from no action "
            "here, and readings at this compliance point have been revised after "
            "Watermaster District field measurements moved the rating curve. Field "
            "verification is recommended before any determination."
        )

    # No shortfall. Recovery may support suspension, but that is the official's
    # call under 875(b)(3), and a sustained-flow window is required before any
    # suspension recommendation.
    if shortfall == 0:
        action = (
            RecommendedAction.FIELD_VERIFICATION_FIRST
            if near
            else RecommendedAction.CONSIDER_SUSPENSION
        )
        return Recommendation(
            basin=basin,
            evaluated_for=when,
            action=action,
            observed_cfs=observed,
            operative_minimum_cfs=minimum,
            shortfall_cfs=shortfall,
            near_threshold=near,
            recommended_extent_rank=None,
            ledger=tuple(
                RightLedgerEntry(
                    right_id=rid,
                    placement=p,
                    reached_by_extent=False,
                    lcs_protected=_protecting_lcs(rid, lcs_solutions, when) is not None,
                    lcs_id=getattr(_protecting_lcs(rid, lcs_solutions, when), "lcs_id", None),
                    diversion_cfs=rates.get(rid),
                    priority_date=_priority_dates.get(rid),
                    priority_year_only=_priority_years.get(rid),
                    note="not reached: flow is at or above the operative minimum",
                )
                for rid, p in placements.items()
            ),
            judgment_inputs=tuple(judgment),
            data_quality_flags=tuple(data_flags),
        )

    # Walk the ladder from the lowest grouping upward, accumulating recoverable
    # flow, until the shortfall is covered or the ladder is exhausted.
    by_rank = sorted(placements.items(), key=lambda kv: kv[1].rank)
    recovered = Decimal("0")
    extent: int | None = None
    unknown_volume: list[str] = []
    closed = False

    for rid, p in by_rank:
        protecting = _protecting_lcs(rid, lcs_solutions, when)
        if protecting is not None:
            continue  # shielded, contributes nothing recoverable
        rate = rates.get(rid)
        if rate is None:
            unknown_volume.append(rid)
            extent = p.rank
            continue
        recovered += rate
        extent = p.rank
        if recovered >= shortfall:
            closed = True
            break

    if unknown_volume:
        data_flags.append(
            f"{len(unknown_volume)} right(s) have no known diversion rate, so the "
            "shortfall arithmetic could not be closed. The recommended extent is a "
            "FLOOR justified by the rights whose volumes are known, not a solved "
            "answer. Riparian and pre-1914 claims are self-reported Statements of "
            "Water Diversion and Use, not permits reviewed by the Division, so "
            "missing volumes are expected rather than exceptional."
        )

    if not closed:
        judgment.append(
            f"Curtailing every unprotected right on the ladder recovers {recovered} cfs "
            f"against a shortfall of {shortfall} cfs. The arithmetic does not close on "
            "the data available, so whether the extent provides reasonable assurance "
            "under 875(b)(1) is a determination for the official, informed by "
            "hydrologic, weather and other conditions."
        )

    ledger = tuple(
        RightLedgerEntry(
            right_id=rid,
            placement=p,
            reached_by_extent=extent is not None and p.rank <= extent,
            lcs_protected=_protecting_lcs(rid, lcs_solutions, when) is not None,
            lcs_id=getattr(_protecting_lcs(rid, lcs_solutions, when), "lcs_id", None),
            diversion_cfs=rates.get(rid),
            priority_date=_priority_dates.get(rid),
            priority_year_only=_priority_years.get(rid),
            note=_ledger_note(p, extent, _protecting_lcs(rid, lcs_solutions, when), rates.get(rid)),
        )
        for rid, p in by_rank
    )

    return Recommendation(
        basin=basin,
        evaluated_for=when,
        action=(
            RecommendedAction.FIELD_VERIFICATION_FIRST
            if near
            else RecommendedAction.CONSIDER_CURTAILMENT
        ),
        observed_cfs=observed,
        operative_minimum_cfs=minimum,
        shortfall_cfs=shortfall,
        near_threshold=near,
        recommended_extent_rank=extent,
        ledger=ledger,
        judgment_inputs=tuple(judgment),
        data_quality_flags=tuple(data_flags),
        shortfall_arithmetic_closed=closed,
    )


def _ledger_note(
    p: Placement,
    extent: int | None,
    protecting: LocalCooperativeSolution | None,
    rate: Decimal | None,
) -> str:
    if protecting is not None:
        return (
            f"protected by Local Cooperative Solution {protecting.lcs_id} "
            f"(state: {protecting.state.value}) under 23 CCR 875(f)"
        )
    if extent is None or p.rank > extent:
        return f"{p.grouping_label} is senior to the recommended extent; not reached"
    volume = f"{rate} cfs" if rate is not None else "diversion rate unknown"
    return f"reached at {p.grouping_label} per {p.citation}; {volume}"
