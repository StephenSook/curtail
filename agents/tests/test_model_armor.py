"""Layer 2 must never report a pass it did not earn.

The whole value of a second injection layer is that it is INDEPENDENT of the first. That
value is destroyed the moment "we could not reach the service" is reported the same way
as "we screened it and it was clean", because a caller then treats an outage as a
defence. Every test here is about keeping those two apart.

No network. The transport is faked, because a unit suite that calls a real screening API
fails offline, bills for opinions, and turns every sanitiser test into a round trip.
"""

from __future__ import annotations

import urllib.request
from typing import Any

import pytest

from curtail_agents import model_armor
from curtail_agents.model_armor import ArmorState, screen_document

CLEAN_RESPONSE: dict[str, Any] = {"sanitizationResult": {"filterMatchState": "NO_MATCH_FOUND"}}

MATCH_RESPONSE: dict[str, Any] = {
    "sanitizationResult": {
        "filterMatchState": "MATCH_FOUND",
        "filterResults": {
            "pi_and_jailbreak": {
                "piAndJailbreakFilterResult": {
                    "matchState": "MATCH_FOUND",
                    "confidenceLevel": "HIGH",
                }
            },
            "rai": {"raiFilterResult": {"matchState": "NO_MATCH_FOUND"}},
        },
    }
}


@pytest.fixture(autouse=True)
def _enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The root conftest disables screening for the whole suite. These tests are about
    the screening logic itself, so they re-enable it and fake the transport instead."""
    monkeypatch.delenv(model_armor.DISABLE_ENV, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "curtail-505118")
    monkeypatch.setattr(model_armor, "_token", lambda: "fake-token")


def _respond(
    *results: tuple[bool, list[str], str | None],
) -> list[tuple[bool, list[str], str | None]]:
    """Queue per-chunk outcomes for the faked chunk screener."""
    return list(results)


class TestUnavailableIsNeverClean:
    def test_the_disable_switch_reports_unavailable_not_clean(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(model_armor.DISABLE_ENV, "1")
        verdict = screen_document("anything")
        assert verdict.state is ArmorState.UNAVAILABLE
        assert verdict.screened is False, "a skipped layer must never report as having run"
        assert verdict.blocked is False
        assert model_armor.DISABLE_ENV in verdict.reason

    def test_a_missing_project_reports_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        verdict = screen_document("anything")
        assert verdict.state is ArmorState.UNAVAILABLE
        assert "GOOGLE_CLOUD_PROJECT" in verdict.reason

    def test_absent_credentials_report_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(model_armor, "_token", lambda: None)
        verdict = screen_document("anything")
        assert verdict.state is ArmorState.UNAVAILABLE
        assert verdict.screened is False

    def test_a_transport_failure_midway_is_unavailable_and_says_how_far_it_got(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**A partial screen is not a clean screen.** Reporting CLEAN after three of
        forty chunks would be the worst output this module can produce: the tail is
        exactly where a payload hides in a long order."""
        queue = _respond(
            (False, [], None),
            (False, [], None),
            (False, [], "URLError: connection reset"),
        )
        monkeypatch.setattr(model_armor, "_screen_chunk", lambda chunk, *, url, token: queue.pop(0))

        verdict = screen_document("x" * (model_armor.CHUNK_CHARS * 5))
        assert verdict.state is ArmorState.UNAVAILABLE
        assert verdict.screened is False
        assert "after 2 of 5 chunks" in verdict.reason, verdict.reason
        assert "connection reset" in verdict.reason


