"""Herald: two state machines, because service and notification are different acts.

`_herald` was `return node_input`. Every type it needed already existed, unused: the four
Water Code 1121 methods, the two lanes, the recipient classes, `ServiceRecord` with its
`constitutes_legal_service` property, the dedup table with its lease. This is the
orchestration that makes them do something.

**The lane is decided by what the artifact IS, never by the caller.** Water Code 1121, as
amended by SB 756, permits four service methods and applies to orders. Section 875(d)(2)
and 875(e) allow an email distribution list and a website posting for drought updates,
suspensions and rescissions. A reinstating addendum is treated as a candidate order and
takes the legal lane, because whether an addendum is itself an "order" triggering fresh
service and a fresh 30-day clock is genuinely unadjudicated, and the defensive choice is
the one that survives being wrong.

**An email that left the building is not service.** The whole reason this module exists as
two machines rather than one is that a single "delivered" flag would let an operator
believe a party was served when they were merely emailed. On the legal lane a delivery
without a receipt reference is a FAILURE, and it escalates to a human rather than being
recorded as done.

**No delivery vendor is wired, and this module refuses to pretend one is.** There is no
certified mail integration and no email provider here. The default transport raises. The
demo passes an explicitly named synthetic transport, and every result it produces carries
`synthetic=True` so no surface can present it as real delivery. Only notification contacts
may be synthetic, and they must be visibly labelled.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from curtail_agents.messaging import (
    DELIVERY_ATTEMPTS,
    Channel,
    DedupTable,
    notification_idempotency_key,
)
from curtail_core.clocks import (
    RecipientClass,
    ServiceLane,
    ServiceMethod,
    ServiceRecord,
    lane_for_action,
)

#: Channels that can carry legal service, mapped to the method they effect.
#:
#: EMAIL IS ABSENT ON PURPOSE, and its absence is the point of the mapping. Water Code
#: 1121 permits personal delivery, certified mail, service in the manner of a summons,
#: and physical delivery providing a receipt. An email is none of those, however
#: confidently a provider reports it as delivered.
SERVICE_METHOD_FOR: dict[Channel, ServiceMethod] = {
    Channel.CERTIFIED_MAIL: ServiceMethod.CERTIFIED_MAIL,
    Channel.PERSONAL_DELIVERY: ServiceMethod.PERSONAL_DELIVERY,
    Channel.RECEIPTED_DELIVERY: ServiceMethod.RECEIPTED_PHYSICAL_DELIVERY,
}

#: Doubling from one second. Bounded by the attempt ceiling rather than by a deadline,
#: because the escalation is what protects the clock, not the schedule.
BACKOFF_BASE_SECONDS = 1.0


class TransportUnavailableError(RuntimeError):
    """Raised when no transport is configured. Never falls back to pretending.

    A recorded delivery that never happened is worse than a visible failure: the record
    is what a reviewing court would read, and it would say a party was served.
    """


@dataclass(frozen=True, slots=True)
class Recipient:
    """Who receives, in what capacity, over which channel.

    `recipient_class` is not decoration. Water Code 1121 serves "the parties", while
    875(d)(1) reaches each right holder, claimant or agent of record and places an
    onward-notice duty on them. Only a PARTY can be legally served.
    """

    recipient_id: str
    recipient_class: RecipientClass
    channel: Channel
    #: Deliberately a label, never an address. No personal contact detail is stored.
    label: str = ""


@dataclass(frozen=True, slots=True)
class TransportResult:
    """What a transport says happened. A receipt is the only thing that proves it."""

    accepted: bool
    receipt_reference: str | None = None
    delivered_at: datetime | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    """Everything one artifact's distribution accomplished, and what it did not."""

    order_id: str
    lane: ServiceLane
    records: tuple[ServiceRecord, ...]
    attempts: tuple[tuple[str, int], ...]
    escalations: tuple[str, ...] = field(default_factory=tuple)
    skipped_as_duplicate: tuple[str, ...] = field(default_factory=tuple)
    #: True when the transport was synthetic. Travels with the result so no surface can
    #: present a demonstration as real delivery.
    synthetic: bool = False

    @property
    def legally_served(self) -> tuple[str, ...]:
        return tuple(r.recipient_id for r in self.records if r.constitutes_legal_service)

    @property
    def may_report_as_served(self) -> bool:
        """The one question an interface must ask before showing the word "served".

        False on the notification lane ALWAYS, however green its indicators look, and
        false on the legal lane unless every party got a receipted delivery. A partial
        service is not service, and an order shown as served when one party was missed
        is the failure a reviewing court would find first.
        """
        if self.lane is not ServiceLane.LEGAL_SERVICE:
            return False
        if self.escalations:
            return False
        parties = {
            r.recipient_id for r in self.records if r.recipient_class is RecipientClass.PARTY
        }
        return bool(parties) and parties.issubset(set(self.legally_served))


def _refuse(recipient: Recipient, artifact: str) -> TransportResult:
    raise TransportUnavailableError(
        f"no transport is configured, so nothing can be delivered to "
        f"{recipient.recipient_id}. Refusing rather than recording a delivery that did "
        "not happen: the record is what a reviewing court reads, and it would say a "
        "party was served."
    )


