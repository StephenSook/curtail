"""The signature step: the beat this whole system is built around.

`approval.sign` and `credentials` were built, tested, and imported by nothing. A
watermaster who cannot sign is a tour of components rather than a loop.

What is tested here is the WIRING, not the law. Every rule that decides whether a
signature is valid already lives in `approval.sign` and has its own suite: the wrong
officer's signature is none, an approval is bound to the exact bytes reviewed, an
unverified draft cannot be approved without naming every blocking finding. These tests
exist to prove the queue and the endpoints do not offer a way around any of that.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from curtail_agents.api import app
from curtail_agents.approval import ApprovalError, QueueItem
from curtail_agents.approval_queue import (
    DEMO_PASSPHRASE_ENV,
    DEMO_ROSTER,
    SIGNING_KEY_ENV,
    ApprovalQueue,
    DemoLoginRefusedError,
    SigningUnavailableError,
    demo_token,
    signing_key,
)
from curtail_agents.routing import GuardResult, Verdict
from curtail_core.clocks import SignatoryRole

KEY = "k" * 48
PASSPHRASE = "open-sesame-for-judges"
NOW = datetime(2026, 6, 16, 12, tzinfo=UTC)


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    monkeypatch.setenv(DEMO_PASSPHRASE_ENV, PASSPHRASE)
    yield


@pytest.fixture
def clean_queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[ApprovalQueue]:
    """A fresh queue per test, so one test's signature cannot satisfy another."""
    import curtail_agents.api as api_module
    import curtail_agents.approval_queue as queue_module

    fresh = ApprovalQueue()
    monkeypatch.setattr(queue_module, "QUEUE", fresh)
    monkeypatch.setattr(api_module, "QUEUE", fresh)
    yield fresh


def item(order_id: str = "DRAFT-SHASTA-2026-06-15-pass", *, passing: bool = True) -> QueueItem:
    guard = (
        GuardResult(verdict=Verdict.PASS, reason="draft matches the computed ledger")
        if passing
        else GuardResult(
            verdict=Verdict.ESCALATE,
            reason="1 right asserted with no basis in the ledger: A999999",
            unsupported_rights=("A999999",),
        )
    )
    return QueueItem(
        order_id=order_id,
        draft_text="DRAFT ORDER. Issued under 23 CCR 875. Curtailment applies as computed.",
        requires_role=SignatoryRole.DEPUTY_DIRECTOR,
        guard=guard,
        created_at=NOW,
    )


class TestNoKeyMeansNoSignature:
    """A built-in development key would let any deployment that forgot to configure one
    mint signatures with a value published in this repository. That is worse than being
    unable to sign, because signing is the act the system exists to protect."""

    def test_signing_key_refuses_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
        with pytest.raises(SigningUnavailableError, match="Refusing rather than using"):
            signing_key()

    def test_a_short_key_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, "too-short")
        with pytest.raises(SigningUnavailableError, match="bytes"):
            signing_key()

    def test_the_session_endpoint_refuses_rather_than_defaulting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The passphrase IS set, so the request gets past the login gate and reaches the
        # key check. Without this the endpoint 403s first and the test proves nothing
        # about defaulting.
        monkeypatch.setenv(DEMO_PASSPHRASE_ENV, PASSPHRASE)
        monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
        response = TestClient(app).post(
            "/api/session", json={"role": "deputy_director", "passphrase": PASSPHRASE}
        )
        assert response.status_code == 503
        assert SIGNING_KEY_ENV in response.json()["detail"]