class TestItScreensTheWholeDocument:
    def test_long_text_is_chunked_so_a_buried_payload_is_still_seen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The documented injection window is near 512 TOKENS. A 110,000 character
        order screened in one call would be checked at its head only, and this corpus
        really contains one that size."""
        seen: list[str] = []

        def _fake(chunk: str, *, url: str, token: str) -> tuple[bool, list[str], str | None]:
            seen.append(chunk)
            return ("NEEDLE" in chunk, ["pi_and_jailbreak"] if "NEEDLE" in chunk else [], None)

        monkeypatch.setattr(model_armor, "_screen_chunk", _fake)

        buried = ("a" * model_armor.CHUNK_CHARS * 3) + "NEEDLE"
        verdict = screen_document(buried)

        assert len(seen) == 4, "the document was not split into chunks"
        assert verdict.state is ArmorState.MATCH_FOUND, (
            "a payload past the first chunk was missed, which is the whole reason to chunk"
        )
        assert verdict.matched_filters == ("pi_and_jailbreak",)

    def test_a_truncated_screen_is_reported_rather_than_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**No silent caps.** A document longer than the cap has an UNSCREENED tail, and
        reporting that as clean would be a coverage claim the run did not make."""
        monkeypatch.setattr(
            model_armor, "_screen_chunk", lambda chunk, *, url, token: (False, [], None)
        )
        monkeypatch.setattr(model_armor, "MAX_CHUNKS", 3)

        verdict = screen_document("y" * (model_armor.CHUNK_CHARS * 6))
        assert verdict.state is ArmorState.UNAVAILABLE, (
            "a partially screened document reported as CLEAN"
        )
        assert "NOT checked" in verdict.reason
        assert verdict.chunks_total == 6

    def test_a_clean_document_says_how_much_it_checked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            model_armor, "_screen_chunk", lambda chunk, *, url, token: (False, [], None)
        )
        verdict = screen_document("an ordinary finding about a gage reading")
        assert verdict.state is ArmorState.CLEAN
        assert verdict.screened is True
        assert verdict.blocked is False
        assert "1 of 1" in verdict.reason


class TestTheResponseShapeIsReadDefensively:
    def test_a_match_is_found_without_hard_coding_the_inner_key(self) -> None:
        """Each filter nests its verdict under a different inner key. A hard-coded path
        would report every filter clean the first time Google adds one."""
        assert model_armor._match_in(
            MATCH_RESPONSE["sanitizationResult"]["filterResults"]["pi_and_jailbreak"]
        )
        assert not model_armor._match_in(
            MATCH_RESPONSE["sanitizationResult"]["filterResults"]["rai"]
        )

    def test_a_non_dict_filter_block_does_not_crash_the_screen(self) -> None:
        """A screening layer that raises on an unexpected payload shape takes down the
        path that serves the recommendation. It returns False instead."""
        assert model_armor._match_in("unexpected") is False
        assert model_armor._match_in(None) is False

    def test_only_matching_filters_are_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Naming a filter that did not match would overstate what was detected."""
        captured: dict[str, Any] = {}

        class _Response:
            status = 200

            def read(self) -> bytes:
                import json

                return json.dumps(MATCH_RESPONSE).encode()

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: object) -> None:
                return None

        def _urlopen(request: Any, timeout: int = 0) -> _Response:
            captured["url"] = request.full_url
            return _Response()

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        verdict = screen_document("payload")

        assert verdict.matched_filters == ("pi_and_jailbreak",), verdict.matched_filters
        assert "rai" not in verdict.matched_filters

    def test_the_regional_endpoint_is_used_not_the_global_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**The trap that made this layer look unreachable for weeks.** `gcloud
        model-armor` and the global host return PERMISSION_DENIED on this account; the
        regional host answers 200 with the same credentials."""
        captured: dict[str, Any] = {}

        def _fake(chunk: str, *, url: str, token: str) -> tuple[bool, list[str], str | None]:
            captured["url"] = url
            return (False, [], None)

        monkeypatch.setattr(model_armor, "_screen_chunk", _fake)
        screen_document("text")

        assert "modelarmor.us-central1.rep.googleapis.com" in captured["url"], captured["url"]
        assert ":sanitizeUserPrompt" in captured["url"]
