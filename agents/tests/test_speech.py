"""The spoken briefing, and the several ways audio can lie.

Entirely offline. Every test stubs the transport, and nothing here reaches Google's
Text-to-Speech API. That is not a general preference, it is a rule this suite learned the
hard way: browser tests in this repo reached USGS three separate times and a 429 from a
third party broke a build that had nothing wrong with it. A test that depends on somebody
else's uptime is a test that reports their outage as your regression.

**The invariants worth stating.**

Audio has a failure mode text does not have. A wrong sentence on a screen is visible; a
briefing that plays as silence, or plays and stops mid-clause, is indistinguishable from a
briefing with nothing to say. So every path that cannot produce real speech RAISES, and the
tests below are mostly about proving that no path returns quiet.

The other half is provenance. `brief()` takes a `Recommendation`, never a string, so a
caller cannot put words in this system's mouth, and the composed script comes back beside
the audio so a reader can check what was said without listening to it.
"""

from __future__ import annotations

import base64
from datetime import date
from typing import Any

import httpx
import pytest

from curtail_agents.speech import (
    MAX_CHARACTERS,
    SpeechUnavailableError,
    brief,
    synthesise,
)
from curtail_core.allocation import recommend
from curtail_core.basins import Basin
from curtail_core.rights import rights_for

#: Long enough to pass the 512-byte floor, which is itself a guard under test.
FAKE_MP3 = b"\xff\xfb\x90\x64" + b"\x00" * 2048


class FakeCredentials:
    """Credentials that refresh without a network call and without a key on disk."""

    def __init__(self, *, token: str = "fake-token", quota_project_id: str | None = "p") -> None:
        self.token = token
        self.quota_project_id = quota_project_id
        self.refreshed = False

    def refresh(self, _request: Any) -> None:
        self.refreshed = True


def transport(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"audioContent": base64.b64encode(FAKE_MP3).decode()})


@pytest.fixture(scope="module")
def shasta() -> Any:
    record = rights_for(Basin.SHASTA)
    return recommend(
        basin=Basin.SHASTA,
        when=date(2026, 6, 16),
        observed_cfs=45.3,
        rights=record.rights,
    )


class TestTheScriptIsComposedNotSupplied:
    """A caller hands over a basin and a reading. It never hands over words.

    This is the whole reason the module exists in this shape. A general text-to-speech
    endpoint on a legal system is a machine that will read out whatever it is given in
    the voice of the system, and there is no way to audit that after the fact.
    """

    def test_brief_takes_a_recommendation_and_not_a_string(self) -> None:
        import inspect

        signature = inspect.signature(brief)
        first = next(iter(signature.parameters.values()))
        assert first.annotation == "Recommendation", (
            f"brief() takes {first.annotation}. If it ever takes a string, a caller can "
            "make this system say anything in its own voice."
        )

    def test_every_number_spoken_is_a_number_computed(self, shasta: Any) -> None:
        script = brief(shasta, observed_cfs=45.3, minimum_cfs=50.0)
        assert "45.3" in script
        assert "50.0" in script
        assert str(len(shasta.ledger)) in script

    def test_the_recommendation_is_spoken_as_a_recommendation(self, shasta: Any) -> None:
        """The limit is the point. An official who hears a conclusion without hearing
        that a human must make the determination has been told the wrong thing, and
        this project's own evidence is that reviewers accept machine drafts they
        should not: 35 to 45 percent of erroneous drafts went in unedited.
        """
        script = brief(shasta, observed_cfs=45.3, minimum_cfs=50.0)
        assert "recommendation" in script.lower()
        assert "875" in script, "the authority vesting the determination is not spoken"
        assert "human" in script.lower()

    def test_withheld_judgment_is_named_and_not_merely_counted(self, shasta: Any) -> None:
        """Saying "there are 2 things I did not resolve" and stopping tells a listener
        something was withheld without telling them what, which is worse than silence
        on the subject."""
        if not shasta.judgment_inputs:
            pytest.skip("this recommendation surfaces no judgment inputs")
        script = brief(shasta, observed_cfs=45.3, minimum_cfs=50.0)
        assert str(shasta.judgment_inputs[0]) in script


