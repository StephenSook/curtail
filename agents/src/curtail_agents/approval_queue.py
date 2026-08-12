"""The queue an officer signs from, and the decisions it has recorded.

`approval.sign` and `credentials` were built, tested, and imported by nothing. The
signature is the beat this whole system is built around: a watermaster who cannot sign is
a tour of components rather than a loop. This is the store that gives them somewhere to
sign from.

**Deliberately thin.** Every rule that matters already lives in `approval.sign`: the
wrong officer's signature is none, an approval is bound to the exact bytes reviewed, and
an unverified draft cannot be approved without naming every blocking finding. Nothing
here re-implements any of that, and nothing here may be an alternative route to a
decision. This holds drafts and records outcomes.

**In process, and that is a stated limitation rather than a design.** Cloud SQL is the
source of truth in the architecture and it is not wired, so this queue lives in the
serving process: it does not survive a restart and two Cloud Run instances do not share
it. `persistence` says so on every response that carries queue state, for the same reason
the synthetic transport marks itself. A demo surface that looked durable would be making
a claim the deployment cannot support.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hmac import compare_digest

from curtail_agents.approval import ApprovalError, QueueItem, SignedDecision, sign
from curtail_agents.credentials import issue_officer_token
from curtail_core.clocks import SignatoryRole

#: Read by every response that exposes queue state. The queue is per-instance and
#: per-process, so a judge reading it is told rather than left to assume.
PERSISTENCE = (
    "in-process, not durable: no database is wired, so this queue does not survive a "
    "restart and is not shared between instances"
)

#: The environment variable holding the officer-token signing key.
SIGNING_KEY_ENV = "CURTAIL_SIGNING_KEY"

#: The shared secret a caller must present before any token is minted.
DEMO_PASSPHRASE_ENV = "CURTAIL_DEMO_PASSPHRASE"

#: The ONLY officer identities the demo login will issue, keyed by role.
#:
#: Caller-supplied ids were the defect. The endpoint took `officer_id` as free text on a
#: public URL, so anyone could mint a Deputy Director token and the resulting record would
#: name whoever they typed. A signature is only worth what the identity behind it is
#: worth, and a self-asserted string is worth nothing.
#:
#: These ids are obviously not real people ON PURPOSE. The first version used the name of
#: the actual sitting Deputy Director, which would have produced fabricated orders signed
#: in a real official's name: impersonation, and precisely what the no-government-
#: impersonation rule exists to stop. What this demo can honestly establish is that
#: SOMEBODY holding the passphrase acted in a role, so that is exactly what it records.
DEMO_ROSTER: dict[SignatoryRole, str] = {
    SignatoryRole.DEPUTY_DIRECTOR: "demo-deputy-director",
    SignatoryRole.EXECUTIVE_DIRECTOR: "demo-executive-director",
}


class DemoLoginRefusedError(RuntimeError):
    """Raised when the caller did not present the demo passphrase.

    Separate from SigningUnavailableError because the causes differ and so do the fixes:
    one is a deployment that has no secret configured, the other is a caller who does not
    hold it.
    """


class SigningUnavailableError(RuntimeError):
    """Raised when no signing key is configured. Never falls back to a default.

    A built-in development key would make every deployment that forgot to set one
    silently able to mint officer tokens with a value published in this repository.
    That is worse than being unable to sign, because signing is the act the whole
    system exists to protect.
    """


def signing_key() -> bytes:
    """The key, or a refusal. Never a default.

    Read per call rather than captured at import, so a deployment that sets the secret
    after startup does not need a restart, and so tests can set and unset it without
    reloading the module.
    """
    raw = os.environ.get(SIGNING_KEY_ENV, "")
    if not raw:
        raise SigningUnavailableError(
            f"{SIGNING_KEY_ENV} is not set, so no officer token can be issued or "
            "verified. Refusing rather than using a built-in key: a default committed "
            "to this repository would let any deployment that forgot to configure one "
            "mint signatures with a published value."
        )
    key = raw.encode()
    if len(key) < 32:
        raise SigningUnavailableError(
            f"{SIGNING_KEY_ENV} is {len(key)} bytes. A short key weakens the MAC that "
            "is the only thing standing between a request and an officer's signature."
        )
    return key


@dataclass
class ApprovalQueue:
    """Drafts awaiting signature, and the decisions already recorded.

    Decisions are kept alongside rather than replacing the item, because the
    administrative record needs both: what was signed and what it looked like when it
    was signed.
    """

    items: dict[str, QueueItem] = field(default_factory=dict)
    decisions: dict[str, SignedDecision] = field(default_factory=dict)

    def add(self, item: QueueItem) -> QueueItem:
        """Queue a draft. Refuses to replace one that has already been decided.

        A decided order whose draft was swapped underneath it would leave a signature
        pointing at bytes nobody approved, and `sign` guards the live path by digest.
        This guards the stored one.
        """
        if item.order_id in self.decisions:
            raise ApprovalError(
                f"{item.order_id} has already been decided and its draft cannot be "
                "replaced. A recorded signature names the exact bytes it was given, "
                "and overwriting them would leave that record pointing at a document "
                "nobody approved."
            )
        self.items[item.order_id] = item
        return item

    def get(self, order_id: str) -> QueueItem:
        try:
            return self.items[order_id]
        except KeyError:
            raise ApprovalError(f"no draft is queued as {order_id}") from None

    def pending(self) -> tuple[QueueItem, ...]:
        """Undecided drafts, oldest first, so the queue reads as a queue."""
        undecided = [i for k, i in self.items.items() if k not in self.decisions]
        return tuple(sorted(undecided, key=lambda i: i.created_at))

    def decision_for(self, order_id: str) -> SignedDecision | None:
        return self.decisions.get(order_id)

    def decide(
        self,
        order_id: str,
        *,
        officer_token: str,
        reviewed_digest: str,
        approved: bool = True,
        overriding: tuple[str, ...] = (),
        note: str = "",
    ) -> SignedDecision:
        """Sign, or refuse. The SERVER performs the act; the caller states intent.

        The token is verified inside `approval.sign` rather than here, so there is one
        path to a signature and it runs through the MAC. This function does not decide
        anything: it locates the draft, hands the caller's stated intent to the rule
        that knows the law, and records what comes back.
        """
        item = self.get(order_id)
        if order_id in self.decisions:
            raise ApprovalError(
                f"{order_id} was already decided at "
                f"{self.decisions[order_id].decided_at.isoformat()}. A second signature "
                "on one order is two records of one act."
            )
        decision = sign(
            item,
            officer_token=officer_token,
            key=signing_key(),
            reviewed_digest=reviewed_digest,
            approved=approved,
            overriding=overriding,
            note=note,
        )
        self.decisions[order_id] = decision
        return decision


#: The process-wide queue the API serves from. One per instance, which is what
#: PERSISTENCE says out loud.
QUEUE = ApprovalQueue()


def demo_token(*, role: str, passphrase: str, minutes: int = 30) -> str:
    """Mint a short-lived token, and only for a caller who holds the passphrase.

    **This endpoint was the worst defect in the project.** It took a role and a free-text
    officer id and minted a token for anyone who asked, on a public URL with
    unauthenticated ingress. A MAC proves the SERVER issued a token; it proves nothing
    about who asked for it. So the signing path was sound and the identity behind every
    signature was whatever a stranger had typed, which is not a weaker record than none.
    It is a fabricated one.

    Two changes close it. A shared passphrase, with no default, gates minting at all. And
    the identity is no longer supplied by the caller: it comes from a fixed roster of
    ids that are plainly not real people, because what this demo can honestly establish
    is that SOMEBODY holding the passphrase acted in a role.

    A real deployment replaces this with IAP or another identity provider and drops the
    roster. That is the seam; this is not it pretending to be one.
    """
    expected = os.environ.get(DEMO_PASSPHRASE_ENV, "")
    if not expected:
        raise DemoLoginRefusedError(
            f"{DEMO_PASSPHRASE_ENV} is not set, so no demo login can be granted. "
            "Refusing rather than letting an ungated endpoint mint officer tokens: a "
            "signature is worth exactly what the identity behind it is worth."
        )
    if not compare_digest(passphrase.encode(), expected.encode()):
        raise DemoLoginRefusedError("the demo passphrase is not correct")

    chosen = SignatoryRole(role)
    officer_id = DEMO_ROSTER.get(chosen)
    if officer_id is None:
        raise DemoLoginRefusedError(f"the demo login issues no identity for the {chosen.value}")

    now = datetime.now(UTC)
    return issue_officer_token(
        role=chosen,
        officer_id=officer_id,
        # Says exactly what was established and no more: a passphrase, not a person.
        authenticated_via="demo console login, shared passphrase",
        issued_at=now,
        expires_at=now + timedelta(minutes=minutes),
        key=signing_key(),
    )
