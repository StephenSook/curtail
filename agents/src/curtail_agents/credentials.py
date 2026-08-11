"""The authentication boundary: an officer identity that must be VERIFIED to exist.

A review said twice that `approval.sign` minted a signed record from caller-supplied
identity strings, so the role gate protected nothing and forged approvals were a
function call away. The first answer was that a domain module cannot authenticate
anybody and the honest move was to label the gap. That was half right and the wrong
half mattered, the same shape as an earlier finding on this project: the boundary
was declared unbuildable without anyone trying to build it.

It is buildable, and this is it. An `Officer` cannot be constructed from strings at
all. The only way to obtain one is `verify_officer_token`, which checks an HMAC over
the exact claim bytes using a key the domain layer does not hold. Forging an
approval therefore requires the signing key rather than a keyboard, which is the
difference between a documented gap and a boundary.

**What this does and does not claim.**

- It DOES bind an approval to a claim some other layer minted with the key, with
  expiry, tamper detection, and a constant-time comparison.
- It does NOT authenticate a human. Establishing that a person is the Deputy
  Director is IAP's or the identity provider's job. This carries that assertion
  across a trust boundary without letting anything in between rewrite it.
- The key belongs in Secret Manager and is loaded by the console, never by an agent
  and never committed. A short key is refused rather than accepted weakly.

The token is deliberately not a general-purpose JWT. It carries four fields, it has
one audience, and its verifier accepts one algorithm, so there is no algorithm
field for an attacker to set to `none`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from curtail_core.clocks import SignatoryRole

#: Refused below this length. A key short enough to brute force is worse than an
#: obvious absence, because it produces records that look verified.
MINIMUM_KEY_BYTES: Final = 32

#: One audience, checked. A token minted for anything else is refused even if its
#: MAC is valid, so a signing key shared with another surface cannot be replayed
#: here to approve a curtailment order.
AUDIENCE: Final = "curtail.approval.officer"

#: A sentinel only this module can pass. It is what makes `Officer` unconstructible
#: from ordinary code: the dataclass refuses any instantiation that does not present
#: it, so a caller cannot fabricate an identity by filling in the fields.
_VERIFIED = object()


class CredentialError(RuntimeError):
    """A token was absent, expired, tampered with, or minted for something else.

    One exception for every failure, with a message that does not distinguish a bad
    MAC from a bad payload, so the error itself is not an oracle.
    """


@dataclass(frozen=True, slots=True)
class Officer:
    """A verified officer identity. Obtainable only from `verify_officer_token`.

    The `_proof` field is the private-constructor idiom: ordinary code cannot supply
    it, so this type cannot be conjured beside the verifier that produces it. That
    is not a defence against someone editing this file; it is a defence against the
    far likelier event of a future console filling in an identity by hand because it
    was easy.
    """

    role: SignatoryRole
    officer_id: str
    authenticated_via: str
    issued_at: datetime
    expires_at: datetime
    _proof: Any = None

    def __post_init__(self) -> None:
        if self._proof is not _VERIFIED:
            raise CredentialError(
                "an Officer cannot be constructed directly. Obtain one from "
                "verify_officer_token, which checks an HMAC the domain layer has no "
                "key for. An identity assembled from strings is an identity anybody "
                "can assemble."
            )


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(key: bytes, payload: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def _check_key(key: bytes) -> None:
    if len(key) < MINIMUM_KEY_BYTES:
        raise CredentialError(
            f"the signing key is {len(key)} bytes, under the {MINIMUM_KEY_BYTES} "
            "byte minimum. A weak key produces records that look verified, which is "
            "worse than having none."
        )


def issue_officer_token(
    *,
    role: SignatoryRole,
    officer_id: str,
    authenticated_via: str,
    issued_at: datetime,
    expires_at: datetime,
    key: bytes,
) -> str:
    """Mint a claim. Called by the authenticated console, never by an agent.

    The console has already established who the person is, through IAP or whatever
    identity provider is configured; this converts that fact into something the
    domain layer can verify without trusting its caller.
    """
    _check_key(key)
    if not officer_id.strip():
        raise CredentialError(
            "an officer id is required; a record naming only a role is not auditable"
        )
    if not authenticated_via.strip():
        raise CredentialError("the authentication method must be recorded")
    if authenticated_via.strip().lower() in {"unknown", "none", "n/a", "todo"}:
        raise CredentialError(
            f"{authenticated_via!r} is a placeholder, not an authentication method. "
            "A record that cannot say how identity was established is not evidence, "
            "and one that says 'unknown' is worse: it looks like evidence."
        )
    if issued_at.tzinfo is None or expires_at.tzinfo is None:
        raise CredentialError("token timestamps must be timezone aware")
    if expires_at <= issued_at:
        raise CredentialError("a token that expires before it is issued is never valid")

    payload = json.dumps(
        {
            "aud": AUDIENCE,
            "role": role.value,
            "officer_id": officer_id.strip(),
            "authenticated_via": authenticated_via.strip(),
            "iat": issued_at.isoformat(),
            "exp": expires_at.isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{_b64(payload)}.{_b64(_mac(key, payload))}"


def verify_officer_token(token: str, *, key: bytes, now: datetime) -> Officer:
    """The only way to obtain an Officer.

    Verifies the MAC over the EXACT payload bytes received rather than over a
    re-serialisation of the parsed claim: re-encoding would let two different byte
    strings share a signature, which is the canonicalisation bug that has broken
    real token formats.

    Args:
        now: injected, so expiry is tested without sleeping and so a caller cannot
            be surprised by a clock it did not choose.
    """
    _check_key(key)
    if now.tzinfo is None:
        raise CredentialError("the verification time must be timezone aware")

    try:
        encoded_payload, encoded_mac = token.split(".")
        payload = _unb64(encoded_payload)
        presented = _unb64(encoded_mac)
    except (ValueError, TypeError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CredentialError("the token is malformed") from exc

    # Constant time. A byte-by-byte comparison leaks how much of a forged MAC was
    # correct, which is enough to recover the rest one byte at a time.
    if not hmac.compare_digest(_mac(key, payload), presented):
        raise CredentialError("the token could not be verified")

    try:
        claim = json.loads(payload)
        role = SignatoryRole(claim["role"])
        issued_at = datetime.fromisoformat(claim["iat"])
        expires_at = datetime.fromisoformat(claim["exp"])
        audience = claim["aud"]
        officer_id = claim["officer_id"]
        authenticated_via = claim["authenticated_via"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CredentialError("the token could not be verified") from exc

    if audience != AUDIENCE:
        raise CredentialError(
            "the token was minted for another audience. A key shared with a second "
            "surface must not be replayable here to approve a curtailment order."
        )
    if now >= expires_at:
        raise CredentialError(
            f"the token expired at {expires_at.isoformat()}. An approval signed with "
            "a stale session is an approval nobody was present for."
        )
    if now < issued_at:
        raise CredentialError("the token is not yet valid")

    return Officer(
        role=role,
        officer_id=officer_id,
        authenticated_via=authenticated_via,
        issued_at=issued_at,
        expires_at=expires_at,
        _proof=_VERIFIED,
    )
