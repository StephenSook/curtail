"""The message contracts the fleet passes between agents.

Types first, agents second. Every agent in this fleet is a separate failure
domain, and the only thing crossing a domain boundary is one of these messages,
so the boundary is only as strong as the type that crosses it.

**Two design rules, both learned from defects in this repository.**

First, a message carries its PROVENANCE, not just its value. The recurring
failure here has been a figure that looks right and cannot be traced: a reading
that came from a research summary, a quote that was composed rather than copied,
a claim that reached a test assertion. Every event below records where its
numbers came from, so a downstream agent can refuse a value it cannot source.

Second, an invalid state should be unrepresentable rather than merely unlikely.
An event announcing a threshold crossing must carry the threshold it crossed and
the reading that crossed it, because a bare "FLOW_BELOW_MINIMUM" is an assertion
nobody can check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from curtail_core.basins import Basin


class EventType(StrEnum):
    """What the Gage Sentinel can announce.

    Deliberately small. An agent that can emit anything is an agent whose output
    nothing downstream can exhaustively handle.
    """

    #: Discharge is under the operative minimum for the date.
    FLOW_BELOW_MINIMUM = "flow_below_minimum"
    #: Discharge has been at or above the minimum for a sustained window. The
    #: window matters: a single reading above the line is noise, and acting on
    #: one would produce a suspension that has to be reversed days later.
    FLOW_RECOVERED_SUSTAINED = "flow_recovered_sustained"
    #: Within the near-threshold band either side of the minimum. Carries a
    #: recommendation to verify in the field, mirroring real Fort Jones practice.
    READING_NEAR_THRESHOLD = "reading_near_threshold"
    #: At or above the minimum, but NOT for long enough to call it a recovery.
    #: A distinct state, and the one a premature suspension comes from. Without
    #: it a "not yet" is indistinguishable from a "yes", which is how a
    #: suspension gets issued on a single anomalous reading.
    FLOW_ABOVE_MINIMUM_UNSUSTAINED = "flow_above_minimum_unsustained"
    #: USGS revised a past reading. The consequential part is not the new number,
    #: it is WHICH ORDERS rested on the superseded one.
    REVISION_IMPACT = "revision_impact"


class Provenance(StrEnum):
    """How a value entered the system. Never inferred, always recorded.

    `UNSOURCED` exists so that an untraceable value can be represented and
    refused, rather than being quietly indistinguishable from a sourced one.
    """

    #: Live call to the USGS OGC API.
    USGS_LIVE = "usgs_live"
    #: A cached USGS response, replayed. The demo runs from cache when the API
    #: flakes, and the console must be able to say so on screen.
    USGS_CACHED = "usgs_cached"
    #: A field measurement submitted by a human observer.
    FIELD_MEASUREMENT = "field_measurement"
    #: Read out of a Board order or addendum.
    BOARD_DOCUMENT = "board_document"
    #: Provenance unknown. A consumer MUST refuse to act on this.
    UNSOURCED = "unsourced"


def _now() -> datetime:
    """Timezone-aware, always. A naive timestamp on a legal record is a defect:
    these events feed statutory clocks measured in days."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class GageEvent:
    """One announcement from the Gage Sentinel.

    Frozen because an event is a statement about a moment. A mutable event is a
    record that can be edited after the fact, which is exactly what an audit log
    exists to prevent.
    """

    event_type: EventType
    basin: Basin
    #: The discharge that triggered this, in cubic feet per second.
    observed_cfs: float
    #: The operative minimum on the date, so a reader can check the comparison
    #: without re-deriving the schedule.
    minimum_cfs: float
    #: When the reading was TAKEN, which is not when this event was emitted.
    #: The July 2025 sequence turned on a reading taken at 21:30 and an order
    #: issued the next morning.
    observed_at: datetime
    provenance: Provenance
    correlation_id: str
    emitted_at: datetime = field(default_factory=_now)
    #: Free-text, human-readable, and always populated for a near-threshold or
    #: revision event, because those are the ones a person has to act on.
    note: str = ""

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError(
                "observed_at must be timezone aware. These events feed statutory "
                "clocks measured in days, and a naive timestamp silently assumes "
                "a timezone that may not be the basin's."
            )
        if not self.correlation_id:
            raise ValueError(
                "correlation_id is required. Without it a message that dead-letters "
                "cannot be traced back to the poll that produced it, which is the "
                "one thing the chaos drill has to demonstrate."
            )

    @property
    def actionable(self) -> bool:
        """Whether a downstream agent may act on this without a human first.

        An unsourced value is never actionable. This is the structural version of
        the rule that a figure with no traceable origin does not enter a legal
        record, and it is enforced here rather than trusted to each consumer.
        """
        return self.provenance is not Provenance.UNSOURCED

    @property
    def shortfall_cfs(self) -> float:
        """How far under the minimum, positive when short, negative when over."""
        return self.minimum_cfs - self.observed_cfs
