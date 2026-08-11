"""The Season Ledger: statutory clocks that outlive the process that started them.

The track asks for a system that "safely maintains context across weeks of
asynchronous operations." That is easy to claim and easy to fake, because an
in-memory dict satisfies every unit test ever written about it and loses everything
the moment a Cloud Run instance is recycled. So the ledger is defined by what
survives a restart, and the tests prove it by building a SECOND session service
against the same database and reading the record back through it.

**What makes this a mechanism rather than an adjective.** A curtailment order starts
five statutory clocks the moment it is adopted: a 30-day reconsideration window and
a 90-day Board response window under Water Code 1122, a 30-day judicial review
window under 1126(b), a certification deadline of at least 7 days under 23 CCR
875.6, and an information-order response window of at least 5 days under 875.8.
Those run in real calendar time while nobody is looking. A watermaster who misses
the 30-day petition window has lost the right to challenge, and because these orders
issue under delegated authority, reconsideration is a mandatory prerequisite to
judicial review rather than an optional first step. Weeks of asynchronous state is
not a feature here, it is the domain.

**Append-only, and it refuses rather than overwrites.** An adoption timestamp is
legal evidence: every deadline above is measured from it. A ledger that let a second
write move it would let a bug silently extend or extinguish a party's rights.

**Stored clocks, plus drift detection, and this is the design decision worth
reading.** The deadline that governs an order is the one the law set when it was
adopted. Recomputing from today's constants would silently rewrite history the
moment a statute is amended, which is the same defect as scoring a 2021 order
against the 2024 flow table. So the computed values are STORED as facts of record.
But a bug frozen forever is its own failure, so `drift_for` recomputes and REPORTS
disagreement instead of resolving it. That mirrors how this project handles the
875(b)(3) cross-reference defect: preserve what was enacted, attach the
interpretation, and make the discrepancy visible in the audit record rather than
quietly picking a winner.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from curtail_core.clocks import (
    Clock,
    ClockType,
    ServiceLane,
    ServiceMethod,
    ServiceRecord,
    SignatoryRole,
    clocks_for_order,
    judicial_review_clock,
)
from curtail_core.clocks import RecipientClass as CoreRecipientClass

#: Where the ledger lives in ADK session state.
#:
#: State, deliberately, and the contrast with `fleet.SOURCE_DOCUMENT` is the point.
#: Untrusted document text is BANNED from state because nodes bind parameters from
#: it by name and can read it by accident. The ledger is the opposite kind of thing:
#: a governed record every node is entitled to see, whose whole purpose is to
#: outlive the invocation that wrote it. Same store, opposite rules, because the
#: question is never "is state safe" but "safe for what".
LEDGER_STATE_KEY = "season_ledger"


class LcsLifecycle(StrEnum):
    """Where a Local Cooperative Solution has got to.

    A lifecycle, never a boolean. The instrument under 23 CCR 875(f) is petitioned,
    noticed, objected to, approved, conditioned, monitored and revocable, and a
    single `lcs_exemption` flag cannot express a petition that was filed and
    refused, or an approval later rescinded. Both of those change who gets curtailed.
    """

    PETITIONED = "petitioned"
    NOTICED = "noticed"
    OBJECTED = "objected"
    APPROVED = "approved"
    CONDITIONED = "conditioned"
    MONITORED = "monitored"
    RESCINDED = "rescinded"


class LedgerIntegrityError(RuntimeError):
    """An append would have altered a record that is already law.

    Raised rather than reconciled. Every statutory deadline runs from the adoption
    timestamp, so a silent overwrite could extend or extinguish a party's right to
    challenge an order, and neither is something a system should do quietly.
    """


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One adopted order or addendum, with everything time-bound about it."""

    order_id: str
    order_type: str
    adopted_at: datetime
    signatory: SignatoryRole
    clocks: tuple[Clock, ...]
    #: The discretionary windows the official actually chose.
    #:
    #: 23 CCR 875.6 and 875.8 set FLOORS, "not less than seven days" and "not less
    #: than five", so the number in a given order is a decision that official made
    #: and therefore a fact of the record rather than a constant.
    #:
    #: They are stored because drift detection needs the same INPUTS to recompute
    #: with, and the first version omitted them: an untouched entry created with a
    #: 7-day certification window reported drift against a recomputation that had
    #: no certification clock at all. A false positive in a drift detector is as
    #: damaging as a false negative, because a checker that cries wolf gets ignored
    #: and then the real drift goes unread.
    certification_days: int | None = None
    information_response_days: int | None = None
    #: When the Board finally acted, which is what starts the writ window.
    #:
    #: None until it happens, and that is the honest state rather than a gap. For a
    #: delegated order, reconsideration is a mandatory prerequisite, so final action
    #: is the Board's ruling on that petition and can fall up to 90 days after
    #: adoption. A judicial-review deadline computed at adoption would therefore
    #: report a window that had closed before it opened.
    final_action_at: datetime | None = None
    lcs_state: LcsLifecycle | None = None
    service_records: tuple[ServiceRecord, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("an order id is required to record an order")
        if self.adopted_at.tzinfo is None:
            raise ValueError(
                f"adopted_at for {self.order_id} is naive. Every deadline in this "
                "record is measured from it, and a timestamp with no zone is "
                "ambiguous by up to a day, which is a third of the reconsideration "
                "window."
            )

        # ONE CLOCK PER TYPE, enforced at construction rather than at the one call
        # site that got it wrong.
        #
        # A review found `with_final_action` appending a second judicial-review
        # clock to a Board order, which already carries one from adoption because a
        # non-delegated order IS its own final action. Two writ windows opening ten
        # days apart is not a question of which is right; it is a record that
        # contradicts itself, and `open_clocks` would have shown a watermaster both.
        #
        # Fixing only that function would leave the next path free to do it again,
        # so the invariant lives on the type. Any construction that duplicates a
        # clock type now fails immediately, including one nobody has written yet.
        seen: set[ClockType] = set()
        for clock in self.clocks:
            if clock.clock_type in seen:
                raise LedgerIntegrityError(
                    f"{self.order_id} carries two {clock.clock_type.value} clocks. "
                    "A single order cannot have two versions of one statutory "
                    "deadline: whichever a reader picked, the other would be a "
                    "deadline nobody was watching."
                )
            seen.add(clock.clock_type)

    def open_clocks(self, at: datetime) -> tuple[Clock, ...]:
        return tuple(c for c in self.clocks if c.is_open(at))

    @property
    def exhaustion_required(self) -> bool:
        """True when reconsideration is a prerequisite to judicial review.

        Read off the stored clocks rather than recomputed from the signatory, so
        the record answers for itself.
        """
        return any(c.exhaustion_required for c in self.clocks)

    @property
    def legally_served(self) -> bool:
        """True only if some record actually effected service under 1121.

        A green delivery indicator in the notification lane is not service, and
        this property is where that distinction stops being a comment.
        """
        return any(r.constitutes_legal_service for r in self.service_records)


def _clock_to_dict(clock: Clock) -> dict[str, Any]:
    return {
        "clock_type": clock.clock_type.value,
        "opens_at": clock.opens_at.isoformat(),
        "closes_at": clock.closes_at.isoformat(),
        "citation": clock.citation,
        "description": clock.description,
        "exhaustion_required": clock.exhaustion_required,
        "agency_deadline_non_jurisdictional": clock.agency_deadline_non_jurisdictional,
    }


def _clock_from_dict(raw: dict[str, Any]) -> Clock:
    return Clock(
        clock_type=ClockType(raw["clock_type"]),
        opens_at=datetime.fromisoformat(raw["opens_at"]),
        closes_at=datetime.fromisoformat(raw["closes_at"]),
        citation=raw["citation"],
        description=raw["description"],
        exhaustion_required=bool(raw["exhaustion_required"]),
        agency_deadline_non_jurisdictional=bool(raw["agency_deadline_non_jurisdictional"]),
    )


def _service_to_dict(record: ServiceRecord) -> dict[str, Any]:
    return {
        "order_id": record.order_id,
        "recipient_id": record.recipient_id,
        "recipient_class": record.recipient_class.value,
        "lane": record.lane.value,
        "method": record.method.value if record.method else None,
        "sent_at": record.sent_at.isoformat(),
        "delivered_at": record.delivered_at.isoformat() if record.delivered_at else None,
        "receipt_reference": record.receipt_reference,
    }


def _service_from_dict(raw: dict[str, Any]) -> ServiceRecord:
    return ServiceRecord(
        order_id=raw["order_id"],
        recipient_id=raw["recipient_id"],
        recipient_class=CoreRecipientClass(raw["recipient_class"]),
        lane=ServiceLane(raw["lane"]),
        method=ServiceMethod(raw["method"]) if raw["method"] else None,
        sent_at=datetime.fromisoformat(raw["sent_at"]),
        delivered_at=(datetime.fromisoformat(raw["delivered_at"]) if raw["delivered_at"] else None),
        receipt_reference=raw["receipt_reference"],
    )


def entry_to_dict(entry: LedgerEntry) -> dict[str, Any]:
    """Render one entry as plain JSON-safe values.

    Plain values, because session state is persisted by a real service and a live
    dataclass does not survive the trip. Learned the hard way one module over,
    where an `Observation` in state worked perfectly until it met a session
    service that had to write it down.
    """
    return {
        "order_id": entry.order_id,
        "order_type": entry.order_type,
        "adopted_at": entry.adopted_at.isoformat(),
        "signatory": entry.signatory.value,
        "clocks": [_clock_to_dict(c) for c in entry.clocks],
        "certification_days": entry.certification_days,
        "information_response_days": entry.information_response_days,
        "final_action_at": entry.final_action_at.isoformat() if entry.final_action_at else None,
        "lcs_state": entry.lcs_state.value if entry.lcs_state else None,
        "service_records": [_service_to_dict(r) for r in entry.service_records],
    }


def entry_from_dict(raw: dict[str, Any]) -> LedgerEntry:
    return LedgerEntry(
        order_id=raw["order_id"],
        order_type=raw["order_type"],
        adopted_at=datetime.fromisoformat(raw["adopted_at"]),
        signatory=SignatoryRole(raw["signatory"]),
        clocks=tuple(_clock_from_dict(c) for c in raw["clocks"]),
        certification_days=raw["certification_days"],
        information_response_days=raw["information_response_days"],
        final_action_at=(
            datetime.fromisoformat(raw["final_action_at"]) if raw["final_action_at"] else None
        ),
        lcs_state=LcsLifecycle(raw["lcs_state"]) if raw["lcs_state"] else None,
        service_records=tuple(_service_from_dict(r) for r in raw["service_records"]),
    )


def record_order(
    adopted_at: datetime,
    *,
    order_id: str,
    order_type: str,
    signatory: SignatoryRole,
    certification_days: int | None = None,
    information_response_days: int | None = None,
    lcs_state: LcsLifecycle | None = None,
) -> LedgerEntry:
    """Compute an order's clocks once, at adoption, and freeze them into an entry."""
    clocks = clocks_for_order(
        adopted_at,
        signatory=signatory,
        certification_days=certification_days,
        information_response_days=information_response_days,
    )
    return LedgerEntry(
        order_id=order_id,
        order_type=order_type,
        adopted_at=adopted_at,
        signatory=signatory,
        clocks=tuple(clocks),
        certification_days=certification_days,
        information_response_days=information_response_days,
        lcs_state=lcs_state,
    )


def append(entries: Sequence[LedgerEntry], new: LedgerEntry) -> tuple[LedgerEntry, ...]:
    """Add an entry. Refuses to replace one that already exists.

    Append-only is not a stylistic preference here. Every deadline in an entry runs
    from its adoption timestamp, so silently replacing a record could move or
    extinguish a party's window to challenge the order.
    """
    if any(e.order_id == new.order_id for e in entries):
        raise LedgerIntegrityError(
            f"{new.order_id} is already in the ledger. Its adoption timestamp is "
            "what every statutory deadline is measured from, so it is never "
            "rewritten. Record a superseding order under its own id instead."
        )
    return (*entries, new)


def with_final_action(entry: LedgerEntry, final_action_at: datetime) -> LedgerEntry:
    """Record the Board's final action, which is what starts the writ window.

    A separate legal event with its own timestamp, not a correction to the adoption
    record. The judicial-review clock is added HERE rather than at adoption because
    until this moment its triggering event has not happened, and a deadline computed
    from the wrong event told a party their right to review had expired before it
    opened.

    Refuses on an order that already has a writ window, which for a non-delegated
    order means one it has carried since adoption: with no exhaustion required the
    order IS its own final action, so there is no separate later event to record and
    asking to record one means the caller has the wrong model of the order.

    Refuses to move a final action already recorded, for the same reason the ledger
    refuses to replace an entry: the date a window opened is not something a later
    write should be able to change.
    """
    if final_action_at.tzinfo is None:
        raise ValueError("final_action_at must be timezone aware; a legal deadline cannot be naive")
    if any(c.clock_type is ClockType.JUDICIAL_REVIEW for c in entry.clocks):
        raise LedgerIntegrityError(
            f"{entry.order_id} already carries a judicial-review window. A "
            f"{entry.signatory.value} order that required no exhaustion is its own "
            "final action, so its writ window opened at adoption and there is no "
            "separate later event to record."
        )
    if entry.final_action_at is not None:
        raise LedgerIntegrityError(
            f"{entry.order_id} already records final action at "
            f"{entry.final_action_at.isoformat()}. The writ window runs from it, so "
            "moving it would move a party's deadline."
        )
    if final_action_at < entry.adopted_at:
        raise LedgerIntegrityError(
            f"final action at {final_action_at.isoformat()} precedes adoption at "
            f"{entry.adopted_at.isoformat()}. The Board cannot finally act on an "
            "order before adopting it, so one of the two timestamps is wrong."
        )
    import dataclasses

    return dataclasses.replace(
        entry,
        final_action_at=final_action_at,
        clocks=(
            *entry.clocks,
            judicial_review_clock(final_action_at, delegated=entry.exhaustion_required),
        ),
    )


def to_state(entries: Iterable[LedgerEntry]) -> list[dict[str, Any]]:
    """The value to put in session state under `LEDGER_STATE_KEY`."""
    return [entry_to_dict(e) for e in entries]


@dataclass(frozen=True, slots=True)
class QuarantinedEntry:
    """A stored entry that could not be read, kept with the reason it could not."""

    raw: dict[str, Any]
    reason: str

    @property
    def order_id(self) -> str:
        """Whatever the record claims to be, for a human to go and look at."""
        claimed = self.raw.get("order_id")
        return str(claimed) if claimed else "<no order_id in the stored record>"


@dataclass(frozen=True, slots=True)
class LoadedLedger:
    """What came back, and what could not be read.

    Two fields rather than one, because the alternative designs are both wrong.
    Raising on the first bad entry takes the WHOLE season offline: nineteen sound
    records with running deadlines become invisible because the twentieth is
    malformed, which is the same harm as reading the ledger as empty. Silently
    skipping the bad one is worse still, since the deadline it carried then goes
    unwatched with nothing to show a human.

    So the load succeeds, the damage is itemised, and `is_complete` makes the
    difference impossible to pass over by accident.
    """

    entries: tuple[LedgerEntry, ...]
    quarantined: tuple[QuarantinedEntry, ...] = field(default_factory=tuple)

    @property
    def is_complete(self) -> bool:
        return not self.quarantined

    def summary(self) -> str:
        """One line for an operator, naming every record that needs a human."""
        if self.is_complete:
            return f"{len(self.entries)} orders, all readable"
        damaged = ", ".join(f"{q.order_id} ({q.reason})" for q in self.quarantined)
        return (
            f"{len(self.entries)} orders readable, {len(self.quarantined)} QUARANTINED "
            f"and their deadlines are NOT being tracked: {damaged}"
        )


def from_state(raw: Any) -> LoadedLedger:
    """Read the ledger back out of session state, quarantining what will not load.

    Tolerates absence, because a season that has recorded nothing yet is a valid
    state and not an error. Does NOT tolerate a corrupt CONTAINER: a ledger that
    silently reads back empty because its stored form changed would report "no open
    deadlines" for an order whose reconsideration window is running, which is the
    most dangerous wrong answer this system can give.

    **Per ENTRY, though, it quarantines rather than aborting**, and a review is why.
    The constructor now rejects duplicate clock types, and an eager tuple
    comprehension meant one such record would abort the whole read: every sound
    order in the same season would vanish along with it. Turning a fix for deadline
    tracking into a deadline-tracking outage is a poor trade.

    On legacy data specifically: there is none. This ledger has never been wired to
    an order-adoption path and has never been deployed, so no stored record can
    predate the invariant, and a versioned migration would be machinery for zero
    rows. Quarantine is the recovery path that generalises anyway: a record from any
    cause that will not load is surfaced with its reason rather than dropped.
    """
    if raw is None:
        return LoadedLedger(entries=())
    if isinstance(raw, str):
        # A session service may round-trip the value as JSON text.
        raw = json.loads(raw)
    if not isinstance(raw, list):
        raise LedgerIntegrityError(
            f"the ledger in session state is a {type(raw).__name__}, not a list. "
            "Reading it as empty would report no open deadlines for orders whose "
            "clocks are running."
        )

    entries: list[LedgerEntry] = []
    damaged: list[QuarantinedEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            damaged.append(
                QuarantinedEntry(raw={}, reason=f"stored as {type(item).__name__}, not an object")
            )
            continue
        try:
            entries.append(entry_from_dict(item))
        except (LedgerIntegrityError, ValueError, KeyError, TypeError) as exc:
            damaged.append(QuarantinedEntry(raw=item, reason=f"{type(exc).__name__}: {exc}"))
    return LoadedLedger(entries=tuple(entries), quarantined=tuple(damaged))


def open_clocks(
    entries: Sequence[LedgerEntry], at: datetime | None = None
) -> tuple[tuple[str, Clock], ...]:
    """Every deadline still running, across the whole season, with its order id."""
    moment = at or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("`at` must be timezone aware; a naive moment cannot be compared")
    return tuple(
        (entry.order_id, clock) for entry in entries for clock in entry.open_clocks(moment)
    )


def drift_for(entry: LedgerEntry) -> tuple[str, ...]:
    """Where the STORED clocks disagree with what today's constants would compute.

    **Reports rather than resolves, and that is deliberate.** Either answer alone is
    wrong. Recomputing on read would silently rewrite a historical deadline the
    moment a statute is amended, which is the same defect as scoring a 2021 order
    against the 2024 flow table. Trusting the stored value forever would freeze a
    computation bug into the legal record.

    So the discrepancy becomes visible in the audit trail and a human decides,
    exactly as this project handles the 875(b)(3) cross-reference defect: preserve
    what was enacted, attach the interpretation, and never quietly pick a winner.
    """
    # The SAME inputs the entry was built from, not just its timestamp and
    # signatory. Recomputing with fewer inputs invents drift that is not there.
    recomputed = {
        c.clock_type: c
        for c in clocks_for_order(
            entry.adopted_at,
            signatory=entry.signatory,
            certification_days=entry.certification_days,
            information_response_days=entry.information_response_days,
        )
    }
    # The judicial-review clock is not computed at adoption for a delegated order,
    # so its presence or absence is decided by whether final action was recorded and
    # is not drift. Comparing it against an adoption-time recomputation would report
    # the module's own correct behaviour as a discrepancy.
    if entry.final_action_at is not None:
        recomputed[ClockType.JUDICIAL_REVIEW] = judicial_review_clock(
            entry.final_action_at, delegated=entry.exhaustion_required
        )

    differences: list[str] = []
    for stored in entry.clocks:
        now_says = recomputed.get(stored.clock_type)
        if now_says is None:
            differences.append(
                f"{stored.clock_type.value}: stored, but today's rules compute no "
                f"such clock for a {entry.signatory.value} order"
            )
            continue
        if now_says.closes_at != stored.closes_at:
            differences.append(
                f"{stored.clock_type.value}: stored closes_at "
                f"{stored.closes_at.isoformat()}, today's rules say "
                f"{now_says.closes_at.isoformat()}"
            )
        if now_says.exhaustion_required != stored.exhaustion_required:
            differences.append(
                f"{stored.clock_type.value}: stored exhaustion_required "
                f"{stored.exhaustion_required}, today's rules say "
                f"{now_says.exhaustion_required}"
            )
    stored_types = {c.clock_type for c in entry.clocks}
    for clock_type in recomputed:
        if clock_type not in stored_types:
            differences.append(
                f"{clock_type.value}: today's rules compute this clock, but the "
                "record does not carry it"
            )
    return tuple(differences)