class TestTheQueueIsNotARouteAroundTheLaw:
    """Every refusal below is decided by `approval.sign`. These prove the wiring hands
    the decision over rather than making its own."""

    def test_the_wrong_officer_cannot_sign(self, keyed: None, clean_queue: ApprovalQueue) -> None:
        queued = clean_queue.add(item())
        wrong = demo_token(role="executive_director", passphrase=PASSPHRASE)
        with pytest.raises(ApprovalError, match="requires the deputy_director"):
            clean_queue.decide(queued.order_id, officer_token=wrong, reviewed_digest=queued.digest)

    def test_a_stale_digest_voids_the_approval(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        """An approval is bound to the bytes it was given."""
        queued = clean_queue.add(item())
        token = demo_token(role="deputy_director", passphrase=PASSPHRASE)
        with pytest.raises(ApprovalError, match="changed after it was reviewed"):
            clean_queue.decide(queued.order_id, officer_token=token, reviewed_digest="0" * 64)

    def test_an_unverified_draft_needs_its_findings_named(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        queued = clean_queue.add(item("DRAFT-SHASTA-2026-06-15-escalate", passing=False))
        token = demo_token(role="deputy_director", passphrase=PASSPHRASE)
        with pytest.raises(ApprovalError, match="naming what is being overridden"):
            clean_queue.decide(queued.order_id, officer_token=token, reviewed_digest=queued.digest)

    def test_naming_them_completely_does_allow_an_override(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        """Non-vacuity. A queue that refused every override would be safe and useless,
        and 875(b)(3) preserves exactly this discretion."""
        queued = clean_queue.add(item("DRAFT-SHASTA-2026-06-15-escalate", passing=False))
        token = demo_token(role="deputy_director", passphrase=PASSPHRASE)
        decision = clean_queue.decide(
            queued.order_id,
            officer_token=token,
            reviewed_digest=queued.digest,
            overriding=queued.blocking_violations,
            note="field verification confirmed the right",
        )
        assert decision.is_override is True
        assert decision.overridden == queued.blocking_violations

    def test_one_order_gets_one_signature(self, keyed: None, clean_queue: ApprovalQueue) -> None:
        queued = clean_queue.add(item())
        token = demo_token(role="deputy_director", passphrase=PASSPHRASE)
        clean_queue.decide(queued.order_id, officer_token=token, reviewed_digest=queued.digest)
        with pytest.raises(ApprovalError, match="already decided"):
            clean_queue.decide(queued.order_id, officer_token=token, reviewed_digest=queued.digest)

    def test_a_decided_draft_cannot_be_replaced(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        """Overwriting the bytes under a recorded signature would leave that record
        pointing at a document nobody approved."""
        queued = clean_queue.add(item())
        token = demo_token(role="deputy_director", passphrase=PASSPHRASE)
        clean_queue.decide(queued.order_id, officer_token=token, reviewed_digest=queued.digest)
        with pytest.raises(ApprovalError, match="cannot be replaced"):
            clean_queue.add(item())


class TestTheApiCarriesTheDecisionRatherThanMakingIt:
    def test_a_signature_records_how_identity_was_established(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        """`authenticated_via` is not decoration: `issue_officer_token` refuses
        placeholders like "unknown" so a record can always say how it knew who signed."""
        client = TestClient(app)
        queued = clean_queue.add(item())
        token = client.post(
            "/api/session", json={"role": "deputy_director", "passphrase": PASSPHRASE}
        ).json()["officer_token"]

        response = client.post(
            f"/api/queue/{queued.order_id}/sign",
            json={"officer_token": token, "reviewed_digest": queued.digest},
        )
        assert response.status_code == 200
        assert response.json()["authenticated_via"] == "demo console login, shared passphrase"

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"reviewed_digest": "x" * 64}, "officer token is required"),
            ({"officer_token": "t"}, "digest of the draft actually reviewed"),
        ],
    )
    def test_a_request_missing_what_binds_it_is_refused(
        self, keyed: None, clean_queue: ApprovalQueue, body: dict[str, str], expected: str
    ) -> None:
        queued = clean_queue.add(item())
        response = TestClient(app).post(f"/api/queue/{queued.order_id}/sign", json=body)
        assert response.status_code == 422
        assert expected in response.json()["detail"]

    def test_a_domain_refusal_is_409_not_500(self, keyed: None, clean_queue: ApprovalQueue) -> None:
        """These are decisions the domain layer made, not failures of the service."""
        client = TestClient(app)
        queued = clean_queue.add(item())
        wrong = client.post(
            "/api/session", json={"role": "executive_director", "passphrase": PASSPHRASE}
        ).json()["officer_token"]
        response = client.post(
            f"/api/queue/{queued.order_id}/sign",
            json={"officer_token": wrong, "reviewed_digest": queued.digest},
        )
        assert response.status_code == 409

    def test_the_list_view_carries_no_draft_text(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        """A queue is read to decide what to open. Shipping every full order into that
        view puts documents in front of a reader who has not chosen to review them."""
        clean_queue.add(item())
        listing = TestClient(app).get("/api/queue").json()
        assert listing["pending"]
        for entry in listing["pending"]:
            assert "draft_text" not in entry
            assert "digest" in entry
            assert "state" in entry

    def test_every_queue_response_states_that_it_is_not_durable(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        """Cloud SQL is the source of truth in the architecture and it is not wired. A
        demo surface that looked durable would claim what the deployment cannot support."""
        client = TestClient(app)
        queued = clean_queue.add(item())
        assert "not durable" in client.get("/api/queue").json()["persistence"]
        assert "not durable" in client.get(f"/api/queue/{queued.order_id}").json()["persistence"]

    def test_an_unknown_order_is_404(self, keyed: None, clean_queue: ApprovalQueue) -> None:
        assert TestClient(app).get("/api/queue/NOT-A-DRAFT").status_code == 404


class TestTheDraftIsNotDressedAsAStateOrder:
    def test_the_order_id_is_not_shaped_like_a_board_number(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        """Rule 8. An artifact numbered "WR 2026-0005-DWR" reads as one the state
        issued, and this system produces drafts."""
        import re

        queued = clean_queue.add(item())
        assert queued.order_id.startswith("DRAFT-")
        assert not re.match(r"^WR[ -]\d{4}-\d{4}", queued.order_id)

    def test_the_item_view_says_nothing_is_in_force(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        queued = clean_queue.add(item())
        body = TestClient(app).get(f"/api/queue/{queued.order_id}").json()
        assert "Nothing here is in force" in body["disclaimer"]
        assert "875(b)" in body["disclaimer"]


class TestTheDemoLoginIsNotAnOpenDoor:
    """The worst defect in this project, caught by a review after it had shipped.

    `/api/session` took a role and a free-text officer id and minted a token for anyone
    who asked, on a public URL with unauthenticated ingress. The signing path verifies a
    MAC, which proves the SERVER issued a token and nothing about who asked for it, so
    the identity behind every signature was whatever a stranger had typed.

    That is not a weaker administrative record than none. It is a fabricated one.
    """

    def test_no_passphrase_configured_means_no_login(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
        monkeypatch.delenv(DEMO_PASSPHRASE_ENV, raising=False)
        with pytest.raises(DemoLoginRefusedError, match="Refusing rather than letting"):
            demo_token(role="deputy_director", passphrase="anything")

    @pytest.mark.parametrize(
        "attempt", ["", "guess", "OPEN-SESAME-FOR-JUDGES", " open-sesame-for-judges "]
    )
    def test_the_wrong_passphrase_mints_nothing(self, keyed: None, attempt: str) -> None:
        with pytest.raises(DemoLoginRefusedError, match="not correct"):
            demo_token(role="deputy_director", passphrase=attempt)

    def test_the_endpoint_returns_403_and_no_token(self, keyed: None) -> None:
        response = TestClient(app).post(
            "/api/session", json={"role": "deputy_director", "passphrase": "guess"}
        )
        assert response.status_code == 403
        assert "officer_token" not in response.json()

    def test_the_identity_in_the_signed_record_comes_from_the_roster(
        self, keyed: None, clean_queue: ApprovalQueue
    ) -> None:
        """Asserted on the SIGNATURE, not on the session response.

        The first version checked the id the endpoint echoed back, and a mutation putting
        the caller's own string into the TOKEN passed it: the response was rendering the
        roster value independently of what the token carried. The only identity that
        matters is the one inside the verified token, because that is what reaches the
        record.
        """
        queued = clean_queue.add(item())
        token = demo_token(role="deputy_director", passphrase=PASSPHRASE)
        decision = clean_queue.decide(
            queued.order_id, officer_token=token, reviewed_digest=queued.digest
        )
        assert decision.officer.officer_id == DEMO_ROSTER[SignatoryRole.DEPUTY_DIRECTOR]
        assert decision.officer.officer_id.startswith("demo-")

    def test_the_session_response_agrees_with_the_token(self, keyed: None) -> None:
        """The echoed value and the token must not be able to disagree, since a reader
        sees the first and the record keeps the second."""
        body = (
            TestClient(app)
            .post("/api/session", json={"role": "deputy_director", "passphrase": PASSPHRASE})
            .json()
        )
        assert body["officer_id"] == DEMO_ROSTER[SignatoryRole.DEPUTY_DIRECTOR]
        assert body["officer_id"].startswith("demo-")

    def test_no_roster_identity_is_a_real_person(self) -> None:
        """The first version used the name of the actual sitting Deputy Director, which
        would have produced fabricated orders signed in a real official's name."""
        for officer_id in DEMO_ROSTER.values():
            assert officer_id.startswith("demo-"), officer_id
            assert "christian" not in officer_id.lower()

    def test_the_record_says_only_what_was_established(self, keyed: None) -> None:
        """A passphrase, not a person. The disclaimer has to match what actually
        happened, or it is decoration on a fabricated record."""
        body = (
            TestClient(app)
            .post("/api/session", json={"role": "deputy_director", "passphrase": PASSPHRASE})
            .json()
        )
        assert "shared passphrase" in body["authenticated_via"]
        assert "NOT who they are" in body["disclaimer"]


class TestTheCredentialNeverTravelsInAUrl:
    """It shipped as a query parameter for one revision and a review caught it.

    Cloud Run records the full request URL in its access log, so the live passphrase was
    sitting in plaintext in Cloud Logging. It would also reach browser history, any
    intervening proxy, and the Referer header on the next outbound link. Verified in the
    logs before fixing, and the exposed value was rotated.

    A credential in a URL is a credential you have published.
    """

    def test_the_query_string_form_no_longer_works(self, keyed: None) -> None:
        response = TestClient(app).post(
            f"/api/session?role=deputy_director&passphrase={PASSPHRASE}"
        )
        assert response.status_code == 422, "the endpoint still accepts a credential from the URL"
        assert "officer_token" not in response.json()

    def test_the_body_form_works(self, keyed: None) -> None:
        """Non-vacuity: an endpoint that refused everything would pass the test above."""
        response = TestClient(app).post(
            "/api/session", json={"role": "deputy_director", "passphrase": PASSPHRASE}
        )
        assert response.status_code == 200
        assert response.json()["officer_token"]

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"passphrase": PASSPHRASE}, "a role is required"),
            ({"role": "deputy_director"}, "a passphrase is required"),
            ({"role": "", "passphrase": PASSPHRASE}, "a role is required"),
        ],
    )
    def test_a_malformed_body_is_refused(
        self, keyed: None, body: dict[str, str], expected: str
    ) -> None:
        response = TestClient(app).post("/api/session", json=body)
        assert response.status_code == 422
        assert expected in response.json()["detail"]

    def test_the_console_sends_it_in_the_body(self) -> None:
        """The other half. A server that only accepts a body is no help if the page it
        serves still puts the secret in a URL."""
        from curtail_agents.api import CONSOLE

        page = CONSOLE.read_text()
        assert "passphrase=${" not in page, "the console builds a URL containing the secret"
        assert '"/api/session", {' in page or '"/api/session",' in page
        assert "JSON.stringify({" in page
