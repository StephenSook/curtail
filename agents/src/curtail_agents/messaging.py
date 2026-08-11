"""Pub/Sub discipline: ordering keys, idempotency, and the dedup table.

Four things are judged here and each is a decision rather than a default.

**Pull subscriptions with exactly-once.** Exactly-once delivery is supported only
on pull, so the subscription type is not a preference. It is also not what makes
this safe, which is the point of the next paragraph.

**Exactly-once is a DELIVERY guarantee, not a PROCESSING one.** It bounds how
many times the broker hands you a message. It says nothing about whether your
handler already committed a side effect before it crashed, and nothing about the
same logical event arriving as two different messages. The plan for this task put
that on record as a prediction to be tested: the dedup table, not the flag, is
what makes redelivery a no-op. So application-level idempotency exists REGARDLESS
of the flag, and the tests below prove the table is what does the work.

**High-cardinality ordering keys.** Keyed by water right or by gage, never one
global key. A single key serialises the entire fleet behind its slowest message,
which turns a per-right retry into a basin-wide stall. Ordering also interacts
badly with dead-lettering: redelivery of a keyed message redelivers subsequent
messages with that key, and ordering may not survive the dead-letter path at all,
so a consumer must tolerate both.

**Dead-letter topics.** Between 5 and 100 delivery attempts, and the correlation
ID must survive into the dead letter or a failed message cannot be traced back to
the poll that produced it, which is the one thing the chaos drill demonstrates.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from curtail_core.basins import Basin

#: Pub/Sub's permitted range for a dead-letter policy. Outside it the
#: subscription is rejected at creation, so a value is checked here rather than
#: discovered on deploy.
MIN_DELIVERY_ATTEMPTS = 5
MAX_DELIVERY_ATTEMPTS = 100

#: What we actually configure. Five is the floor, and the floor is right for a
#: fleet whose retries are already bounded upstream by the routing guard: a
#: message that has failed five times is not going to succeed on the sixth, and
#: holding it longer delays the human who needs to see it.
DELIVERY_ATTEMPTS = 5


class Channel(StrEnum):
    """Where a notification goes. Part of a notification's identity.

    An email and an SMS carrying the same content to the same recipient are two
    deliveries, not one, so the channel belongs in the idempotency key. Leaving
    it out would let a successful email suppress the SMS that was meant to
    escalate it.
    """

    EMAIL = "email"
    SMS = "sms"
    CERTIFIED_MAIL = "certified_mail"
    WEBSITE_POSTING = "website_posting"
    #: The two remaining Water Code 1121 methods. Added when Herald needed to express
    #: them: this enum previously modelled notification channels plus certified mail,
    #: so a personally served order had no channel to be recorded under and would have
    #: been forced into one that cannot effect service.
    PERSONAL_DELIVERY = "personal_delivery"
    RECEIPTED_DELIVERY = "receipted_delivery"


class OrderType(StrEnum):
    IMPOSE = "impose"
    REINSTATE = "reinstate"
    SUSPEND = "suspend"
    RESCIND = "rescind"


def ordering_key_for_right(right_id: str) -> str:
    """Order messages about ONE water right, independently of every other right.

    Cardinality is the whole point. With thousands of rights this is thousands of
    independent ordered streams, so a retry on one ranch's notice cannot stall a
    neighbouring basin. A constant key here would be a correctness-preserving,
    throughput-destroying choice that only shows up under load.
    """
    if not right_id.strip():
        raise ValueError(
            "an ordering key derived from an empty right id would collapse every "
            "right onto one stream, which is the single-global-key failure wearing "
            "a different name"
        )
    return f"right:{right_id.strip()}"


def ordering_key_for_gage(basin: Basin) -> str:
    """Order gage readings per basin.

    Two basins, so two streams. Readings for one river must stay in order
    relative to each other, because a suspension followed by a reinstatement
    processed backwards releases water the Board just protected. Readings for
    DIFFERENT rivers have no such relationship and must not be coupled.
    """
    return f"gage:{basin.value}"


def _digest(*parts: str) -> str:
    """A stable, unambiguous identity for a logical event.

    **Length-prefixed, not separator-joined, and that was a real collision.**

    An earlier version joined on \\x1f and its comment claimed that hashing made
    free-text ids safe. It did not: joining is ambiguous whenever a component can
    contain the separator, so `("r\\x1fo", "x")` and `("r", "o\\x1fx")` produced
    the SAME pre-hash string and therefore the same key. Two distinct
    notifications, one identity, and the second silently deduplicated away. A
    notice that never reaches a rancher is recorded as sent.

    Length-prefixing removes the class rather than forbidding the character,
    because these ids come from eWRIMS and from migrated records and we do not
    control their contents.

    Truncated to 32 hex characters: 128 bits, far past any birthday concern for a
    corpus of thousands, and short enough to read in a log line.
    """
    canonical = "".join(f"{len(part)}:{part}" for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def order_idempotency_key(right_id: str, order_type: OrderType, effective_date: date) -> str:
    """Identity of one drafted order for one right.

    Right plus type plus effective date. Two drafts with these three equal are
    the same order however many times the pipeline produced them, and the second
    must be a no-op rather than a second signed document for the same ranch on
    the same day.
    """
    if not right_id.strip():
        raise ValueError("right_id is required for an order idempotency key")
    return _digest("order", right_id.strip(), order_type.value, effective_date.isoformat())


def notification_idempotency_key(recipient_id: str, order_id: str, channel: Channel) -> str:
    """Identity of one notification.

    Recipient plus order plus channel. The channel is load-bearing: the same
    notice by email and by SMS is two deliveries, and collapsing them would let a
    delivered email suppress the escalation that email failing was supposed to
    trigger.
    """
    if not recipient_id.strip() or not order_id.strip():
        raise ValueError("recipient_id and order_id are required")
    return _digest("notify", recipient_id.strip(), order_id.strip(), channel.value)


class ClaimState(StrEnum):
    """Where a claimed event got to.

    Two states, because one is not enough. A table recording only "seen" cannot
    tell a completed event from one whose worker claimed it and then crashed
    before doing anything, and it answers "already handled" to both. The order is
    then never produced while the system records it as processed, which is the
    quietest possible way to lose a legal document.
    """

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


#: How long a claim is held before another worker may take it over.
#:
#: A LEASE, and it is the only thing that reconciles two requirements that
#: otherwise contradict each other. Crash recovery wants an unfinished claim to
#: be retryable. Concurrency wants a claim held by a live worker to be exclusive.
#: Without a lease you must pick one, and picking either is a real defect:
#: permanent claims lose events to crashes, immediately-retryable claims let
#: every concurrent consumer through at once.
#:
#: Five minutes comfortably exceeds a Scribe draft plus a PDF render, and is well
#: under the ack deadline extension a long agent task would use.
CLAIM_LEASE_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class _Claim:
    state: ClaimState
    claimed_at: float


@dataclass
class DedupTable:
    """Records which logical events are in flight, and which finished.

    Deliberately separate from the delivery guarantee. Exactly-once bounds how
    often the BROKER hands you a message; this bounds how often the SYSTEM acts
    on one. They fail differently: a broker guarantee cannot help when the same
    event is republished by a retried upstream agent under a fresh message id,
    and it cannot help when a handler committed a side effect and then crashed
    before acknowledging.

    In-memory here, which is correct for tests and the emulator. The Cloud SQL
    implementation satisfies the same interface; `claim` returning a bool is the
    shape an INSERT ... ON CONFLICT DO UPDATE ... RETURNING reports.
    """

    lease_seconds: float = CLAIM_LEASE_SECONDS
    _claims: dict[str, _Claim] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    #: Injectable so lease expiry is tested without sleeping. Monotonic, because
    #: a wall clock that steps backwards would resurrect expired leases.
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def claim(self, idempotency_key: str) -> bool:
        """Claim an event. True means this caller should process it.

        **Genuinely atomic, under a lock.** An earlier version was a bare
        `if key in seen: ... seen.add(key)` whose docstring claimed an atomicity
        it did not have. CPython's GIL makes that race hard to observe, which is
        worse rather than better: the construction is unsafe and the tests could
        not see it.

        **Leased, not permanent.** The first repair of the crash case simply made
        an in-progress entry re-claimable, which fixed crash recovery and broke
        concurrency outright: 32 of 32 racing threads won. A lease resolves both.
        An unexpired claim is exclusive; an expired one is assumed abandoned and
        may be taken over; a completed one is never re-claimable.
        """
        if not idempotency_key:
            raise ValueError(
                "an empty idempotency key would claim nothing and let every "
                "duplicate through while the table reported success"
            )
        now = self._clock()
        with self._lock:
            existing = self._claims.get(idempotency_key)
            if existing is not None:
                if existing.state is ClaimState.COMPLETED:
                    return False
                if now - existing.claimed_at < self.lease_seconds:
                    # Held by a worker that is probably still alive.
                    return False
            self._claims[idempotency_key] = _Claim(ClaimState.IN_PROGRESS, now)
            return True

    def complete(self, idempotency_key: str) -> None:
        """Mark an event finished. Only now is a redelivery a true no-op.

        Called AFTER the side effect, never before. The window between claim and
        complete is exactly where a crash lands, and leaving the entry
        IN_PROGRESS is what lets the retry proceed once the lease lapses instead
        of being swallowed forever.
        """
        if not idempotency_key:
            raise ValueError("cannot complete an empty idempotency key")
        with self._lock:
            self._claims[idempotency_key] = _Claim(ClaimState.COMPLETED, self._clock())

    def state_of(self, idempotency_key: str) -> ClaimState | None:
        with self._lock:
            claim = self._claims.get(idempotency_key)
            return claim.state if claim else None

    def has_seen(self, idempotency_key: str) -> bool:
        """Read-only, and COMPLETED only. For assertions and the console.

        Never for gating: an in-progress entry must not read as handled.
        """
        with self._lock:
            claim = self._claims.get(idempotency_key)
            return claim is not None and claim.state is ClaimState.COMPLETED

    def __len__(self) -> int:
        with self._lock:
            return len(self._claims)


@dataclass(frozen=True, slots=True)
class SubscriptionConfig:
    """The settings a subscription must carry, validated before deploy.

    A dataclass rather than a call to the API, because the values are the
    judged artifact and they should be reviewable in a diff and testable without
    a project. The client call that applies them is a thin translation.
    """

    name: str
    topic: str
    dead_letter_topic: str
    max_delivery_attempts: int = DELIVERY_ATTEMPTS
    enable_exactly_once_delivery: bool = True
    enable_message_ordering: bool = True

    def __post_init__(self) -> None:
        if not self.dead_letter_topic:
            raise ValueError(
                f"subscription {self.name!r} has no dead-letter topic. A message "
                "that fails every attempt would be dropped silently, and a "
                "curtailment notice nobody received is indistinguishable from one "
                "nobody sent."
            )
        if not MIN_DELIVERY_ATTEMPTS <= self.max_delivery_attempts <= MAX_DELIVERY_ATTEMPTS:
            raise ValueError(
                f"max_delivery_attempts must be between {MIN_DELIVERY_ATTEMPTS} and "
                f"{MAX_DELIVERY_ATTEMPTS}; Pub/Sub rejects the subscription "
                f"otherwise. Got {self.max_delivery_attempts}."
            )
        if self.enable_exactly_once_delivery and not self.name:
            raise ValueError("a subscription needs a name")