class TestNothingReturnsSilence:
    """Every failure raises. None of them returns an empty or truncated payload.

    Same family as the reading of zero standing in for a sensor that did not answer:
    a plausible-looking value in place of a missing one is worse than an error.
    """

    def test_an_empty_script_refuses(self) -> None:
        with pytest.raises(SpeechUnavailableError, match="no script"):
            synthesise("   ", client=transport(ok), credentials=FakeCredentials())

    def test_a_200_carrying_no_audio_refuses(self) -> None:
        """The status-code-says-fine failure, which this project has now met in a WAF
        challenge page, an ArcGIS error object and a lying watch command."""

        def empty(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        with pytest.raises(SpeechUnavailableError, match="no audio content"):
            synthesise("Speak.", client=transport(empty), credentials=FakeCredentials())

    def test_a_payload_too_short_to_be_speech_refuses(self) -> None:
        """A few bytes of valid base64 decode fine and play as nothing at all."""

        def tiny(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"audioContent": base64.b64encode(b"\xff\xfb").decode()}
            )

        with pytest.raises(SpeechUnavailableError, match="too short to be speech"):
            synthesise("Speak.", client=transport(tiny), credentials=FakeCredentials())

    def test_a_non_json_body_refuses(self) -> None:
        def html(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>error</html>")

        with pytest.raises(SpeechUnavailableError, match="non-JSON"):
            synthesise("Speak.", client=transport(html), credentials=FakeCredentials())

    def test_a_non_200_carries_the_status_and_the_body(self) -> None:
        """The reason must reach the caller. A bare "unavailable" sends whoever
        diagnoses it back to the same request to find out what happened."""

        def denied(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="quota project required")

        with pytest.raises(SpeechUnavailableError, match=r"403.*quota project required"):
            synthesise("Speak.", client=transport(denied), credentials=FakeCredentials())

    def test_an_unreachable_service_refuses(self) -> None:
        def boom(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        with pytest.raises(SpeechUnavailableError, match="unreachable"):
            synthesise("Speak.", client=transport(boom), credentials=FakeCredentials())

    def test_undecodable_audio_refuses(self) -> None:
        def garbage(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"audioContent": "!!!not base64!!!"})

        with pytest.raises(SpeechUnavailableError):
            synthesise("Speak.", client=transport(garbage), credentials=FakeCredentials())


class TestTheQuotaProjectIsResolvedBeforeTheRequest:
    """Under local Application Default Credentials this API answers 403 without an
    `x-goog-user-project` header, and the error reads like a permissions problem.
    Cost real time to diagnose once, so it is refused up front with the actual cause
    rather than sent to fail confusingly somewhere else."""

    def test_the_header_is_sent(self) -> None:
        seen: dict[str, str] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return ok(request)

        synthesise(
            "Speak.",
            client=transport(capture),
            credentials=FakeCredentials(quota_project_id="curtail-505118"),
        )
        assert seen.get("x-goog-user-project") == "curtail-505118"
        assert seen.get("authorization") == "Bearer fake-token"

    def test_no_known_project_refuses_before_sending(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        sent = False

        def watch(request: httpx.Request) -> httpx.Response:
            nonlocal sent
            sent = True
            return ok(request)

        with pytest.raises(SpeechUnavailableError, match="quota project"):
            synthesise(
                "Speak.",
                client=transport(watch),
                credentials=FakeCredentials(quota_project_id=None),
            )
        assert not sent, "a request certain to fail was sent anyway"

    def test_the_environment_supplies_the_project_when_credentials_do_not(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "from-env")
        seen: dict[str, str] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return ok(request)

        synthesise(
            "Speak.",
            client=transport(capture),
            credentials=FakeCredentials(quota_project_id=None),
        )
        assert seen.get("x-goog-user-project") == "from-env"


class TestTruncationIsAnnouncedOutLoud:
    """A briefing that stops mid-clause sounds like a dropped connection, and a
    listener has no way to tell a cut short from a finished one. So the cut is spoken
    and the flag comes back in the result."""

    def test_a_long_script_is_cut_and_says_so(self) -> None:
        long = ("This is a sentence about a water right. " * 400)[: MAX_CHARACTERS + 500]
        spoken = synthesise(long, client=transport(ok), credentials=FakeCredentials())
        assert spoken.truncated
        assert "too long to speak" in spoken.script
        assert len(spoken.script) <= MAX_CHARACTERS + 100

    def test_a_short_script_is_untouched(self) -> None:
        spoken = synthesise(
            "A short briefing.", client=transport(ok), credentials=FakeCredentials()
        )
        assert not spoken.truncated
        assert spoken.script == "A short briefing."


class TestTheWordsComeBackWithTheAudio:
    def test_the_result_carries_the_script_the_audio_was_made_from(self) -> None:
        spoken = synthesise(
            "The gage reads 45.3.", client=transport(ok), credentials=FakeCredentials()
        )
        assert spoken.script == "The gage reads 45.3."
        assert spoken.audio_mp3 == FAKE_MP3
        assert base64.b64decode(spoken.audio_base64) == FAKE_MP3

    def test_the_voice_is_recorded(self) -> None:
        spoken = synthesise("Speak.", client=transport(ok), credentials=FakeCredentials())
        assert spoken.voice.startswith("en-US-Chirp3-HD-"), (
            "the bonus criterion is a named Google model, so which voice spoke must be "
            "recorded rather than assumed from a default that could change"
        )

    def test_the_request_names_the_chirp_voice_and_asks_for_mp3(self) -> None:
        seen: dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            import json

            seen.update(json.loads(request.content))
            return ok(request)

        synthesise("Speak.", client=transport(capture), credentials=FakeCredentials())
        assert seen["voice"]["name"].startswith("en-US-Chirp3-HD-")
        assert seen["audioConfig"]["audioEncoding"] == "MP3"


class TestTheEndpoint:
    """`/api/brief/{basin}`, with the speech call stubbed at the module boundary.

    The endpoint composes rather than proxies, so the tests worth having are about what
    it refuses and about the words coming back beside the audio.
    """

    @staticmethod
    def client() -> Any:
        from fastapi.testclient import TestClient

        from curtail_agents.api import app

        return TestClient(app)

    @staticmethod
    def stub_speech(monkeypatch: Any, **overrides: Any) -> None:
        from curtail_agents import api
        from curtail_agents.speech import Spoken

        def fake(script: str, **_kwargs: Any) -> Spoken:
            return Spoken(
                script=overrides.get("script", script),
                audio_mp3=overrides.get("audio", FAKE_MP3),
                voice=overrides.get("voice", "en-US-Chirp3-HD-Achernar"),
                truncated=overrides.get("truncated", False),
            )

        monkeypatch.setattr(api, "synthesise", fake)

    def test_it_returns_the_script_beside_the_audio(self, monkeypatch: Any) -> None:
        self.stub_speech(monkeypatch)
        response = self.client().get(
            "/api/brief/shasta", params={"cfs": 45.3, "at": "2026-06-16T12:00:00+00:00"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["model"] == "Chirp 3 HD"
        assert body["voice"].startswith("en-US-Chirp3-HD-")
        assert base64.b64decode(body["audio_base64"]) == FAKE_MP3
        # Every figure the response reports must appear in the words that were spoken,
        # or the transcript is not a transcript of this briefing.
        assert str(body["observed_cfs"]) in body["script"]
        assert str(body["minimum_cfs"]) in body["script"]
        assert body["recommendation_only"] is True
        assert "875(b)" in body["disclaimer"]

    def test_an_unknown_basin_is_a_404(self, monkeypatch: Any) -> None:
        self.stub_speech(monkeypatch)
        response = self.client().get("/api/brief/colorado", params={"cfs": 40})
        assert response.status_code == 404
        assert "colorado" in response.json()["detail"]

    def test_speech_failing_is_a_503_carrying_the_reason(self, monkeypatch: Any) -> None:
        """Never a 200 with an empty payload. A player with nothing behind it is
        indistinguishable from a briefing with nothing to say."""
        from curtail_agents import api

        def refuse(_script: str, **_kwargs: Any) -> None:
            raise SpeechUnavailableError("no quota project is set")

        monkeypatch.setattr(api, "synthesise", refuse)
        response = self.client().get(
            "/api/brief/shasta", params={"cfs": 45.3, "at": "2026-06-16T12:00:00+00:00"}
        )
        assert response.status_code == 503
        assert "quota project" in response.json()["detail"]

    def test_an_impossible_reading_is_refused_before_anything_is_spoken(
        self, monkeypatch: Any
    ) -> None:
        """The same reading guard every other endpoint uses. A negative discharge is
        not a river, and speaking it aloud would give it more authority, not less."""
        called = False

        def watch(_script: str, **_kwargs: Any) -> None:
            nonlocal called
            called = True

        from curtail_agents import api

        monkeypatch.setattr(api, "synthesise", watch)
        response = self.client().get("/api/brief/shasta", params={"cfs": -5})
        assert response.status_code == 422
        assert not called, "an impossible reading reached the speech service"

    def test_truncation_is_reported_to_the_caller(self, monkeypatch: Any) -> None:
        self.stub_speech(monkeypatch, truncated=True)
        response = self.client().get(
            "/api/brief/shasta", params={"cfs": 45.3, "at": "2026-06-16T12:00:00+00:00"}
        )
        assert response.json()["truncated"] is True


class TestTheCredentialPathsRefuseRatherThanCrash:
    def test_a_credential_failure_is_a_speech_failure(self, monkeypatch: Any) -> None:
        """`google.auth.default()` raises when nothing is configured, and an unhandled
        DefaultCredentialsError out of an endpoint is a 500 that tells the caller
        nothing about what to fix."""
        import google.auth

        def boom(**_kwargs: Any) -> None:
            raise RuntimeError("no application default credentials")

        # Patched on the library, not through the module's attribute, because the
        # module imports `google.auth` rather than re-exporting it.
        monkeypatch.setattr(google.auth, "default", boom)
        with pytest.raises(SpeechUnavailableError, match="could not obtain credentials"):
            synthesise("Speak.", client=transport(ok))

    def test_a_refresh_failure_is_a_speech_failure(self) -> None:
        class Broken(FakeCredentials):
            def refresh(self, _request: Any) -> None:
                raise RuntimeError("token endpoint unreachable")

        with pytest.raises(SpeechUnavailableError, match="could not obtain credentials"):
            synthesise("Speak.", client=transport(ok), credentials=Broken())
