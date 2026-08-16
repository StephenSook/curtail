"""The scheduled river watch, and the door in front of it.

Offline. Nothing here reaches USGS, Firestore or Google's certificate endpoint.

The auth tests are the important half. This service answers the public internet so judges
can click it, which means `/internal/` is a naming convention and not a boundary. This
project's own notes record a Devvit rejection for exactly that mistake: a menu item hidden
from non-moderators stayed reachable over HTTP.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from curtail_agents.scheduler_auth import (
    ALLOWLIST_ENV,
    AUDIENCE_ENV,
    SchedulerAuthError,
    verify,
)
from curtail_agents.watch_store import (
    InMemoryWatchStore,
    Observation,
    Watch,
)
from curtail_core.basins import Basin

SCHEDULER = "curtail-scheduler@curtail-505118.iam.gserviceaccount.com"
AUDIENCE = "https://curtail-console-api-672785135387.us-central1.run.app/internal/poll/shasta"


@pytest.fixture(autouse=True)
def _configured(monkeypatch: Any) -> None:
    monkeypatch.setenv(ALLOWLIST_ENV, SCHEDULER)
    monkeypatch.setenv(AUDIENCE_ENV, AUDIENCE)


def claims(**overrides: Any) -> dict[str, Any]:
    return {"email": SCHEDULER, "email_verified": True, "aud": AUDIENCE} | overrides


def like_google(*, aud: str, **overrides: Any) -> Any:
    """A verifier that behaves the way `google.auth.jwt.decode` actually does.

    It RAISES on an audience mismatch rather than returning claims carrying the wrong
    `aud`, which is the contract the first version of these tests got wrong and the reason
    an actionable message sat in unreachable code.
    """

    def verifier(_token: str, _request: Any, audience: str) -> Any:
        if aud != audience:
            raise ValueError(f"Token has wrong audience {aud}, expected one of ['{audience}']")
        return claims(aud=aud, **overrides)

    return verifier


def verifier_returning(payload: Any) -> Any:
    def verifier(_token: str, _request: Any, _audience: str) -> Any:
        return payload

    return verifier


def observation(minute: int, cfs: float, minimum: float = 50.0) -> Observation:
    when = datetime(2026, 6, 16, 12, tzinfo=UTC) + timedelta(minutes=minute)
    return Observation(
        basin="shasta",
        observed_at=when,
        recorded_at=when + timedelta(seconds=30),
        cfs=cfs,
        minimum_cfs=minimum,
        classification="flow_below_minimum" if cfs < minimum else "flow_recovered_sustained",
        reading_key=f"shasta-{when.isoformat()}",
    )


class TestEachAuthCheckIsLoadBearing:
    """Three checks, and dropping any one leaves a hole the other two do not cover.

    A correctly signed Google token is obtainable by anyone with a Google account, so the
    signature alone proves nothing about WHO. An audience check alone would admit any
    caller who addressed this endpoint. An allowlist alone would trust an unverified
    claim. Each gets its own test because each fails differently.
    """

    def test_a_verified_allowlisted_caller_is_accepted(self) -> None:
        caller = verify("Bearer t", verifier=verifier_returning(claims()))
        assert caller == SCHEDULER

    def test_a_signed_token_from_another_account_is_refused(self) -> None:
        """The check that matters most. Anyone can get Google to sign a token for them."""
        other = claims(email="someone-else@gmail.com")
        with pytest.raises(SchedulerAuthError, match="not permitted"):
            verify("Bearer t", verifier=verifier_returning(other))

    def test_a_token_for_another_audience_is_refused(self) -> None:
        """The confused-deputy shape: a token minted for a different service, replayed.

        **This drives the DEFENSIVE branch, not the production one, and the distinction
        matters enough to state.** It uses a verifier that RETURNS mismatched claims, which
        `google.auth` never does: the real library raises, so in production the refusal
        comes from the exception handler instead. The check being exercised here exists for
        a future refactor that drops the audience argument, or a verifier that reports
        rather than raises, either of which would otherwise widen this endpoint to every
        Google-signed token in existence.

        `TestOneAudienceServesEveryBasin` covers the path production actually takes, with a
        fake that raises the way the library does. Assuming this test covered that path is
        precisely how an actionable error message ended up in unreachable code.
        """
        with pytest.raises(SchedulerAuthError, match="different audience"):
            verify("Bearer t", verifier=verifier_returning(claims(aud="https://elsewhere")))

    def test_an_unverified_email_is_refused(self) -> None:
        """A token can carry an email Google has not confirmed, which is the entire
        reason the flag exists."""
        with pytest.raises(SchedulerAuthError, match="not verified"):
            verify("Bearer t", verifier=verifier_returning(claims(email_verified=False)))

    def test_a_token_that_does_not_verify_is_refused(self) -> None:
        def explode(_token: str, _request: Any, _audience: str) -> Any:
            raise ValueError("bad signature")

        with pytest.raises(SchedulerAuthError, match="could not be verified"):
            verify("Bearer t", verifier=explode)

    @pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer ", "Bearer    "])
    def test_a_missing_or_malformed_header_is_refused(self, header: str | None) -> None:
        with pytest.raises(SchedulerAuthError):
            verify(header, verifier=verifier_returning(claims()))

    def test_the_audience_is_passed_to_the_verifier(self) -> None:
        """Asserted on the CALL, not only on the claim. If the argument were dropped, the
        library would stop checking the audience and the belt-and-braces claim check would
        be the only thing left, which is a much weaker position than it looks."""
        seen: dict[str, Any] = {}

        def capture(token: str, _request: Any, audience: str) -> Any:
            seen["token"], seen["audience"] = token, audience
            return claims()

        verify("Bearer the-token", verifier=capture)
        assert seen["token"] == "the-token"
        assert seen["audience"] == AUDIENCE


class TestItFailsClosedWhenUnconfigured:
    """A watch anyone can write to is worse than no watch, because its history would look
    exactly as authoritative."""

    def test_an_unset_allowlist_admits_nobody(self, monkeypatch: Any) -> None:
        monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
        with pytest.raises(SchedulerAuthError, match="no caller is permitted"):
            verify("Bearer t", verifier=verifier_returning(claims()))

    def test_an_empty_allowlist_admits_nobody(self, monkeypatch: Any) -> None:
        monkeypatch.setenv(ALLOWLIST_ENV, "   ,  ,")
        with pytest.raises(SchedulerAuthError, match="no caller is permitted"):
            verify("Bearer t", verifier=verifier_returning(claims()))

    def test_an_unset_audience_refuses_rather_than_skipping_the_check(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv(AUDIENCE_ENV, raising=False)
        with pytest.raises(SchedulerAuthError, match="replayed here"):
            verify("Bearer t", verifier=verifier_returning(claims()))

    def test_the_verifier_is_never_called_when_unconfigured(self, monkeypatch: Any) -> None:
        """Refused before the token is even examined, so a misconfiguration cannot become
        a partial check."""
        monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
        called = False

        def watch(_token: str, _request: Any, _audience: str) -> Any:
            nonlocal called
            called = True
            return claims()

        with pytest.raises(SchedulerAuthError):
            verify("Bearer t", verifier=watch)
        assert not called


class TestTheWatchRecordsARun:
    def test_consecutive_below_counts_back_from_the_newest(self) -> None:
        """Not a total. A season with forty below-minimum readings scattered through it
        and a recovery yesterday has a run of zero, and those two numbers are easy to
        confuse and mean completely different things to a watermaster."""
        store = InMemoryWatchStore()
        for index, cfs in enumerate([45.0, 44.0, 60.0, 46.0, 45.5]):
            store.append(Basin.SHASTA, observation(index, cfs))
        watch = store.load(Basin.SHASTA)
        assert watch.consecutive_below == 2
        assert watch.below_since == observation(3, 46.0).observed_at

    def test_a_recovery_resets_the_run(self) -> None:
        store = InMemoryWatchStore()
        for index, cfs in enumerate([45.0, 44.0, 60.0]):
            store.append(Basin.SHASTA, observation(index, cfs))
        watch = store.load(Basin.SHASTA)
        assert watch.consecutive_below == 0
        assert watch.below_since is None

    def test_an_empty_watch_has_no_run(self) -> None:
        watch = InMemoryWatchStore().load(Basin.SHASTA)
        assert watch.consecutive_below == 0
        assert watch.below_since is None

    def test_a_re_poll_of_the_same_reading_is_a_no_op(self) -> None:
        """USGS stamps each reading, and two polls landing between publications see the
        same one. Keying on the poll instead would manufacture a new data point every
        firing, and a run of identical values would read as a river holding steady under
        observation rather than one nobody had a fresh reading for."""
        store = InMemoryWatchStore()
        _, first = store.append(Basin.SHASTA, observation(0, 45.0))
        _, second = store.append(Basin.SHASTA, observation(0, 45.0))
        assert first is True
        assert second is False
        assert len(store.load(Basin.SHASTA).observations) == 1

    def test_readings_are_ordered_by_when_they_were_observed(self) -> None:
        """They can arrive out of order, and a run computed over arrival order would be a
        run through a history that never happened."""
        store = InMemoryWatchStore()
        store.append(Basin.SHASTA, observation(5, 60.0))
        store.append(Basin.SHASTA, observation(0, 45.0))
        observed = [item.observed_at for item in store.load(Basin.SHASTA).observations]
        assert observed == sorted(observed)

    def test_the_two_timestamps_are_kept_apart(self) -> None:
        """A large gap between them means the watch was down or the gage was late, and
        collapsing them into one field would erase the only evidence of that."""
        item = observation(0, 45.0)
        assert item.recorded_at > item.observed_at

    def test_memory_says_it_is_not_durable(self) -> None:
        store = InMemoryWatchStore()
        watch = store.load(Basin.SHASTA)
        assert watch.durable is False
        assert "may mean the record was lost" in watch.storage

    def test_basins_do_not_share_a_history(self) -> None:
        store = InMemoryWatchStore()
        store.append(Basin.SHASTA, observation(0, 45.0))
        assert len(store.load(Basin.SCOTT).observations) == 0


class TestThePollEndpoint:
    @staticmethod
    def client() -> Any:
        from fastapi.testclient import TestClient

        from curtail_agents.api import app

        return TestClient(app)

    def test_an_unauthenticated_poll_is_refused_and_writes_nothing(self, monkeypatch: Any) -> None:
        """The whole point of verifying in the handler. `/internal/` is a name."""
        from curtail_agents import api

        store = InMemoryWatchStore()
        monkeypatch.setattr(api, "watch_store", lambda: store)
        response = self.client().post("/internal/poll/shasta")
        assert response.status_code == 403
        assert len(store.load(Basin.SHASTA).observations) == 0

    def test_an_unknown_basin_is_a_404(self) -> None:
        response = self.client().post("/internal/poll/colorado")
        assert response.status_code == 404

    def test_the_public_watch_reports_durability(self, monkeypatch: Any) -> None:
        from curtail_agents import api

        store = InMemoryWatchStore()
        store.append(Basin.SHASTA, observation(0, 45.0))
        monkeypatch.setattr(api, "watch_store", lambda: store)

        body = self.client().get("/api/watch/shasta").json()
        assert body["count"] == 1
        assert body["durable"] is False
        assert body["consecutive_below_minimum"] == 1
        assert body["recommendation_only"] is True
        assert "875(b)" in body["note"]

    def test_a_store_failure_is_a_503_not_an_empty_history(self, monkeypatch: Any) -> None:
        """An empty list is a claim the river was never observed, which is a different
        statement from "the store did not answer"."""
        from curtail_agents import api
        from curtail_agents.watch_store import WatchStoreUnavailableError

        class Broken:
            durable = True

            def describe(self) -> str:
                return "broken"

            def load(self, _basin: Basin, *, limit: int = 200) -> Watch:
                raise WatchStoreUnavailableError("Firestore did not answer")

            def append(self, _basin: Basin, _observation: Observation) -> Any:
                raise WatchStoreUnavailableError("Firestore did not answer")

        monkeypatch.setattr(api, "watch_store", Broken)
        response = self.client().get("/api/watch/shasta")
        assert response.status_code == 503
        assert "did not answer" in response.json()["detail"]


class FakeSnapshot:
    def __init__(self, state: dict[str, Any], exists: bool = True) -> None:
        self._state = state
        self.exists = exists

    def to_dict(self) -> dict[str, Any]:
        return self._state


class FakeDocument:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store = store
        self._key = key

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self._store.get(self._key, {}), exists=self._key in self._store)

    def set(self, state: dict[str, Any]) -> None:
        self._store[self._key] = state

    def collection(self, _name: str) -> FakeCollection:
        """The store addresses `collection(x).document(basin).collection("observations")`,
        so a document has to be able to hold one."""
        return FakeCollection(self._store)


class FakeCollection:
    """Stands in for Firestore, and models the one property being relied on: the document
    id IS the reading key, so a second write of the same reading addresses the same
    document rather than appending a second one."""

    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> Any:
        return FakeDocument(self._store, key)

    def collection(self, _name: str) -> FakeCollection:
        return self

    def stream(self) -> list[FakeSnapshot]:
        return [FakeSnapshot(state) for state in self._store.values()]


class FakeClient:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    def collection(self, _name: str) -> FakeCollection:
        return FakeCollection(self.store)


class TestTheDurableStore:
    """The Firestore path, against a fake that models document-id addressing.

    Idempotency here is a property of the STORAGE rather than of a read-then-write: the
    document id is the reading key, so two instances polling concurrently converge on one
    document instead of racing. A check-then-act would have to promise something it
    cannot.
    """

    def store(self) -> Any:
        from curtail_agents.watch_store import FirestoreWatchStore

        return FirestoreWatchStore(FakeClient())

    def test_it_reports_itself_durable(self) -> None:
        store = self.store()
        assert store.durable is True
        assert "Firestore" in store.describe()
        assert store.load(Basin.SHASTA).durable is True

    def test_an_observation_round_trips(self) -> None:
        store = self.store()
        store.append(Basin.SHASTA, observation(0, 45.0))
        loaded = store.load(Basin.SHASTA).observations
        assert len(loaded) == 1
        assert loaded[0].cfs == 45.0
        assert loaded[0].below_minimum is True
        assert loaded[0].observed_at == observation(0, 45.0).observed_at

    def test_the_same_reading_written_twice_stays_one_document(self) -> None:
        store = self.store()
        _, first = store.append(Basin.SHASTA, observation(0, 45.0))
        _, second = store.append(Basin.SHASTA, observation(0, 45.0))
        assert (first, second) == (True, False)
        assert len(store.load(Basin.SHASTA).observations) == 1

    def test_it_returns_them_in_observation_order(self) -> None:
        store = self.store()
        store.append(Basin.SHASTA, observation(5, 60.0))
        store.append(Basin.SHASTA, observation(0, 45.0))
        seen = [item.observed_at for item in store.load(Basin.SHASTA).observations]
        assert seen == sorted(seen)

    def test_a_read_failure_raises_rather_than_returning_nothing(self) -> None:
        from curtail_agents.watch_store import FirestoreWatchStore, WatchStoreUnavailableError

        class Exploding:
            def collection(self, _name: str) -> Any:
                raise RuntimeError("permission denied")

        with pytest.raises(WatchStoreUnavailableError, match="could not be read"):
            FirestoreWatchStore(Exploding()).load(Basin.SHASTA)

    def test_a_write_failure_raises(self) -> None:
        from curtail_agents.watch_store import FirestoreWatchStore, WatchStoreUnavailableError

        class RefusingCollection(FakeCollection):
            def document(self, key: str) -> Any:
                raise RuntimeError("quota exceeded")

        class HalfBroken(FakeClient):
            """Reads fine, refuses to address a document. That asymmetry is the point:
            the failure happens while RESOLVING the document, which is the step that was
            outside the try."""

            def collection(self, _name: str) -> Any:
                return RefusingCollection(self.store)

        with pytest.raises(WatchStoreUnavailableError, match="could not be written"):
            FirestoreWatchStore(HalfBroken()).append(Basin.SHASTA, observation(0, 45.0))


class TestBuildingTheStore:
    def test_without_a_project_it_is_memory_and_says_why(self, monkeypatch: Any) -> None:
        """A deploy that replaced the environment once dropped a durable store to memory
        while every route kept answering. The reason travels with the store."""
        from curtail_agents.watch_store import build_watch_store

        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        store = build_watch_store()
        assert store.durable is False
        assert "GOOGLE_CLOUD_PROJECT is not set" in store.describe()


class TestAPollThatSucceeds:
    """The success path, with the gage, the clock and the auth all injected.

    Reaching USGS from a test would make somebody else's rate limit into this suite's
    flakiness, which has already happened here three times.
    """

    @staticmethod
    def client() -> Any:
        from fastapi.testclient import TestClient

        from curtail_agents.api import app

        return TestClient(app)

    @pytest.fixture
    def polled(self, monkeypatch: Any) -> Any:
        from curtail_agents import api, live_gage
        from curtail_agents.events import Provenance
        from curtail_agents.gage_client import Reading
        from curtail_agents.live_gage import LiveReading

        store = InMemoryWatchStore()
        monkeypatch.setattr(api, "watch_store", lambda: store)
        monkeypatch.setattr(api, "verify_scheduler", lambda _header: SCHEDULER)

        observed = datetime(2026, 6, 16, 12, tzinfo=UTC)

        async def read(_basin: Basin) -> LiveReading:
            return LiveReading(
                reading=Reading(
                    monitoring_location_id="USGS-11517500",
                    observed_at=observed,
                    cfs=45.3,
                    unit="ft^3/s",
                    qualifier=None,
                ),
                provenance=Provenance.USGS_LIVE,
                fetched_at=observed,
                age_seconds=0.0,
            )

        monkeypatch.setattr(live_gage.READER, "read", read)
        return store

    def test_it_records_the_reading_and_reports_the_run(self, polled: Any) -> None:
        body = self.client().post("/internal/poll/shasta").json()
        assert body["appended"] is True
        assert body["cfs"] == 45.3
        assert body["minimum_cfs"] == 50.0
        assert body["consecutive_below_minimum"] == 1
        assert body["polled_by"] == SCHEDULER
        assert body["durable"] is False
        assert len(polled.load(Basin.SHASTA).observations) == 1

    def test_polling_twice_records_once(self, polled: Any) -> None:
        """The same USGS publication seen by two firings."""
        client = self.client()
        client.post("/internal/poll/shasta")
        second = client.post("/internal/poll/shasta").json()
        assert second["appended"] is False
        assert second["already_recorded"] is True
        assert len(polled.load(Basin.SHASTA).observations) == 1

    def test_the_stored_minimum_is_the_one_it_was_classified_against(self, polled: Any) -> None:
        """Taken from the event rather than looked up a second time, so the recorded
        threshold cannot drift from the comparison that actually happened."""
        body = self.client().post("/internal/poll/shasta").json()
        stored = polled.load(Basin.SHASTA).observations[0]
        assert stored.minimum_cfs == body["minimum_cfs"]
        assert stored.classification == body["classification"]


class TestOneAudienceServesEveryBasin:
    """The defect a second-model review found in the TEMPLATE while production was fine.

    `.env.example` told a stranger to set the audience to `.../internal/poll/shasta`. One
    configured value is compared against every basin's token, so a per-basin path matches
    the Shasta job and rejects Scott with a 403 on every single firing. **The deployment
    was correct and the template disagreed with it**, which is the worst way for those two
    to differ: a healthy running system is no evidence at all that the template is a trap,
    and this repository has already recorded that config templates are where wrong claims
    survive after the prose has been fixed.
    """

    def test_one_audience_admits_every_basin(self, monkeypatch: Any) -> None:
        """The property that makes a single value correct, asserted directly. Cloud
        Scheduler is told the same root for both jobs, so both mint an identical audience
        while addressing different paths."""
        root = "https://curtail-console-api-672785135387.us-central1.run.app"
        monkeypatch.setenv(AUDIENCE_ENV, root)
        for basin in ("shasta", "scott"):
            token = claims(aud=root)
            assert verify("Bearer t", verifier=verifier_returning(token)) == SCHEDULER, (
                f"the {basin} job's token was refused by the shared audience"
            )

    def test_a_path_audience_refuses_the_other_basin_and_says_why(self, monkeypatch: Any) -> None:
        """The misconfiguration reproduced through a verifier that behaves like the real
        one, which is the whole correction here.

        **The first version of this test used a fake that RETURNED mismatched claims, and
        the library RAISES.** So it exercised a branch production can never reach, and the
        actionable message it asserted would never have been printed. A fake that does not
        model the real contract is not a test of the real code path.
        """
        shasta_path = "https://service/internal/poll/shasta"
        monkeypatch.setenv(AUDIENCE_ENV, shasta_path)

        # Shasta's own token still verifies, which is exactly what makes this dangerous:
        # the watch half works and nothing looks broken.
        assert verify("Bearer t", verifier=like_google(aud=shasta_path))

        scott_path = "https://service/internal/poll/scott"
        with pytest.raises(SchedulerAuthError, match="must be the service root"):
            verify("Bearer t", verifier=like_google(aud=scott_path))

    def test_the_library_raises_on_a_wrong_audience_rather_than_returning(self) -> None:
        """The premise the fix rests on, pinned against the installed library with a real
        signed token rather than assumed.

        It has to be a real signature: `jwt.decode` returns early on `verify=False` and
        never reaches the audience check at all, so an unsigned token would have "passed"
        this test while proving nothing. The key is generated here, so it is offline and
        deterministic.

        If a future version ever RETURNS the mismatched claims instead of raising, this
        fails and the hint belongs back after the call.
        """
        import datetime as stdlib_datetime

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from google.auth import crypt, jwt

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        issued = int(stdlib_datetime.datetime.now(stdlib_datetime.UTC).timestamp())
        # google.auth ships no type information for these, and the alternative to four
        # ignores is not pinning the premise at all.
        token = jwt.encode(  # type: ignore[no-untyped-call]
            crypt.RSASigner.from_string(private_pem),  # type: ignore[no-untyped-call]
            {
                "aud": "https://minted-for-something-else",
                "email": SCHEDULER,
                "iat": issued,
                "exp": issued + 600,
            },
        )

        with pytest.raises(Exception, match="wrong audience"):
            jwt.decode(  # type: ignore[no-untyped-call]
                token, certs=public_pem, audience="https://expected"
            )

        # The control, so the test above cannot be passing because the token is simply
        # unusable for some unrelated reason.
        decoded = jwt.decode(  # type: ignore[no-untyped-call]
            token, certs=public_pem, audience="https://minted-for-something-else"
        )
        assert decoded["email"] == SCHEDULER

    def test_the_template_does_not_teach_a_path_audience(self) -> None:
        """The guard on the artifact a stranger actually follows. The code was never
        wrong; the instructions were."""
        from pathlib import Path

        repo = Path(__file__).resolve().parents[2]
        line = next(
            entry
            for entry in (repo / ".env.example").read_text().splitlines()
            if entry.startswith(f"{AUDIENCE_ENV}=")
        )
        value = line.split("=", 1)[1].strip()
        assert value.startswith("https://"), value
        remainder = value.removeprefix("https://")
        assert "/" not in remainder.rstrip("/"), (
            f"{AUDIENCE_ENV} in .env.example carries a path ({value}). One value is checked "
            "against every basin's token, so a per-basin path admits one river and 403s "
            "the rest, on every firing, forever."
        )
