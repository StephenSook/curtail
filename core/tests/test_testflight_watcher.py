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
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization

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

    def test_the_command_notify_actually_builds_quotes_the_message(self) -> None:
        """Asserted at the CALL SITE, on every platform, and deliberately not
        through `osacompile`.

        `osacompile` exists only on macOS, so a test using it skips on the Linux
        runner, and this repository fails CI on any skip because a skipped guard is a
        false green. The defect is a quoting defect, and quoting is checkable
        anywhere, so it is checked anywhere. The osacompile run happened once by hand
        against both the fixed and the shipped form; the standing guard is this.
        """
        built: list[list[str]] = []

        def capture(command: list[str], **kwargs: Any) -> None:
            built.append(command)
            if command[0] == "terminal-notifier":
                raise FileNotFoundError("not installed")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(watcher.subprocess, "run", capture)
            watcher.notify('Build 1: "ready" \\ 45.3 cfs')

        script = built[-1][-1]
        assert built[-1][0] == "osascript"
        assert script.startswith('display notification "')
        assert "'" not in script, (
            "AppleScript has no single-quoted string. This is the exact byte pattern "
            "that shipped and failed with syntax error -2741 on every run."
        )
        # The escapes survive into the literal rather than ending it early.
        assert '\\"ready\\"' in script
        assert "\\\\ 45.3" in script


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


