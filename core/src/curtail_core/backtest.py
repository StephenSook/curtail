"""Replay historical curtailment decisions against the engine.

This is the credibility artifact. The claim it supports is fixed in wording and
is deliberately narrow:

    Curtail independently determines the priority grouping or tier to which
    curtailment must extend to provide reasonable assurance of meeting the
    drought emergency minimum flow at the compliance gage.

Never "derives the cutoff date". The cutoffs are decree-defined tiers, and
claiming the engine derived them would claim something it does not do.

**What is actually being tested.** Each case supplies the gage reading the Board
itself published as the basis for a decision, on the date the Board published it.
The engine sees that reading and that date and nothing else, and its
recommendation direction is compared against what the Board did. Using the
Board's own published reading rather than a reconstructed gage record removes an
entire class of dispute: nobody has to agree about what the river was doing, only
about what the document says it was doing.

**What this does NOT test.** The engine's near-threshold flag, its judgment
inputs, and the discretion reserved to the official under 875(b)(3) are all
outside the direction test. A case where the Board declined to curtail despite a
reading below the minimum is not necessarily an engine error, because 875(b)(3)
expressly permits the official to decline. Those are reported as DIVERGENT with
the reasoning shown, never silently counted as failures or as successes.

**The era guard.** The 2021 cycle is a separate adoption whose table has not
been verified in full against a primary source. Scoring a 2021 decision against
the readopted schedule risks marking the Board wrong for correctly applying the
rule then in force, so the harness refuses anything before the era boundary.

Measured, not assumed: every Scott month the 2021-era documents state matches the
readopted table. The refusal is about what has been VERIFIED, not about a known
difference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

from curtail_core.basins import Basin
from curtail_core.flow_minimums import (
    ScheduleGapError,
    is_below_minimum,
    is_near_threshold,
    minimum_flow,
)

CASES_PATH = Path(__file__).resolve().parents[3] / "data" / "backtest_cases.json"


class Direction(StrEnum):
    """Which way a decision points, which is what the engine can be scored on.

    The engine computes whether flow is below the operative minimum. It does not
    choose between "reinstate" and "impose", which depend on whether an order is
    already in force, so both collapse to RESTRICT here. Scoring the finer verb
    would be scoring the engine on a question it was never asked.
    """

    RESTRICT = "restrict"
    RELIEVE = "relieve"


#: The Board's verbs, mapped to the direction each one points.
_ACTION_DIRECTION: dict[str, Direction] = {
    "impose": Direction.RESTRICT,
    "reinstate": Direction.RESTRICT,
    "suspend": Direction.RELIEVE,
    "rescind": Direction.RELIEVE,
}


class Outcome(StrEnum):
    MATCH = "match"
    #: The engine and the Board pointed different ways. Not automatically an
    #: engine error: 875(b)(3) lets the official decline to curtail, narrow the
    #: grouping, or suspend, on grounds the engine does not hold.
    DIVERGENT = "divergent"
    #: Refused rather than scored. Counted in neither numerator nor denominator.
    NOT_SCORABLE = "not_scorable"


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    basin: Basin
    decision_date: date
    reading_cfs: float
    minimum_cfs: float | None
    board_direction: Direction | None
    engine_direction: Direction | None
    outcome: Outcome
    near_threshold: bool
    reasoning: str
    source_quote: str

    @property
    def scored(self) -> bool:
        return self.outcome is not Outcome.NOT_SCORABLE


@dataclass(frozen=True, slots=True)
class BacktestReport:
    results: tuple[CaseResult, ...]
    era_id: str
    excluded: tuple[dict[str, Any], ...]

    @property
    def scored(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if r.scored)

    @property
    def matches(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.MATCH)

    @property
    def divergent(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.DIVERGENT)

    @property
    def refused(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.NOT_SCORABLE)

    @property
    def headline(self) -> str:
        """The claim, phrased so it cannot overstate itself.

        The denominator is what was actually scored, never what exists. A refused
        case appears in neither half of the fraction and is reported separately,
        because a denominator that swallowed refusals would describe the engine
        as tested against documents it never saw.
        """
        return (
            f"Curtail reproduces the direction of {len(self.matches)} of "
            f"{len(self.scored)} scored historical curtailment decisions "
            f"({len(self.refused)} refused, {len(self.excluded)} excluded before scoring)."
        )


def _load(path: Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((path or CASES_PATH).read_text())
    return data


def score_case(case: dict[str, Any], *, earliest_scorable: date) -> CaseResult:
    """Score one historical decision. Refuses rather than guesses."""
    case_id = case["id"]
    basin = Basin(case["basin"])
    decision_date = date.fromisoformat(case["decision_date"])
    reading = float(case["reading_cfs"])
    quote = case["source_quote"]

    def refuse(why: str) -> CaseResult:
        return CaseResult(
            case_id=case_id,
            basin=basin,
            decision_date=decision_date,
            reading_cfs=reading,
            minimum_cfs=None,
            board_direction=None,
            engine_direction=None,
            outcome=Outcome.NOT_SCORABLE,
            near_threshold=False,
            reasoning=why,
            source_quote=quote,
        )

    if decision_date < earliest_scorable:
        return refuse(
            f"Decision predates the regulatory era this schedule encodes "
            f"(earliest scorable {earliest_scorable.isoformat()}). That era's flow "
            "table has not been verified in full against a primary source, so "
            "scoring it here risks marking the Board wrong for correctly applying "
            "the rule then in force."
        )

    board_direction = _ACTION_DIRECTION.get(case["board_action"])
    if board_direction is None:
        return refuse(
            f"Board action {case['board_action']!r} does not map to a direction, "
            "so there is nothing to compare. Amendments and list updates change "
            "who is covered rather than whether the threshold was crossed."
        )

    try:
        minimum = minimum_flow(basin, decision_date)
    except ScheduleGapError as exc:
        return refuse(f"No minimum flow encoded for this date: {exc}")

    below = is_below_minimum(basin, decision_date, reading)
    near = is_near_threshold(basin, decision_date, reading)
    engine_direction = Direction.RESTRICT if below else Direction.RELIEVE

    matched = engine_direction is board_direction
    comparison = "below" if below else "at or above"
    reasoning = (
        f"{reading:g} cfs is {comparison} the {minimum:g} cfs minimum for "
        f"{decision_date.isoformat()}, so the engine recommends "
        f"{engine_direction.value}. The Board acted to {board_direction.value}."
    )
    if near:
        reasoning += (
            " The reading is inside the near-threshold band, so the engine also "
            "flags field verification as recommended."
        )
    if not matched:
        reasoning += (
            " DIVERGENT. This is not automatically an engine error: 875(b)(3) "
            "permits the official to decline to issue, to use a smaller priority "
            "grouping, or to suspend, on hydrologic, weather, fisheries and "
            "voluntary-contribution grounds the engine does not hold."
        )

    return CaseResult(
        case_id=case_id,
        basin=basin,
        decision_date=decision_date,
        reading_cfs=reading,
        minimum_cfs=minimum,
        board_direction=board_direction,
        engine_direction=engine_direction,
        outcome=Outcome.MATCH if matched else Outcome.DIVERGENT,
        near_threshold=near,
        reasoning=reasoning,
        source_quote=quote,
    )


def run(path: Path | None = None) -> BacktestReport:
    data = _load(path)
    era = data["regulatory_era"]
    earliest = date.fromisoformat(era["earliest_scorable_date"])
    results = tuple(score_case(c, earliest_scorable=earliest) for c in data["cases"])
    return BacktestReport(
        results=results,
        era_id=era["id"],
        excluded=tuple(data.get("excluded_with_reason", ())),
    )