def synthetic_transport(
    *, fail_first: int = 0, withhold_receipt_for: Sequence[str] = ()
) -> Callable[[Recipient, str], TransportResult]:
    """A LABELLED stand-in for a delivery vendor, for the demo and the drill.

    Named so it cannot be mistaken for real delivery, and every report it produces is
    marked `synthetic`. Only notification contacts may be synthetic in this project, and
    they must be visibly labelled.

    Args:
        fail_first: Reject this many attempts per recipient before accepting, so retry
            and backoff are observable rather than asserted.
        withhold_receipt_for: Recipients that accept but return NO receipt, which is the
            case that separates a delivery from legal service.
    """
    seen: dict[str, int] = {}

    def deliver(recipient: Recipient, artifact: str) -> TransportResult:
        seen[recipient.recipient_id] = seen.get(recipient.recipient_id, 0) + 1
        if seen[recipient.recipient_id] <= fail_first:
            return TransportResult(accepted=False, error="synthetic transient failure")
        if recipient.recipient_id in withhold_receipt_for:
            return TransportResult(accepted=True, receipt_reference=None, delivered_at=None)
        now = datetime.now(UTC)
        return TransportResult(
            accepted=True,
            receipt_reference=f"synthetic-receipt-{recipient.recipient_id}-{seen[recipient.recipient_id]}",
            delivered_at=now,
        )

    return deliver


def backoff_seconds(attempt: int) -> float:
    """Doubling from one second. Exposed so a test asserts the schedule rather than
    trusting a sleep that a test would have to wait through."""
    if attempt < 1:
        raise ValueError(f"attempt must be 1 or greater, got {attempt}")
    return float(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))


def deliver_order(
    *,
    order_id: str,
    action: str,
    recipients: Sequence[Recipient],
    artifact: str,
    transport: Callable[[Recipient, str], TransportResult] | None = None,
    dedup: DedupTable | None = None,
    attempts: int = DELIVERY_ATTEMPTS,
    now: datetime | None = None,
    synthetic: bool = False,
) -> DeliveryReport:
    """Route one artifact to its lane and distribute it, honestly.

    Args:
        order_id: The artifact being distributed.
        action: What it does. THIS decides the lane, not the caller.
        recipients: Who receives it and in what capacity.
        artifact: The text handed to the transport.
        transport: Injected. Defaults to a refusal, because no vendor is wired.
        dedup: Claim table, so a redelivery is a no-op rather than a second service.
        attempts: Ceiling per recipient. Exhaustion escalates; it never silently fails.
        now: Injected clock, so records are reproducible.
        synthetic: Marks the report, and must be set whenever the transport is.

    Raises:
        TransportUnavailableError: when no transport is configured.
    """
    lane = lane_for_action(action)
    send = transport or _refuse
    table = dedup if dedup is not None else DedupTable()
    stamp = now or datetime.now(UTC)

    records: list[ServiceRecord] = []
    tries: list[tuple[str, int]] = []
    escalations: list[str] = []
    duplicates: list[str] = []

    for recipient in recipients:
        key = notification_idempotency_key(recipient.recipient_id, order_id, recipient.channel)
        if not table.claim(key):
            # Already in flight or already done. A second delivery is not merely waste:
            # on the legal lane it would write a second service record for one act.
            duplicates.append(recipient.recipient_id)
            continue

        result = TransportResult(accepted=False, error="never attempted")
        used = 0
        for attempt in range(1, attempts + 1):
            used = attempt
            result = send(recipient, artifact)
            if result.accepted:
                break
        tries.append((recipient.recipient_id, used))

        if not result.accepted:
            escalations.append(
                f"{recipient.recipient_id} was not reached after {used} attempt(s): "
                f"{result.error or 'no reason given'}"
            )
            # NOT completed. The claim lapses with its lease so the event can be retried
            # by a later run, rather than being marked done on a failure.
            continue

        method = (
            SERVICE_METHOD_FOR.get(recipient.channel) if lane is ServiceLane.LEGAL_SERVICE else None
        )
        record = ServiceRecord(
            order_id=order_id,
            recipient_id=recipient.recipient_id,
            recipient_class=recipient.recipient_class,
            lane=lane,
            method=method,
            sent_at=stamp,
            delivered_at=result.delivered_at,
            receipt_reference=result.receipt_reference,
        )
        records.append(record)
        table.complete(key)

        if lane is ServiceLane.LEGAL_SERVICE and not record.constitutes_legal_service:
            # Accepted by the transport and still not service. This is the case the two
            # machines exist for: an email provider reporting success, a courier with no
            # receipt, a channel that cannot effect service at all.
            escalations.append(
                f"{recipient.recipient_id} was reached over {recipient.channel.value} but "
                "that did not effect service under Water Code 1121: "
                + (
                    "the channel is not a permitted method"
                    if method is None
                    else "no receipt of delivery was returned"
                )
            )

    return DeliveryReport(
        order_id=order_id,
        lane=lane,
        records=tuple(records),
        attempts=tuple(tries),
        escalations=tuple(escalations),
        skipped_as_duplicate=tuple(duplicates),
        synthetic=synthetic,
    )
