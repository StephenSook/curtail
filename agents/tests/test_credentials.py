"""The authentication boundary, tested by attacking it.

A boundary tested only on its happy path is a boundary nobody has tried to get past.
Every test below is an attempt at forgery, tampering, replay, or expiry abuse, and
the one non-attack test exists so the whole file cannot pass by refusing everything.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from curtail_agents.credentials import (
    AUDIENCE,
    MINIMUM_KEY_BYTES,
    CredentialError,
    Officer,
    issue_officer_token,
    verify_officer_token,
)
from curtail_core.clocks import SignatoryRole

KEY = b"k" * MINIMUM_KEY_BYTES
OTHER_KEY = b"j" * MINIMUM_KEY_BYTES
ISSUED = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
EXPIRES = ISSUED + timedelta(hours=8)
DURING = ISSUED + timedelta(hours=1)


def _token(**overrides: object) -> str:
    args: dict[str, object] = {
        "role": SignatoryRole.DEPUTY_DIRECTOR,
        "officer_id": "jchristian-smith",
        "authenticated_via": "iap-oidc",
        "issued_at": ISSUED,
        "expires_at": EXPIRES,
        "key": KEY,
    }
    args.update(overrides)
    return issue_officer_token(**args)  # type: ignore[arg-type]


class TestAVerifiedTokenYieldsAnOfficer:
    def test_the_round_trip_works(self) -> None:
        """Non-vacuity. Without this, a verifier that rejected everything would pass
        every attack test in this file."""
        officer = verify_officer_token(_token(), key=KEY, now=DURING)
        assert officer.role is SignatoryRole.DEPUTY_DIRECTOR
        assert officer.officer_id == "jchristian-smith"
        assert officer.authenticated_via == "iap-oidc"

    def test_the_claim_carries_its_own_lifetime(self) -> None:
        officer = verify_officer_token(_token(), key=KEY, now=DURING)
        assert officer.issued_at == ISSUED
        assert officer.expires_at == EXPIRES


class TestTheTypeIsAnHonestCarrierNotACapability:
    """The sentinel is gone, and its removal is the finding.

    An earlier version made `Officer` "unconstructible" with a private-constructor
    sentinel. A review called the boundary bypassable by design and was right twice
    over, both confirmed by running them: the sentinel was a module attribute
    anybody could import, and `dataclasses.replace` on a verified officer escalated
    a Board clerk to Deputy Director while carrying the clerk's own proof.

    Removing an ineffective guard beats keeping a misleading one. This type now
    admits it is only what a token said, and nothing accepts one as authority.
    """

    def test_one_can_be_built_and_it_proves_nothing(self) -> None:
        conjured = Officer(
            role=SignatoryRole.DEPUTY_DIRECTOR,
            officer_id="impostor",
            authenticated_via="i said so",
            issued_at=ISSUED,
            expires_at=EXPIRES,
        )
        assert conjured.officer_id == "impostor"

    def test_no_sentinel_survives_to_be_imported(self) -> None:
        """A guard that can be imported is not a guard, and leaving it in place
        would keep advertising a property the module no longer claims."""
        from curtail_agents import credentials

        assert not hasattr(credentials, "_VERIFIED")

    def test_replace_can_still_lie_and_that_is_why_nothing_trusts_the_object(
        self,
    ) -> None:
        import dataclasses

        clerk = verify_officer_token(
            _token(role=SignatoryRole.BOARD, officer_id="clerk"), key=KEY, now=DURING
        )
        escalated = dataclasses.replace(clerk, role=SignatoryRole.DEPUTY_DIRECTOR)
        assert escalated.role is SignatoryRole.DEPUTY_DIRECTOR
        # The authority is the TOKEN. approval.sign re-verifies rather than reading
        # this field, and test_approval covers that the escalation buys nothing.


class TestForgeryRequiresTheKey:
    def test_a_token_minted_with_another_key_is_refused(self) -> None:
        with pytest.raises(CredentialError, match="could not be verified"):
            verify_officer_token(_token(key=OTHER_KEY), key=KEY, now=DURING)

    def test_a_tampered_role_is_refused(self) -> None:
        """The attack that matters: promote yourself to the officer the regulation
        names, and sign a curtailment order."""
        payload_b64, mac_b64 = _token().split(".")
        claim = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
        claim["role"] = SignatoryRole.EXECUTIVE_DIRECTOR.value
        forged = (
            base64.urlsafe_b64encode(
                json.dumps(claim, separators=(",", ":"), sort_keys=True).encode()
            )
            .decode()
            .rstrip("=")
        )
        with pytest.raises(CredentialError, match="could not be verified"):
            verify_officer_token(f"{forged}.{mac_b64}", key=KEY, now=DURING)

    def test_a_tampered_officer_id_is_refused(self) -> None:
        payload_b64, mac_b64 = _token().split(".")
        claim = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
        claim["officer_id"] = "somebody-else"
        forged = (
            base64.urlsafe_b64encode(
                json.dumps(claim, separators=(",", ":"), sort_keys=True).encode()
            )
            .decode()
            .rstrip("=")
        )
        with pytest.raises(CredentialError):
            verify_officer_token(f"{forged}.{mac_b64}", key=KEY, now=DURING)

    def test_a_stripped_signature_is_refused(self) -> None:
        """The `alg: none` family. There is no algorithm field to set, and an empty
        MAC is simply a MAC that does not match."""
        payload_b64, _ = _token().split(".")
        with pytest.raises(CredentialError):
            verify_officer_token(f"{payload_b64}.", key=KEY, now=DURING)

    @pytest.mark.parametrize("malformed", ["", "no-dot", "a.b.c", "!!!.!!!", "....", "a."])
    def test_malformed_tokens_are_refused(self, malformed: str) -> None:
        with pytest.raises(CredentialError):
            verify_officer_token(malformed, key=KEY, now=DURING)


class TestReplayAndExpiry:
    def test_an_expired_token_is_refused(self) -> None:
        """An approval signed with a stale session is an approval nobody was
        present for."""
        with pytest.raises(CredentialError, match="expired"):
            verify_officer_token(_token(), key=KEY, now=EXPIRES + timedelta(seconds=1))

    def test_a_token_is_refused_at_the_exact_moment_it_expires(self) -> None:
        """Boundary, not near-boundary. An inclusive comparison here would extend
        every session by whatever the clock granularity happens to be."""
        with pytest.raises(CredentialError):
            verify_officer_token(_token(), key=KEY, now=EXPIRES)

    def test_a_token_from_the_future_is_refused(self) -> None:
        with pytest.raises(CredentialError, match="not yet valid"):
            verify_officer_token(_token(), key=KEY, now=ISSUED - timedelta(seconds=1))

    def test_a_token_for_another_audience_is_refused(self) -> None:
        """A signing key shared with a second surface must not be replayable here
        to approve a curtailment order."""
        payload = json.dumps(
            {
                "aud": "some.other.service",
                "role": SignatoryRole.DEPUTY_DIRECTOR.value,
                "officer_id": "jchristian-smith",
                "authenticated_via": "iap-oidc",
                "iat": ISSUED.isoformat(),
                "exp": EXPIRES.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        import hashlib
        import hmac

        mac = hmac.new(KEY, payload, hashlib.sha256).digest()
        token = (
            base64.urlsafe_b64encode(payload).decode().rstrip("=")
            + "."
            + base64.urlsafe_b64encode(mac).decode().rstrip("=")
        )
        with pytest.raises(CredentialError, match="another audience"):
            verify_officer_token(token, key=KEY, now=DURING)
        assert AUDIENCE.startswith("curtail."), "the audience must name this surface"


class TestWeakKeysAreRefusedRatherThanAcceptedWeakly:
    def test_a_short_key_cannot_issue(self) -> None:
        with pytest.raises(CredentialError, match="minimum"):
            _token(key=b"short")

    def test_a_short_key_cannot_verify(self) -> None:
        """Refused on BOTH sides. Checking only at issue time would let a verifier
        accept tokens minted by something with a weaker key."""
        with pytest.raises(CredentialError, match="minimum"):
            verify_officer_token(_token(), key=b"short", now=DURING)


class TestIssuanceRefusesIncoherentClaims:
    def test_an_unnamed_officer_is_refused(self) -> None:
        with pytest.raises(CredentialError):
            _token(officer_id="   ")

    def test_an_unrecorded_authentication_method_is_refused(self) -> None:
        with pytest.raises(CredentialError):
            _token(authenticated_via="")

    def test_a_naive_timestamp_is_refused(self) -> None:
        with pytest.raises(CredentialError):
            _token(issued_at=datetime(2026, 6, 10, 9, 0))  # noqa: DTZ001

    def test_a_token_expiring_before_issue_is_refused(self) -> None:
        with pytest.raises(CredentialError):
            _token(expires_at=ISSUED - timedelta(seconds=1))

    def test_a_naive_verification_time_is_refused(self) -> None:
        with pytest.raises(CredentialError):
            verify_officer_token(_token(), key=KEY, now=datetime(2026, 6, 10, 10, 0))  # noqa: DTZ001


class TestTimingSafetyIsAssertedStructurally:
    """The one property functional tests cannot see, so it is checked at the source.

    Mutation proved it: replacing `hmac.compare_digest` with `!=` passes every other
    test in this file. Timing behaviour is not observable from a functional
    assertion, and a wall-clock measurement would be flaky on shared CI, so the
    check that actually holds is that the source uses the constant-time primitive.

    This is not a general licence to assert on source text. It is the narrow case
    where the property is real, the regression is silent, and no runtime observation
    is available: a byte-by-byte comparison leaks how much of a forged MAC was
    correct, which is enough to recover the rest one byte at a time.
    """

    @staticmethod
    def _source() -> str:
        import inspect

        from curtail_agents import credentials

        return inspect.getsource(credentials)

    def test_the_mac_is_compared_in_constant_time(self) -> None:
        source = self._source()
        assert "hmac.compare_digest(" in source, (
            "the MAC comparison is not constant time. A byte-by-byte compare leaks "
            "how much of a forged MAC was correct."
        )

    def test_no_direct_equality_comparison_of_a_mac(self) -> None:
        """Named separately: adding compare_digest somewhere while leaving an `==`
        on the hot path would satisfy the test above and change nothing."""
        source = self._source()
        assert "_mac(key, payload) ==" not in source
        assert "_mac(key, payload) !=" not in source
        assert "presented ==" not in source

    def test_the_check_would_notice_its_own_absence(self) -> None:
        """Non-vacuity for a source-text assertion, which is the shape most likely
        to pass by accident."""
        assert "hmac.compare_digest(" not in "if a != b: raise"