class TestAFailedNotificationIsRetriedRatherThanForgotten:
    """The worst defect the adversarial pass found, and the one the watcher exists
    to not have.

    The baseline used to advance BEFORE the notification was attempted, so a notifier
    that was merely unavailable for one run made every later run see no change. The
    single message the whole thing exists to deliver would be swallowed, permanently,
    with nothing red anywhere.
    """

    READY: MappingProxyType[str, Any] = MappingProxyType(
        {
            **TestEveryFieldIsWatched.BASE,
            "internal_state": "READY_FOR_BETA_TESTING",
            "installable_internally": True,
        }
    )

    def _run(self, tmp: Path, monkeypatch: Any, *, delivers: bool) -> str:
        monkeypatch.setattr(watcher, "STATE_FILE", tmp)
        monkeypatch.setattr(watcher, "state", lambda: dict(self.READY))
        monkeypatch.setattr(watcher, "notify", lambda message: delivers)
        watcher.main(["--notify"])
        return tmp.read_text() if tmp.exists() else ""

    def test_a_failed_delivery_holds_the_baseline(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(dict(TestEveryFieldIsWatched.BASE)))
        self._run(state_file, monkeypatch, delivers=False)

        held = json.loads(state_file.read_text())
        assert held["internal_state"] == "MISSING_EXPORT_COMPLIANCE", (
            "the baseline advanced despite nothing being delivered, so the next run "
            "sees no change and the alert is lost"
        )
        assert "NOT notified" in capsys.readouterr().out

    def test_the_next_run_after_a_failure_retries_and_then_settles(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        """End to end: fail, retry, succeed, go quiet. Four runs, one message."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(dict(TestEveryFieldIsWatched.BASE)))

        self._run(state_file, monkeypatch, delivers=False)
        capsys.readouterr()
        self._run(state_file, monkeypatch, delivers=False)
        assert "NOT notified" in capsys.readouterr().out, "the retry did not happen"

        self._run(state_file, monkeypatch, delivers=True)
        assert "(notified:" in capsys.readouterr().out

        self._run(state_file, monkeypatch, delivers=True)
        assert "no change since last run" in capsys.readouterr().out, (
            "it kept notifying after a successful delivery, which is the noise that "
            "makes somebody turn a watcher off"
        )


def _with_review(envelope: Any, data: Any) -> dict[str, Any]:
    """The envelope with `betaAppReviewSubmission` pointing at `data`."""
    build = envelope["data"][0]
    return {
        **envelope,
        "data": [
            {
                **build,
                "relationships": {
                    **build["relationships"],
                    "betaAppReviewSubmission": {"links": {}, "data": data},
                },
            }
        ],
    }


def _with_reviewed(envelope: Any, attributes: Any) -> dict[str, Any]:
    """The envelope with a review submission that IS referenced and IS included."""
    referenced = _with_review(envelope, {"type": "betaAppReviewSubmissions", "id": "sub-1"})
    return {
        **referenced,
        "included": [
            *referenced["included"],
            {"type": "betaAppReviewSubmissions", "id": "sub-1", "attributes": attributes},
        ],
    }


class TestAMalformedAnswerIsNotAPendingAnswer:
    """`state()` used to turn a missing field into None, which made
    `installable_internally` False, which made main() exit 1 and print "not yet".
    UNKNOWN wearing the answer's clothes."""

    ENVELOPE: MappingProxyType[str, Any] = MappingProxyType(
        {
            "data": [
                {
                    "attributes": {
                        "version": "1",
                        "processingState": "VALID",
                        "expired": False,
                        "uploadedDate": "2026-08-15T16:00:45-07:00",
                    },
                    # The shape Apple actually returns, read off the live envelope:
                    # every relationship carries `data` and `links`, and `data` is null
                    # when the resource genuinely does not exist.
                    "relationships": {
                        "buildBetaDetail": {
                            "links": {},
                            "data": {"type": "buildBetaDetails", "id": "detail-1"},
                        },
                        "betaAppReviewSubmission": {"links": {}, "data": None},
                    },
                }
            ],
            "included": [
                {
                    "type": "buildBetaDetails",
                    "id": "detail-1",
                    "attributes": {
                        "internalBuildState": "MISSING_EXPORT_COMPLIANCE",
                        "externalBuildState": "MISSING_EXPORT_COMPLIANCE",
                    },
                }
            ],
        }
    )

    def _state(self, envelope: Any, monkeypatch: Any) -> dict[str, Any]:
        monkeypatch.setenv("ASC_KEY_ID", "K")
        monkeypatch.setenv("ASC_ISSUER_ID", "I")
        monkeypatch.setenv("ASC_APP_ID", "A")
        monkeypatch.setattr(watcher, "token", lambda *a, **k: "bearer")
        monkeypatch.setattr(watcher, "_get", lambda *a, **k: envelope)
        return dict(watcher.state())

    def test_a_well_formed_envelope_reads(self, monkeypatch: Any) -> None:
        """Guards the guards below. If everything raised, they would pass vacuously."""
        read = self._state(self.ENVELOPE, monkeypatch)
        assert read["internal_state"] == "MISSING_EXPORT_COMPLIANCE"
        assert read["beta_review_state"] is None

    @pytest.mark.parametrize(
        ("name", "mutate"),
        [
            ("no attributes on the build", lambda e: {"data": [{"relationships": {}}]}),
            (
                "buildBetaDetail referenced but not included",
                lambda e: {**e, "included": []},
            ),
            (
                "no buildBetaDetail relationship at all",
                lambda e: {**e, "data": [{**e["data"][0], "relationships": {}}]},
            ),
            (
                "included record carries no internalBuildState",
                lambda e: {
                    **e,
                    "included": [{"type": "buildBetaDetails", "id": "detail-1", "attributes": {}}],
                },
            ),
            ("the build resource is not an object", lambda e: {"data": ["nope"]}),
            (
                "an included item has no type, so it cannot be identified",
                lambda e: {**e, "included": [{"id": "detail-1", "attributes": {}}]},
            ),
            (
                "an included item has no id",
                lambda e: {**e, "included": [{"attributes": {}}]},
            ),
            # The OPTIONAL relationship, which the first fix let degrade silently.
            # Apple saying a submission exists and us failing to read it is not the
            # same fact as Apple saying there is none, and reporting the second when
            # the first happened is the whole defect this function removes.
            (
                "a review submission exists but was not included",
                lambda e: _with_review(e, {"type": "betaAppReviewSubmissions", "id": "sub-1"}),
            ),
            (
                "a review submission is referenced without an id",
                lambda e: _with_review(e, {"type": "betaAppReviewSubmissions"}),
            ),
            # Having the RESOURCE is not having the VALUE. A submission read without a
            # betaReviewState used to report None, which is what this reports when
            # there is no submission at all, so the two were indistinguishable.
            (
                "a review submission was read but carries no betaReviewState",
                lambda e: _with_reviewed(e, {}),
            ),
            (
                "a review submission carries a non-string betaReviewState",
                lambda e: _with_reviewed(e, {"betaReviewState": 7}),
            ),
            (
                "buildBetaDetail carries no externalBuildState",
                lambda e: {
                    **e,
                    "included": [
                        {
                            "type": "buildBetaDetails",
                            "id": "detail-1",
                            "attributes": {"internalBuildState": "MISSING_EXPORT_COMPLIANCE"},
                        }
                    ],
                },
            ),
            (
                "the review linkage carries no data member at all",
                lambda e: {
                    **e,
                    "data": [
                        {
                            **e["data"][0],
                            "relationships": {
                                **e["data"][0]["relationships"],
                                "betaAppReviewSubmission": {"links": {}},
                            },
                        }
                    ],
                },
            ),
        ],
    )
    def test_a_shape_problem_raises_rather_than_reading_as_not_ready(
        self, name: str, mutate: Any, monkeypatch: Any
    ) -> None:
        with pytest.raises(watcher.WatchError):
            self._state(mutate(self.ENVELOPE), monkeypatch)

    def test_an_explicit_null_review_submission_is_an_answer(self, monkeypatch: Any) -> None:
        """`data: null` is Apple saying there IS no external submission. That is the
        live shape and the common case, so it must stay readable or every run of the
        normal path would report UNKNOWN."""
        assert self._state(self.ENVELOPE, monkeypatch)["beta_review_state"] is None

    def test_a_readable_review_submission_is_reported(self, monkeypatch: Any) -> None:
        """The other side of the same guard. Without this, raising on every reference
        would satisfy the cases above while making the external path unreadable."""
        envelope = _with_reviewed(self.ENVELOPE, {"betaReviewState": "IN_REVIEW"})
        assert self._state(envelope, monkeypatch)["beta_review_state"] == "IN_REVIEW"

    def test_an_unexpected_exception_still_exits_two(self, monkeypatch: Any) -> None:
        """The last line of defence. A traceback under launchd is a silent watcher."""

        def explode() -> dict[str, Any]:
            raise KeyError("some shape nobody predicted")

        monkeypatch.setattr(watcher, "state", explode)
        assert watcher.main([]) == 2


class TestTheBaselineSurvivesACrashAndAGarbageFile:
    @pytest.mark.parametrize("content", ["", "{ half a fi", "null", "[1, 2]", '"text"'])
    def test_an_unusable_baseline_degrades_to_no_history(
        self, content: str, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """`null` and `[1,2]` are VALID json, so a ValueError guard alone let them
        through to `previous.get` and an AttributeError. Degrading to no-history
        re-notifies once, which is the harmless direction."""
        state_file = tmp_path / "state.json"
        state_file.write_text(content)
        monkeypatch.setattr(watcher, "STATE_FILE", state_file)
        assert watcher.read_baseline() == {}

    def test_a_missing_baseline_is_not_an_error(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(watcher, "STATE_FILE", tmp_path / "never-written.json")
        assert watcher.read_baseline() == {}

    def test_the_write_is_atomic_so_a_reader_never_sees_half_a_file(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """`write_text` truncates in place. `os.replace` swaps whole files, so a
        concurrent reader sees the old content or the new content, never neither."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"internal_state": "OLD"}))
        monkeypatch.setattr(watcher, "STATE_FILE", state_file)

        seen: list[str] = []
        real_replace = watcher.os.replace

        def replace(src: Any, dst: Any) -> None:
            seen.append(Path(dst).read_text())  # what a reader would see mid-write
            real_replace(src, dst)

        monkeypatch.setattr(watcher.os, "replace", replace)
        watcher.write_baseline({"internal_state": "NEW"})

        assert json.loads(seen[0])["internal_state"] == "OLD"
        assert json.loads(state_file.read_text())["internal_state"] == "NEW"
        assert list(tmp_path.glob("*.tmp")) == [], "the temp file was left behind"

    def test_an_unwritable_baseline_does_not_crash_the_run(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any
    ) -> None:
        """A full disk is how this session started. Losing the baseline costs a
        duplicate notification; crashing costs every future one."""
        monkeypatch.setattr(watcher, "STATE_FILE", tmp_path / "no-such-dir" / "state.json")
        watcher.write_baseline({"internal_state": "NEW"})
        assert "could not persist the baseline" in capsys.readouterr().err


class TestTheKeyMustBeTheCurveES256Means:
    def _key(self, curve: Any, tmp_path: Path) -> Path:
        from cryptography.hazmat.primitives.asymmetric import ec as _ec

        path = tmp_path / "key.p8"
        path.write_bytes(
            _ec.generate_private_key(curve).private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        return path

    def test_a_p256_key_signs(self, tmp_path: Path) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec as _ec

        assembled = watcher.token("K", "I", self._key(_ec.SECP256R1(), tmp_path))
        assert assembled.count(".") == 2

    def test_a_p384_key_is_refused_as_unknown_not_crashed_on(self, tmp_path: Path) -> None:
        """`r.to_bytes(32)` raises OverflowError on P-384, which under launchd is a
        traceback and a watcher that reported nothing."""
        from cryptography.hazmat.primitives.asymmetric import ec as _ec

        with pytest.raises(watcher.WatchError, match="P-256"):
            watcher.token("K", "I", self._key(_ec.SECP384R1(), tmp_path))

    def test_an_unreadable_key_is_refused_as_unknown(self, tmp_path: Path) -> None:
        path = tmp_path / "key.p8"
        path.write_text("this is not a PEM file")
        with pytest.raises(watcher.WatchError, match="unencrypted PEM"):
            watcher.token("K", "I", path)

    def test_a_missing_key_names_the_variable_that_fixes_it(self, tmp_path: Path) -> None:
        with pytest.raises(watcher.WatchError, match="ASC_KEY_PATH"):
            watcher.token("K", "I", tmp_path / "absent.p8")


class TestAResourceIsIdentifiedByTypeAndId:
    """App Store Connect gives BOTH included resources the build's own uuid.

    An `included` map keyed on id alone silently kept whichever arrived last, so one
    relationship resolved to the other's resource and then reported that resource's
    missing field as UNKNOWN. It stayed invisible until a review submission existed,
    because until then only one resource was included, which means it surfaced at
    exactly the moment the review state became the thing to watch. It is also
    order-dependent: whichever resource loses the collision is the one that fails, and
    array order is not guaranteed.

    In JSON:API identity is the (type, id) PAIR. An id alone is half a key.
    """

    #: The real envelope, ids taken verbatim from a live read. Both are the build uuid.
    BUILD_UUID = "5ba39ebf-887a-42e7-8792-7d15dde6c76c"

    def _envelope(self, *, submission_first: bool) -> dict[str, Any]:
        detail = {
            "type": "buildBetaDetails",
            "id": self.BUILD_UUID,
            "attributes": {
                "internalBuildState": "IN_BETA_TESTING",
                "externalBuildState": "WAITING_FOR_BETA_REVIEW",
            },
        }
        submission = {
            "type": "betaAppReviewSubmissions",
            "id": self.BUILD_UUID,
            "attributes": {"betaReviewState": "WAITING_FOR_REVIEW"},
        }
        order = [submission, detail] if submission_first else [detail, submission]
        return {
            "data": [
                {
                    "attributes": {
                        "version": "1",
                        "processingState": "VALID",
                        "expired": False,
                        "uploadedDate": "2026-08-15T16:00:45-07:00",
                    },
                    "relationships": {
                        "buildBetaDetail": {
                            "links": {},
                            "data": {"type": "buildBetaDetails", "id": self.BUILD_UUID},
                        },
                        "betaAppReviewSubmission": {
                            "links": {},
                            "data": {
                                "type": "betaAppReviewSubmissions",
                                "id": self.BUILD_UUID,
                            },
                        },
                    },
                }
            ],
            "included": order,
        }

    @pytest.mark.parametrize("submission_first", [True, False])
    def test_both_resources_resolve_despite_sharing_an_id(
        self, submission_first: bool, monkeypatch: Any
    ) -> None:
        """Parametrised on ARRAY ORDER, because the id-keyed version broke one
        relationship or the other depending on which arrived last, and nothing in the
        API guarantees the order."""
        read = TestAMalformedAnswerIsNotAPendingAnswer()._state(
            self._envelope(submission_first=submission_first), monkeypatch
        )
        assert read["internal_state"] == "IN_BETA_TESTING"
        assert read["external_state"] == "WAITING_FOR_BETA_REVIEW"
        assert read["beta_review_state"] == "WAITING_FOR_REVIEW"
        assert read["installable_internally"] is True

    def test_a_reference_without_a_type_is_unreadable(self, monkeypatch: Any) -> None:
        """Half a key is not a key. A reference carrying only an id cannot identify a
        resource in an envelope where two resources share that id."""
        envelope = self._envelope(submission_first=False)
        envelope["data"][0]["relationships"]["buildBetaDetail"]["data"] = {"id": self.BUILD_UUID}
        with pytest.raises(watcher.WatchError):
            TestAMalformedAnswerIsNotAPendingAnswer()._state(envelope, monkeypatch)


class TestInstallableIsNotOneState:
    """The defect that mattered most, because it fired at the moment of truth.

    `installable_internally` compared for EQUALITY against `READY_FOR_BETA_TESTING`.
    The real build went straight to `IN_BETA_TESTING`, because export compliance was
    answered on a build already attached to an internal group, so the watcher said
    false and would have exited 1 meaning "asked, and not yet" at the instant the
    answer became yes. An equality test against a multi-valued enum, where the omitted
    value was the GOOD one.
    """

    def _state(self, internal: str, monkeypatch: Any) -> dict[str, Any]:
        envelope = {
            **TestAMalformedAnswerIsNotAPendingAnswer.ENVELOPE,
            "included": [
                {
                    "type": "buildBetaDetails",
                    "id": "detail-1",
                    "attributes": {
                        "internalBuildState": internal,
                        "externalBuildState": "READY_FOR_BETA_SUBMISSION",
                    },
                }
            ],
        }
        return TestAMalformedAnswerIsNotAPendingAnswer()._state(envelope, monkeypatch)

    @pytest.mark.parametrize("internal", ["READY_FOR_BETA_TESTING", "IN_BETA_TESTING"])
    def test_both_installable_states_read_as_installable(
        self, internal: str, monkeypatch: Any
    ) -> None:
        read = self._state(internal, monkeypatch)
        assert read["installable_internally"] is True
        assert "READY TO TEST" in watcher.summarise(read)
        monkeypatch.setattr(watcher, "state", lambda: read)
        assert watcher.main([]) == 0

    @pytest.mark.parametrize(
        "internal",
        ["PROCESSING", "MISSING_EXPORT_COMPLIANCE", "IN_EXPORT_COMPLIANCE_REVIEW", "EXPIRED"],
    )
    def test_a_known_pending_state_is_a_real_answer_of_not_yet(
        self, internal: str, monkeypatch: Any
    ) -> None:
        """Guards the guard. If everything read as installable or unknown, the test
        above would pass while the watcher had stopped distinguishing anything."""
        read = self._state(internal, monkeypatch)
        assert read["installable_internally"] is False
        monkeypatch.setattr(watcher, "state", lambda: read)
        assert watcher.main([]) == 1

    def test_a_state_apple_adds_later_is_unknown_and_not_a_no(self, monkeypatch: Any) -> None:
        """The general form of the bug. An unmodelled enum value must be loud, because
        folding it into the negative branch is exactly how IN_BETA_TESTING became
        'not installable' with nothing anywhere reporting a problem."""
        read = self._state("SOME_STATE_APPLE_ADDED_IN_2027", monkeypatch)
        assert read["installable_internally"] is False
        assert "UNKNOWN" in watcher.summarise(read)
        monkeypatch.setattr(watcher, "state", lambda: read)
        assert watcher.main([]) == 2, "an unmodelled state exited 1, which reads as an answer"

    def test_every_installable_state_is_also_a_known_state(self) -> None:
        """Structural. A value in one set and not the other would make a build report
        installable and exit 2 at the same time."""
        assert watcher.INSTALLABLE_INTERNAL_STATES <= watcher.KNOWN_INTERNAL_STATES


class TestTheSummaryNamesTheThingInTheWay:
    def test_export_compliance_is_named_as_a_human_step(self) -> None:
        line = watcher.summarise(TestEveryFieldIsWatched.BASE)
        assert "export compliance" in line

    def test_a_ready_build_says_it_installs_now(self) -> None:
        line = watcher.summarise({**TestEveryFieldIsWatched.BASE, "installable_internally": True})
        assert "READY TO TEST" in line
