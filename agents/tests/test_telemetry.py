"""The telemetry boundary: it must never lie about whether spans are leaving.

ADK opens an `invoke_node` span per fleet node and an `invoke_workflow` span around
the traversal whether or not anything exports them. That makes "is opentelemetry
imported" a useless question and makes ONE question the real one: did a span
processor actually get installed. Everything here is about answering that honestly,
including when the answer is no.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry import trace as otel_trace

from curtail_agents import telemetry


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    """Module state is process-wide, so each test starts from unconfigured.

    Without this the first test to configure would make every later one pass by
    inheritance, which is the vacuous-guard shape: green because nothing ran.
    """
    telemetry._state["reason"] = "configure_tracing has not been called"
    yield
    telemetry._state["reason"] = "configure_tracing has not been called"


class TestItRefusesRatherThanGuesses:
    def test_no_project_id_is_a_stated_refusal_not_a_silent_no_op(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A surface silent about telemetry lets a reader assume it is on."""
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv(telemetry.DISABLE_ENV, raising=False)

        assert telemetry.configure_tracing() is False
        assert telemetry.is_exporting() is False
        reason = telemetry.why_not_exporting()
        assert reason is not None
        assert "GOOGLE_CLOUD_PROJECT" in reason, (
            "the refusal does not name what is missing, so nobody can fix it"
        )

    def test_the_disable_switch_wins_over_a_present_project_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The test suite sets this. A suite that exports spans to a real backend is a
        suite that fails when somebody runs it on a plane."""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "curtail-505118")
        monkeypatch.setenv(telemetry.DISABLE_ENV, "1")

        assert telemetry.configure_tracing() is False
        reason = telemetry.why_not_exporting()
        assert reason is not None and telemetry.DISABLE_ENV in reason

    def test_an_exporter_that_cannot_be_built_does_not_take_the_service_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**Observability is a governance requirement, not a precondition for
        answering.** A telemetry failure must never stop the surface that serves the
        curtailment recommendation, so this refuses and records why."""
        monkeypatch.delenv(telemetry.DISABLE_ENV, raising=False)

        def _explode(*_: object, **__: object) -> None:
            raise RuntimeError("no credentials on this machine")

        monkeypatch.setattr(telemetry, "CloudTraceSpanExporter", _explode)

        assert telemetry.configure_tracing(project_id="curtail-505118") is False
        reason = telemetry.why_not_exporting()
        assert reason is not None and "no credentials on this machine" in reason


class TestItInstallsExactlyOnce:
    def _fake(self, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        """Records every span processor added, without touching the network."""
        added: list[Any] = []
        monkeypatch.delenv(telemetry.DISABLE_ENV, raising=False)
        monkeypatch.setattr(telemetry, "CloudTraceSpanExporter", lambda **_: object())
        monkeypatch.setattr(telemetry, "BatchSpanProcessor", lambda exporter: exporter)

        class _Provider:
            def __init__(self, resource: object = None) -> None:
                self.resource = resource

            def add_span_processor(self, processor: object) -> None:
                added.append(processor)

        monkeypatch.setattr(telemetry, "TracerProvider", _Provider)
        monkeypatch.setattr(otel_trace, "set_tracer_provider", lambda _p: None)
        return added

    def test_configuring_installs_a_processor_and_reports_exporting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        added = self._fake(monkeypatch)

        assert telemetry.configure_tracing(project_id="curtail-505118") is True
        assert telemetry.is_exporting() is True
        assert telemetry.why_not_exporting() is None, (
            "exporting, so there must be no reason not to; a leftover reason would make "
            "the API report a failure that did not happen"
        )
        assert len(added) == 1

    def test_a_second_call_does_not_install_a_second_processor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**Cloud Run can import a module more than once across workers.** A second
        `BatchSpanProcessor` duplicates every span, which reads in Cloud Trace as the
        fleet having run twice: a governance surface reporting phantom work."""
        added = self._fake(monkeypatch)

        assert telemetry.configure_tracing(project_id="curtail-505118") is True
        assert telemetry.configure_tracing(project_id="curtail-505118") is True
        assert len(added) == 1, f"installed {len(added)} processors, so spans duplicate"


class TestFlush:
    def test_flush_is_safe_when_no_provider_supports_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default no-op provider has no `force_flush`. Reaching for it blindly
        would raise inside a `finally`, turning a telemetry detail into a failed
        request on the path that serves the recommendation."""
        monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: object())
        telemetry.flush()

    def test_flush_forwards_the_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Batched spans can outlive an idle container, so the flush must really fire:
        the traversal a judge just ran is the one that must be in Cloud Trace."""
        seen: list[int] = []

        class _Provider:
            def force_flush(self, timeout_millis: int) -> None:
                seen.append(timeout_millis)

        monkeypatch.setattr(otel_trace, "get_tracer_provider", lambda: _Provider())
        telemetry.flush(1234)
        assert seen == [1234]


class TestTheCorrelationAttributeIsOneString:
    def test_the_attribute_name_is_shared_rather_than_retyped(self) -> None:
        """A trace and a log line are only joinable if both use the SAME key. Two
        string literals that happen to match today are two things that can drift, so
        the API imports this constant rather than typing it again."""
        from pathlib import Path

        api = (
            Path(__file__).resolve().parents[2] / "agents" / "src" / "curtail_agents" / "api.py"
        ).read_text()
        assert "CORRELATION_ATTRIBUTE" in api, (
            "the API does not use the shared correlation attribute constant"
        )
        assert f'"{telemetry.CORRELATION_ATTRIBUTE}"' not in api, (
            "the API retypes the correlation attribute as a literal, so it can drift "
            "from the constant it is supposed to match"
        )
