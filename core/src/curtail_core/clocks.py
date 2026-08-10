"""Statutory clocks that a curtailment order starts, and the service that starts them.

An order is not just a decision. Adopting one starts several legal clocks at
once, and missing one forecloses a party's remedy. This module computes them.

Why it matters for this project specifically: the Fortified Enterprise Fleet
track asks how agents "safely maintain context across weeks of asynchronous
operations". Tracking hydrology across a season is a graph of numbers. Tracking
the reconsideration window, the Board's response window, and the exhaustion
prerequisite is a legal obligation with a deadline, and it is the difference
between an adjective and a measured mechanism.

The delegation point is the subtle one. Water Code 1126(b) gives an aggrieved
party 30 days from final action to petition for a writ of mandate, and says
reconsideration is NOT a required administrative remedy, EXCEPT where the
decision issued under authority delegated to an officer of the board. Scott and
Shasta curtailment orders are signed by the Deputy Director for the Division of
Water Rights under authority delegated by Board Resolution No. 2012-0061, so
they fall INSIDE that exception, which makes a petition for reconsideration a
mandatory exhaustion prerequisite before judicial review.

Two traps encoded here deliberately:

  - The 90-day Board response deadline is in section 1122, NOT 1123. Section
    1123 governs the manner of reconsideration.
  - Missing the 90 days does not divest the Board of jurisdiction. State Water
    Board Order WR 2023-0039-EXEC: a petitioner may seek judicial review, but
    the Board "is not divested of jurisdiction to act upon the petition simply
    because it failed to complete its review of the petition on time."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class SignatoryRole(StrEnum):
    """Who signs. The regulation distinguishes these and so must the workflow.

    Section 875(b) assigns the curtailment determination to the Deputy
    Director. Section 875(b)(2) assigns certain human health and safety and
    livestock determinations to the EXECUTIVE Director. A generic "a human
    signs" approval queue does not match the role-based authority the
    regulation actually draws.
    """

    DEPUTY_DIRECTOR = "deputy_director"
    EXECUTIVE_DIRECTOR = "executive_director"
    BOARD = "board"


class ClockType(StrEnum):
    RECONSIDERATION_PETITION = "reconsideration_petition"
    BOARD_RESPONSE = "board_response"
    JUDICIAL_REVIEW = "judicial_review"
    CERTIFICATION = "certification"
    INFORMATION_ORDER_RESPONSE = "information_order_response"


@dataclass(frozen=True, slots=True)
class Clock:
    """One statutory deadline running from an order's adoption."""

    clock_type: ClockType
    opens_at: datetime
    closes_at: datetime
    citation: str
    description: str
    #: True when this clock is a prerequisite to judicial review.
    exhaustion_required: bool = False
    #: True when the deadline binds the agency rather than a party, and blowing
    #: it does not extinguish the agency's authority.
    agency_deadline_non_jurisdictional: bool = False

    def is_open(self, at: datetime) -> bool:
        return self.opens_at <= at <= self.closes_at

    def days_remaining(self, at: datetime) -> int:
        return max(0, (self.closes_at - at).days)


#: Water Code 1122. Both the petition window and the Board's response window.
RECONSIDERATION_DAYS = 30
BOARD_RESPONSE_DAYS = 90
#: Water Code 1126(b).
JUDICIAL_REVIEW_DAYS = 30
#: 23 CCR 875.6, "not less than seven (7) days".
MINIMUM_CERTIFICATION_DAYS = 7
#: 23 CCR 875.8, "not less than five (5) days".
MINIMUM_INFORMATION_RESPONSE_DAYS = 5

#: The delegation that pulls these orders inside the 1126(b) exception.
DELEGATION_RESOLUTION = "State Water Board Resolution No. 2012-0061"


