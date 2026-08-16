"""Prove a request came from this project's scheduler, not from anyone who found the URL.

The console is deliberately reachable without a login, because judges have to be able to
click it. That makes every endpoint on the service publicly addressable, so an endpoint
that WRITES has to establish who is calling it in the handler. A path under `/internal/` is
a naming convention, not a boundary: this project's own notes record a Devvit rejection for
exactly that mistake, where a menu item hidden from non-moderators stayed reachable over
HTTP.

**Three checks, and each one is load-bearing.**

1. The token is a real Google-signed OIDC token. Verified against Google's published keys,
   so it cannot be forged.
2. Its audience is this endpoint. A valid Google token minted for some other service would
   otherwise be replayable here, which is the classic confused-deputy shape.
3. Its subject is an allowlisted service account. Anyone with a Google account can obtain a
   correctly signed token; only this project's scheduler should be able to write to the
   river watch.

Dropping any one of the three leaves a hole that the other two do not cover, which is why
each has its own test.

**It fails closed.** No token, an unverifiable token, the wrong audience or an unexpected
caller all refuse. A watch that anyone can write to is worse than no watch, because its
history would look exactly as authoritative.
"""

from __future__ import annotations

import os
from typing import Any

#: The service accounts permitted to drive the watch, comma separated. Empty means nothing
#: is permitted, which is the safe default: an unset allowlist that accepted everyone would
#: turn a missing configuration into an open endpoint.
ALLOWLIST_ENV = "CURTAIL_SCHEDULER_ACCOUNTS"

#: The audience the scheduler mints its token for. Set to the endpoint's own URL.
AUDIENCE_ENV = "CURTAIL_SCHEDULER_AUDIENCE"


class SchedulerAuthError(RuntimeError):
    """The caller could not be established as this project's scheduler."""


def allowed_accounts() -> frozenset[str]:
    raw = os.environ.get(ALLOWLIST_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def verify(
    authorization: str | None,
    *,
    verifier: Any = None,
    request: Any = None,
) -> str:
    """Return the calling service account, or raise.

    Args:
        authorization: the raw `Authorization` header.
        verifier: injected for tests. Defaults to Google's own verification, which
            fetches and caches Google's signing certificates.

    Raises:
        SchedulerAuthError: on anything short of a verified, correctly addressed token
            from an allowlisted account.
    """
    accounts = allowed_accounts()
    if not accounts:
        raise SchedulerAuthError(
            f"{ALLOWLIST_ENV} is not set, so no caller is permitted to write to the "
            "watch. An unset allowlist refuses rather than admitting everyone."
        )

    audience = os.environ.get(AUDIENCE_ENV)
    if not audience:
        raise SchedulerAuthError(
            f"{AUDIENCE_ENV} is not set, so a token minted for another service could be "
            "replayed here. Refusing rather than skipping the audience check."
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise SchedulerAuthError("no bearer token was presented")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise SchedulerAuthError("the bearer token is empty")

    if verifier is None:  # pragma: no cover - the live path needs Google's certificates
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        verifier = google_id_token.verify_oauth2_token
        request = request or google_requests.Request()

    try:
        claims = verifier(token, request, audience)
    except Exception as exc:
        raise SchedulerAuthError(f"the token could not be verified: {exc}") from exc

    if not isinstance(claims, dict):
        raise SchedulerAuthError("the verified token did not carry claims")

    # Verified email only. A token can carry an `email` that Google has not confirmed, and
    # treating an unverified one as an identity is the whole point of the flag.
    if claims.get("email_verified") not in (True, "true"):
        raise SchedulerAuthError("the token's email is not verified")

    caller = str(claims.get("email", ""))
    if caller not in accounts:
        raise SchedulerAuthError(
            f"{caller or 'an unnamed caller'} is not permitted to write to the watch"
        )

    # Belt and braces on the audience. `verify_oauth2_token` checks it, and asserting it
    # again here means a future refactor that drops the argument cannot silently widen the
    # endpoint to every Google-signed token in existence.
    if claims.get("aud") != audience:
        # The diagnostic matters more than usual here, because the likely misconfiguration
        # fails PARTIALLY. One audience is compared against every basin's token, so an
        # audience carrying a per-basin path matches one river and rejects the others, and
        # a half-working watch is far harder to notice than a dead one.
        hint = ""
        if "/internal/" in audience:
            hint = (
                f" {AUDIENCE_ENV} names a path. It must be the service root, because one "
                "value is checked against every basin's token and a per-basin path admits "
                "one river and refuses the rest."
            )
        raise SchedulerAuthError(f"the token was minted for a different audience.{hint}")

    return caller


__all__ = [
    "ALLOWLIST_ENV",
    "AUDIENCE_ENV",
    "SchedulerAuthError",
    "allowed_accounts",
    "verify",
]
