"""The approval queue: the Collaborative pattern, and the reason nothing self-executes.

Every entry in this field will have a human-in-the-loop checkbox. What makes one
worth anything is whether it is designed against the way human review actually
fails, and there is measured evidence for that failure. Biro et al., npj Digital
Medicine 8:222 (2025): twenty practicing physicians reviewed eighteen patient portal
messages, four of which contained errors, and **35 to 45 percent of erroneous drafts
were submitted entirely unedited**. Eighty percent of the same clinicians said the
drafts reduced their workload and seventy-five percent judged them safe. That
combination is the trap: the reviewers were confident and wrong, so a queue whose
only affordance is an approve button measures nothing but their confidence.

**Three mechanisms, each aimed at a specific way that failure happens.**

1. **An approval binds to the exact bytes reviewed.** The reviewer submits the
   digest of the draft they read. If the draft changed afterwards, the approval is
   void rather than transferred, so a re-drafted order cannot inherit the signature
   of the one a human actually looked at.
2. **Role gating, because the regulation draws the line and a generic human does
   not.** Section 875(b) assigns the curtailment determination to the Deputy
   Director; 875(b)(2) assigns human health and safety and minimum livestock
   watering determinations to the EXECUTIVE Director. An approval by the wrong
   officer is refused, not logged as a warning.
3. **A guard-escalated draft cannot be approved by the ordinary path.** When the
   routing guard flags a draft UNVERIFIED, approving it requires naming the
   violations being overridden. Clicking through an unverified legal order should
   cost more keystrokes than clicking through a verified one.

**The officer identity is VERIFIED, not asserted.** A review said twice that this
function minted a signed record from caller-supplied strings, so the role gate
protected nothing. The first answer was that a domain module cannot authenticate
anybody and the honest move was to label the gap. That was half right, and the wrong
half mattered: the boundary was declared unbuildable without anyone trying to build
it. `Officer` now comes only from `credentials.verify_officer_token`, which checks
an HMAC over the exact claim bytes with a key this layer does not hold, so forging
an approval requires the signing key rather than a function call.

**Approval produces a RECORD, never an action.** Nothing here sends, files, or
serves anything. That is hard rule 3 of the build constitution expressed as a type:
the queue's output is a `SignedDecision`, and no method on it does anything to the
world.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from curtail_agents.credentials import Officer, verify_officer_token
from curtail_agents.routing import GuardResult, Verdict
from curtail_core.clocks import SignatoryRole


class QueueState(StrEnum):
    """Where an item stands. Four states, and UNVERIFIED is not a kind of pending.

    A draft the guard rejected is a different thing from one merely awaiting a
    signature, and collapsing them is how an unverified legal order reaches a
    reviewer looking exactly like a clean one.
    """

    AWAITING_SIGNATURE = "awaiting_signature"
    UNVERIFIED = "unverified"
    SIGNED = "signed"
    DECLINED = "declined"


class ApprovalError(RuntimeError):
    """An approval was refused. The draft is unchanged and nothing was signed."""


def digest_of(draft_text: str) -> str:
    """The identity of the exact bytes a reviewer read.

    Length-prefixed like the messaging keys, for the same reason: a digest built by
    concatenation is ambiguous the moment any component can contain the separator,
    and two different drafts sharing an identity is precisely the confusion this
    exists to prevent.
    """
    payload = f"{len(draft_text)}:{draft_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class SignedDecision:
    """The record an approval produces. It is evidence, not an instruction.

    Nothing on this type acts. It names who decided, what exact draft they decided
    on, when, and what they were told at the time, which is the set of facts an
    administrative record has to survive scrutiny with.
    """

    order_id: str
    officer: Officer
    draft_digest: str
    decided_at: datetime
    approved: bool
    #: Violations the officer explicitly overrode, empty when the guard passed.
    overridden: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""

    @property
    def is_override(self) -> bool:
        return bool(self.overridden)


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One drafted order waiting for the officer the regulation names.

    Carries the guard's result rather than a boolean, so a reviewer sees WHICH
    checks failed instead of a colour. The per-right ledger is the other half of
    that and lives on the recommendation the draft was built from.
    """

    order_id: str
    draft_text: str
    requires_role: SignatoryRole
    guard: GuardResult
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.order_id.strip():
            raise ValueError("a queue item needs an order id")
        if not self.draft_text.strip():
            raise ValueError(
                f"{self.order_id} has an empty draft. An empty document cannot be "
                "reviewed, and an approval on one would record a signature against "
                "nothing."
            )
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone aware")

    @property
    def digest(self) -> str:
        return digest_of(self.draft_text)

    @property
    def state(self) -> QueueState:
        return (
            QueueState.AWAITING_SIGNATURE
            if self.guard.verdict is Verdict.PASS
            else QueueState.UNVERIFIED
        )

    @property
    def blocking_violations(self) -> tuple[str, ...]:
        """Everything an officer would have to override to sign this.

        Assembled from the guard's own findings rather than summarised, because a
        reviewer who is told "2 issues" learns nothing and one who is told which
        rights are unsupported can go and look.
        """
        problems: list[str] = []
        problems.extend(f"unsupported right: {r}" for r in self.guard.unsupported_rights)
        problems.extend(
            f"right reached by the Core but absent from the draft: {r}"
            for r in self.guard.missing_rights
        )
        problems.extend(
            f"unsupported priority date: {d.isoformat()}" for d in self.guard.unsupported_dates
        )
        if self.guard.extent_mismatch:
            problems.append(f"extent mismatch: {self.guard.extent_mismatch}")
        problems.extend(f"stripped citation: {c}" for c in self.guard.stripped_citations)
        problems.extend(self.guard.other_findings)
        return tuple(problems)