def clocks_for_order(
    adopted_at: datetime,
    *,
    signatory: SignatoryRole,
    certification_days: int | None = None,
    information_response_days: int | None = None,
) -> list[Clock]:
    """Every statutory clock an adopted order starts.

    Args:
        adopted_at: The adoption timestamp. Everything below runs from it, which
            is why the Season Ledger stores it immutably.
        signatory: Who signed. Decides whether exhaustion is required.
        certification_days: The window the Deputy Director specified, if any.
            The regulation sets a floor, not a fixed value.
        information_response_days: Same, for an information order.

    Raises:
        ValueError: if a specified window is below the statutory floor, or if
            the timestamp is naive. A naive timestamp in a legal deadline is a
            silent off-by-hours error nobody catches until it matters.
    """
    if adopted_at.tzinfo is None:
        raise ValueError("adopted_at must be timezone-aware; a legal deadline cannot be naive")

    delegated = signatory in (SignatoryRole.DEPUTY_DIRECTOR, SignatoryRole.EXECUTIVE_DIRECTOR)

    clocks = [
        Clock(
            clock_type=ClockType.RECONSIDERATION_PETITION,
            opens_at=adopted_at,
            closes_at=adopted_at + timedelta(days=RECONSIDERATION_DAYS),
            citation="Water Code 1122",
            description=(
                "A petition for reconsideration shall be filed not later than 30 days "
                "from the date the board adopts the decision or order."
            ),
            exhaustion_required=delegated,
        ),
        Clock(
            clock_type=ClockType.BOARD_RESPONSE,
            opens_at=adopted_at,
            closes_at=adopted_at + timedelta(days=BOARD_RESPONSE_DAYS),
            citation="Water Code 1122",
            description=(
                "The board shall order or deny reconsideration not later than 90 days "
                "from the date it adopts the decision or order. The 90-day deadline is "
                "in section 1122, not 1123."
            ),
            agency_deadline_non_jurisdictional=True,
        ),
        Clock(
            clock_type=ClockType.JUDICIAL_REVIEW,
            opens_at=adopted_at,
            closes_at=adopted_at + timedelta(days=JUDICIAL_REVIEW_DAYS),
            citation="Water Code 1126(b)",
            description=(
                "A petition for writ of mandate must be filed not later than 30 days "
                "from final action. Because this order issued under authority delegated "
                f"by {DELEGATION_RESOLUTION}, it falls inside the delegation exception "
                "and reconsideration is a mandatory prerequisite."
                if delegated
                else "A petition for writ of mandate must be filed not later than 30 days "
                "from final action."
            ),
            exhaustion_required=delegated,
        ),
    ]

    if certification_days is not None:
        if certification_days < MINIMUM_CERTIFICATION_DAYS:
            raise ValueError(
                f"certification window of {certification_days} days is below the "
                f"{MINIMUM_CERTIFICATION_DAYS}-day floor in 23 CCR 875.6"
            )
        clocks.append(
            Clock(
                clock_type=ClockType.CERTIFICATION,
                opens_at=adopted_at,
                closes_at=adopted_at + timedelta(days=certification_days),
                citation="23 CCR 875.6",
                description=(
                    "Certification within the timeframe specified by the Deputy Director, "
                    "but not less than seven days."
                ),
            )
        )

    if information_response_days is not None:
        if information_response_days < MINIMUM_INFORMATION_RESPONSE_DAYS:
            raise ValueError(
                f"information response window of {information_response_days} days is below "
                f"the {MINIMUM_INFORMATION_RESPONSE_DAYS}-day floor in 23 CCR 875.8"
            )
        clocks.append(
            Clock(
                clock_type=ClockType.INFORMATION_ORDER_RESPONSE,
                opens_at=adopted_at,
                closes_at=adopted_at + timedelta(days=information_response_days),
                citation="23 CCR 875.8",
                description="Response to an information order, not less than five days.",
            )
        )

    return clocks


def open_clocks(clocks: list[Clock], at: datetime | None = None) -> list[Clock]:
    """Clocks still running at a moment. Defaults to now, in UTC."""
    moment = at or datetime.now(UTC)
    return [c for c in clocks if c.is_open(moment)]


# ---------------------------------------------------------------------------
# Service. Formal service and notification are legally different acts.
# ---------------------------------------------------------------------------


class ServiceMethod(StrEnum):
    """The four methods permitted by Water Code 1121, as amended by SB 756.

    Four, not two. The statute permits personal delivery, certified mail,
    service in the manner of a summons under CCP Article 3 or 4, or any method
    of physical delivery that provides a receipt, which expressly includes
    physical delivery methods providing electronic confirmation of delivery.
    """

    PERSONAL_DELIVERY = "personal_delivery"
    CERTIFIED_MAIL = "certified_mail"
    CCP_SUMMONS_MANNER = "ccp_summons_manner"
    RECEIPTED_PHYSICAL_DELIVERY = "receipted_physical_delivery"


class ServiceLane(StrEnum):
    """Which channel a communication travels, and what it legally accomplishes.

    LEGAL_SERVICE carries initial orders and any addendum that reinstates or
    newly imposes curtailment. NOTIFICATION carries drought updates,
    suspensions, reinstatements and rescissions under 875(d)(2) and 875(e), by
    email distribution list and website posting.

    A green "delivered" in the notification lane must never be displayed as
    legal service. They are not the same act and conflating them in a UI would
    let an operator believe a party was served when they were merely emailed.
    """

    LEGAL_SERVICE = "legal_service"
    NOTIFICATION = "notification"


class RecipientClass(StrEnum):
    """Water Code 1121 serves "the parties". Section 875(d)(1) reaches further.

    The initial order goes to each water right holder, claimant or agent of
    record, and places an onward-notice duty on that recipient to immediately
    notify all diverters exercising the right.
    """

    PARTY = "party"
    OPERATIONAL_DIVERTER = "operational_diverter"


@dataclass(frozen=True, slots=True)
class ServiceRecord:
    """Proof that a communication went out, and what it legally accomplished."""

    order_id: str
    recipient_id: str
    recipient_class: RecipientClass
    lane: ServiceLane
    method: ServiceMethod | None
    sent_at: datetime
    delivered_at: datetime | None = None
    receipt_reference: str | None = None

    @property
    def constitutes_legal_service(self) -> bool:
        """True only when this actually effected service under 1121.

        Requires the legal lane, a permitted method, and proof of delivery.
        An email send in the notification lane is never legal service, however
        green its delivery indicator looks.
        """
        return (
            self.lane is ServiceLane.LEGAL_SERVICE
            and self.method is not None
            and self.delivered_at is not None
            and self.receipt_reference is not None
        )


def lane_for_action(action: str) -> ServiceLane:
    """Which lane an order action belongs in.

    Reinstating and newly imposing curtailment are treated as candidate orders
    requiring legal service, because whether an addendum is itself an "order"
    triggering fresh 1121 service and a fresh 30-day clock is genuinely
    unadjudicated. No Board guidance, adopted order, or court decision resolves
    it, so this designs defensively: capture an immutable adoption timestamp,
    retain verifiable distribution records, start the clock, and support
    receipted output.
    """
    imposing = {"initial_order", "reinstatement", "new_curtailment", "extension"}
    return ServiceLane.LEGAL_SERVICE if action in imposing else ServiceLane.NOTIFICATION
