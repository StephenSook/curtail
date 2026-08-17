"""Gage Sentinel: decides what a gage reading means, and says so once.

**This agent is deterministic on purpose, and that is an architectural claim.**

The plan for M3 predicted that the Order Scribe would be the only agent needing a
language model, and this module is the first test of that prediction. Deciding
whether 48.7 cfs is below a 50 cfs minimum is arithmetic. Putting a model in that
path would add a failure mode (a wrong comparison stated fluently) to a question
that has one correct answer, and would make the whole fleet harder to defend as
NECESSARY rather than decorative.

What the model tier is for, later, is drafting prose a human will sign. Not this.

**Hysteresis is the substance here.** A single reading above the minimum is not a
recovery. Acting on one produces a suspension that has to be reversed days later,
which is the failure the real record shows: the Board issued fourteen addenda in
one Shasta season. So recovery requires a SUSTAINED window and the window is
stated, not implied.

**The near-threshold band is symmetric, and deliberately so.** A reading just
above the line is as worth verifying as one just below, because the decision to
RELEASE water rests on the same number as the decision to withhold it. The July
2025 sequence is the proof: a reading of 48.7 cfs led to curtailment, field
measurement moved the rating curve, and the true value was 78.4.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from curtail_agents.events import EventType, GageEvent, Provenance
from curtail_core.basins import Basin
from curtail_core.flow_minimums import (
    NEAR_THRESHOLD_BAND_CFS,
    ScheduleGapError,
    is_below_minimum,
    is_near_threshold,
    minimum_flow,
)

#: How long flow must hold at or above the minimum before recovery is announced.
#:
#: Not a tuning knob. Without it, every fluctuation across the line produces an
#: order, and the administrative record fills with reversals. Two days is the
#: shortest window that survives a single anomalous reading, which is the failure
#: mode the July 2025 sequence demonstrated: one reading, later revised by 30 cfs,
#: was enough to reinstate curtailment on an entire watershed.
SUSTAINED_RECOVERY_WINDOW = timedelta(days=2)


@dataclass(frozen=True, slots=True)
class Observation:
    """One gage reading with the provenance it arrived with."""

    basin: Basin
    observed_cfs: float
    observed_at: datetime
    provenance: Provenance

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone aware")
        # **Found by a refusal-trap eval, not by review.** A discharge of -5 cfs was
        # accepted and CLASSIFIED, which on a sensor fault or a typed minus sign would
        # read as far below the minimum and point at curtailment. The API rejected
        # negatives at its edge, so the guard existed only where a caller happened to be
        # polite; putting it here makes the wrong value unrepresentable rather than
        # caught, and every path into the Sentinel inherits it.
        #
        # Zero is allowed on purpose: a river can genuinely read zero, and refusing that
        # would refuse a real and legally significant condition.
        if not math.isfinite(self.observed_cfs):
            raise ValueError(
                f"observed_cfs is {self.observed_cfs}, which is not a finite number and "
                "cannot be compared with a minimum"
            )
        if self.observed_cfs < 0:
            raise ValueError(
                f"observed_cfs is {self.observed_cfs}, and a river does not run backwards. "
                "A negative discharge is a sensor fault or a typo, and classifying it "
                "would point at curtailment on evidence that does not exist."
            )


class SentinelError(RuntimeError):
    """The Sentinel could not evaluate a reading and refuses to guess."""


def evaluate(
    observation: Observation,
    *,
    correlation_id: str,
    recent: Sequence[Observation] = (),
) -> GageEvent:
    """Turn one reading into exactly one event.

    Exactly one, deliberately. An agent that can emit several events for a single
    reading forces every consumer to reason about combinations, and the near
    threshold case is the one that would otherwise double up: a reading of 48.7
    against a 50 minimum is BOTH below the minimum and inside the band. It is
    announced as READING_NEAR_THRESHOLD, because that is the more actionable
    statement. The event still carries both numbers, so nothing is lost and a
    consumer can compute the comparison itself.

    Args:
        observation: The reading to evaluate.
        correlation_id: Traces this event back to the poll that produced it.
            Required, because a message that dead-letters with no correlation ID
            cannot be traced, and tracing it is what the chaos drill demonstrates.
        recent: Earlier observations, used only to decide whether a recovery has
            been sustained. Order does not matter.

    Raises:
        SentinelError: When no minimum flow is encoded for the date. The
            underlying schedule refuses rather than defaulting, and that refusal
            is passed through rather than being converted into a guess.
    """
    try:
        minimum = minimum_flow(observation.basin, observation.observed_at.date())
    except ScheduleGapError as exc:
        raise SentinelError(
            f"cannot evaluate {observation.basin.value} on "
            f"{observation.observed_at.date().isoformat()}: {exc}"
        ) from exc

    below = is_below_minimum(
        observation.basin, observation.observed_at.date(), observation.observed_cfs
    )
    near = is_near_threshold(
        observation.basin, observation.observed_at.date(), observation.observed_cfs
    )

    def build(event_type: EventType, note: str) -> GageEvent:
        return GageEvent(
            event_type=event_type,
            basin=observation.basin,
            observed_cfs=observation.observed_cfs,
            minimum_cfs=minimum,
            observed_at=observation.observed_at,
            provenance=observation.provenance,
            correlation_id=correlation_id,
            note=note,
        )

    if near:
        side = "below" if below else "at or above"
        return build(
            EventType.READING_NEAR_THRESHOLD,
            (
                f"{observation.observed_cfs:g} cfs is {side} the {minimum:g} cfs "
                f"minimum and within the {NEAR_THRESHOLD_BAND_CFS:g} cfs band "
                "around it. Field verification recommended before an order rests "
                "on this reading. On 2025-07-20 a reading of 48.7 cfs in this band "
                "produced a curtailment that field measurement reversed two days "
                "later at 78.4 cfs."
            ),
        )

    if below:
        return build(
            EventType.FLOW_BELOW_MINIMUM,
            f"{observation.observed_cfs:g} cfs is below the {minimum:g} cfs minimum.",
        )

    if _recovery_is_sustained(observation, recent, minimum):
        return build(
            EventType.FLOW_RECOVERED_SUSTAINED,
            (
                f"{observation.observed_cfs:g} cfs, at or above the {minimum:g} cfs "
                f"minimum for at least {SUSTAINED_RECOVERY_WINDOW.days} days."
            ),
        )

    # Above the minimum, clear of the band, but the window is not covered.
    #
    # This branch had a bug worth recording: it originally fell through to
    # FLOW_RECOVERED_SUSTAINED with an empty note, announcing a sustained
    # recovery that had not happened. Downstream that is a suspension issued on
    # a single reading, which is precisely the failure hysteresis exists to
    # prevent. "Not yet" and "yes" must not share an event type.
    return build(
        EventType.FLOW_ABOVE_MINIMUM_UNSUSTAINED,
        (
            f"{observation.observed_cfs:g} cfs is at or above the {minimum:g} cfs "
            f"minimum, but recovery is not yet sustained across the "
            f"{SUSTAINED_RECOVERY_WINDOW.days} day window. Not a basis for "
            "suspending curtailment."
        ),
    )


def _recovery_is_sustained(
    observation: Observation,
    recent: Sequence[Observation],
    minimum: float,
) -> bool:
    """Whether flow has held at or above the minimum for the whole window.

    Requires evidence covering the window, not merely an absence of contrary
    evidence. With no history at all this returns False, because "we have not
    seen it drop" and "it has not dropped" are different claims, and only the
    second justifies releasing water.
    """
    cutoff = observation.observed_at - SUSTAINED_RECOVERY_WINDOW
    history = [o for o in recent if o.observed_at <= observation.observed_at]
    if not history:
        return False

    # Any dip below the minimum inside the window disqualifies the recovery.
    if any(o.observed_cfs < minimum for o in history if o.observed_at >= cutoff):
        return False

    # The evidence must REACH BACK to the start of the window.
    #
    # A first version filtered history to `observed_at >= cutoff` and then asked
    # whether the oldest survivor was near the cutoff, which is contradictory:
    # the reading that proves the window was covered from its start sits at or
    # just BEFORE the cutoff, and the filter had already discarded it. Two full
    # days of readings above the minimum were therefore reported as an
    # unsustained recovery, and curtailment would have stayed in force on a
    # river that had recovered.
    anchor = [o for o in history if o.observed_at <= cutoff]
    if not anchor:
        return False

    # And the reading that anchors the window must itself be above the minimum,
    # or the window opened during a shortfall.
    return max(anchor, key=lambda o: o.observed_at).observed_cfs >= minimum
