"""The watcher that tells us the iOS build moved, and the two ways it went quiet.

Both defects here were found by a second model reviewing the merged code, and both were
INVISIBLE on the machine that wrote them:

- the `osascript` fallback was a syntax error, and `terminal-notifier` is installed here,
  so the broken branch never ran locally and only ever would have run on the machine
  that has no `terminal-notifier`;
- change detection compared two hand-picked fields, so an expiry, a new build, or a
  moved external state passed in silence, and nothing about the code looked wrong.

A watcher that goes quiet is indistinguishable from a watcher reporting no change, which
is why these get tests rather than a fix and a shrug.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "watch_testflight.py"


def _module() -> Any:
    spec = importlib.util.spec_from_file_location("watch_testflight", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watcher = _module()


class TestTheAppleScriptFallbackIsActuallyAppleScript:
    def test_a_message_becomes_a_double_quoted_literal(self) -> None:
        """AppleScript has no single-quoted string. Python's `repr` emits one, and
        interpolating it produced `display notification 'text'`, which osascript
        rejects with a syntax error before displaying anything."""
        assert watcher.applescript_string("hello") == '"hello"'

    def test_an_embedded_quote_is_escaped_rather_than_ending_the_literal(self) -> None:
        assert watcher.applescript_string('say "no"') == '"say \\"no\\""'

    def test_a_backslash_is_escaped_before_the_quotes_are(self) -> None:
        """Order matters. Escaping quotes first would then double the backslash the
        quote-escape just introduced, and the literal would end early."""
        assert watcher.applescript_string("a\\b") == '"a\\\\b"'

    @pytest.mark.skipif(sys.platform != "darwin", reason="osascript is macOS only")
    def test_osascript_compiles_the_command_this_builds(self) -> None:
        """The real check, run where the interpreter exists.

        `osacompile` parses without displaying anything, so this asserts the syntax
        the fallback emits rather than the syntax of some simpler stand-in. The
        pre-fix form fails this, which is the whole point.
        """
        message = 'Build 1: "ready", 45.3 cfs \\ 50'
        source = (
            f"display notification {watcher.applescript_string(message)} "
            f"with title {watcher.applescript_string('Curtail TestFlight')}"
        )
        compiled = subprocess.run(
            ["osacompile", "-o", "/dev/null", "-e", source],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert compiled.returncode == 0, compiled.stderr

    @pytest.mark.skipif(sys.platform != "darwin", reason="osascript is macOS only")
    def test_the_repr_form_that_shipped_does_not_compile(self) -> None:
        """Guards the guard. If `osacompile` accepted anything, the test above would
        pass without proving anything, so the known-bad form must be rejected."""
        message = "Build 1 needs the export compliance answer."
        broken = f'display notification {message!r} with title "Curtail TestFlight"'
        compiled = subprocess.run(
            ["osacompile", "-o", "/dev/null", "-e", broken],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert compiled.returncode != 0


class TestNotifyReportsWhetherItActuallyNotified:
    def test_a_delivered_notification_returns_true(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(watcher.subprocess, "run", lambda *a, **k: None)
        assert watcher.notify("moved") is True

    def test_every_notifier_failing_returns_false(self, monkeypatch: Any) -> None:
        """The caller PRINTS what happened. A swallowed exception that still let the
        caller print "notified" would be a claim about the world it cannot make."""

        def explode(*args: Any, **kwargs: Any) -> None:
            raise FileNotFoundError("no such notifier")

        monkeypatch.setattr(watcher.subprocess, "run", explode)
        assert watcher.notify("moved") is False

    def test_the_second_notifier_is_tried_when_the_first_is_absent(self, monkeypatch: Any) -> None:
        tried: list[str] = []

        def run(command: list[str], **kwargs: Any) -> None:
            tried.append(command[0])
            if command[0] == "terminal-notifier":
                raise FileNotFoundError("not installed")

        monkeypatch.setattr(watcher.subprocess, "run", run)
        assert watcher.notify("moved") is True
        assert tried == ["terminal-notifier", "osascript"]


class TestEveryFieldIsWatched:
    """The shipped version compared `internal_state` and `beta_review_state` only."""

    BASE: MappingProxyType[str, Any] = MappingProxyType(
        {
            "version": "1",
            "processing": "VALID",
            "expired": False,
            "internal_state": "MISSING_EXPORT_COMPLIANCE",
            "external_state": "MISSING_EXPORT_COMPLIANCE",
            "beta_review_state": None,
            "installable_internally": False,
            "uploaded_at": "2026-08-15T16:00:45-07:00",
        }
    )

    def test_an_unchanged_state_is_quiet(self) -> None:
        assert watcher.changes(self.BASE, dict(self.BASE)) == {}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("processing", "PROCESSING"),
            ("expired", True),
            ("external_state", "READY_FOR_BETA_TESTING"),
            ("version", "2"),
            ("uploaded_at", "2026-08-20T09:00:00-07:00"),
            ("installable_internally", True),
        ],
    )
    def test_a_field_the_old_version_ignored_now_reports(self, field: str, value: Any) -> None:
        """Each of these passed in silence before. An expired build and a newly
        uploaded build are exactly the events a human wants pushed at them."""
        moved = watcher.changes(self.BASE, {**self.BASE, field: value})
        assert moved == {field: (self.BASE[field], value)}

    def test_the_transition_we_are_actually_waiting_for_reports(self) -> None:
        after = {
            **self.BASE,
            "internal_state": "READY_FOR_BETA_TESTING",
            "installable_internally": True,
        }
        assert set(watcher.changes(self.BASE, after)) == {
            "internal_state",
            "installable_internally",
        }

    def test_a_first_run_with_no_history_reports_everything(self) -> None:
        """No previous state is not "no change". The baseline is worth one message."""
        assert watcher.changes({}, self.BASE).keys() == self.BASE.keys()

    def test_no_field_of_the_reported_state_is_excluded_from_watching(self) -> None:
        """The structural guard against this defect returning.

        `changes` derives its key set from the state itself, so a field added to
        `state()` is watched the day it is added. This asserts that property rather
        than a list of field names that would drift.
        """
        invented = {**self.BASE, "some_future_field": "old"}
        moved = watcher.changes(invented, {**invented, "some_future_field": "new"})
        assert moved == {"some_future_field": ("old", "new")}


class TestUnknownIsNeverReportedAsNotApproved:
    def test_an_unaskable_question_exits_two_not_one(self, monkeypatch: Any) -> None:
        """Exit 1 means asked-and-not-yet. Exit 2 means could-not-ask. Collapsing
        them would let "I never reached Apple" read as "Apple has not approved it",
        which is the failure that costs a deadline."""

        def refuse() -> dict[str, Any]:
            raise watcher.WatchError("App Store Connect was unreachable")

        monkeypatch.setattr(watcher, "state", refuse)
        assert watcher.main([]) == 2

    def test_a_pending_build_exits_one(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            watcher, "state", lambda: {**TestEveryFieldIsWatched.BASE, "version": "1"}
        )
        assert watcher.main([]) == 1

    def test_a_ready_build_exits_zero(self, monkeypatch: Any) -> None:
        ready = {
            **TestEveryFieldIsWatched.BASE,
            "internal_state": "READY_FOR_BETA_TESTING",
            "installable_internally": True,
        }
        monkeypatch.setattr(watcher, "state", lambda: ready)
        assert watcher.main([]) == 0


class TestTheSummaryNamesTheThingInTheWay:
    def test_export_compliance_is_named_as_a_human_step(self) -> None:
        line = watcher.summarise(TestEveryFieldIsWatched.BASE)
        assert "export compliance" in line

    def test_a_ready_build_says_it_installs_now(self) -> None:
        line = watcher.summarise({**TestEveryFieldIsWatched.BASE, "installable_internally": True})
        assert "READY TO TEST" in line