def sign(
    item: QueueItem,
    *,
    officer_token: str,
    key: bytes,
    reviewed_digest: str,
    approved: bool = True,
    overriding: tuple[str, ...] = (),
    note: str = "",
) -> SignedDecision:
    """Record an officer's decision, or refuse to.

    Args:
        reviewed_digest: The digest of the draft the officer actually read. Supplied
            by the caller rather than recomputed here, because recomputing it from
            the current draft would make the check tautological: it would always
            match, and the stale-draft case it exists to catch would pass silently.
        overriding: The violations the officer is knowingly accepting. Required, and
            required to be complete, when the draft is UNVERIFIED.

    Raises:
        ApprovalError: when the wrong officer signs, when the draft changed after
            review, or when an unverified draft is approved without naming what is
            being overridden.
    """
    # THE CLOCK IS READ HERE, and there is no parameter to override it.
    #
    # The previous version took `now` from the caller, and a review caught what that
    # meant: a token that expired in 2020 signed a curtailment order when the caller
    # supplied a convenient 2020 timestamp, and the resulting record was BACKDATED
    # to 2020 as well. Expiry evaluated against an attacker-chosen clock is not
    # expiry, and the previous commit message had already stated the principle it
    # then violated, that anything the caller supplies is something the caller can
    # choose.
    #
    # `verify_officer_token` keeps an injectable clock because it is a primitive its
    # own tests must drive. The AUTHORITY path is this function, and this function
    # does not offer the choice.
    now = datetime.now(UTC)
    officer = verify_officer_token(officer_token, key=key, now=now)
    decided_at = now

    if officer.role is not item.requires_role:
        raise ApprovalError(
            f"{item.order_id} requires the {item.requires_role.value} and was "
            f"signed by the {officer.role.value}. Section 875(b) assigns the "
            "curtailment determination to the Deputy Director and 875(b)(2) assigns "
            "health, safety and livestock determinations to the Executive Director, "
            "so the wrong officer's signature is not a lesser signature, it is none."
        )

    if reviewed_digest != item.digest:
        raise ApprovalError(
            f"{item.order_id}: the draft changed after it was reviewed. The officer "
            f"read {reviewed_digest} and the queue now holds {item.digest}. An "
            "approval is bound to the bytes it was given, so this one is void "
            "rather than carried over to a document nobody has read."
        )

    if approved and item.state is QueueState.UNVERIFIED:
        blocking = set(item.blocking_violations)
        named = set(overriding)
        if not blocking:
            # FAIL CLOSED. The guard refused the draft and then named nothing, so
            # there is no finding for an officer to weigh. A review found that this
            # let arbitrary override text approve a rejected draft: the subset check
            # below is vacuously satisfied when the blocking set is empty. A guard
            # that rejects without saying why is itself the defect, and signing past
            # it would launder that defect into an administrative record.
            raise ApprovalError(
                f"{item.order_id} is {item.state.value} but the guard named no "
                f"specific finding (reason: {item.guard.reason!r}). There is nothing "
                "for an officer to acknowledge, so this cannot be approved and the "
                "guard needs fixing rather than overriding."
            )
        if not named:
            raise ApprovalError(
                f"{item.order_id} is UNVERIFIED and cannot be approved without "
                f"naming what is being overridden. The guard found: "
                f"{sorted(blocking)}"
            )
        missing = blocking - named
        if missing:
            raise ApprovalError(
                f"{item.order_id}: these violations were not acknowledged: "
                f"{sorted(missing)}. Overriding a subset would record a signature "
                "against findings the officer never saw."
            )
        invented = named - blocking
        if invented:
            raise ApprovalError(
                f"{item.order_id}: these are not findings the guard made: "
                f"{sorted(invented)}. Free text in an override field is how a "
                "signature comes to rest on something nobody checked."
            )

    return SignedDecision(
        order_id=item.order_id,
        officer=officer,
        draft_digest=item.digest,
        decided_at=decided_at,
        approved=approved,
        overridden=tuple(sorted(overriding)) if approved else (),
        note=note,
    )
