"""The committed eval artifacts, and the rule that none of them may be invented.

Eval evidence is a judged criterion, which makes it exactly the place where a
placeholder score would be most tempting and most damaging. These tests assert that
every metric in the exported result either carries a real measurement or says
plainly why it has none.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

REPO = Path(__file__).resolve().parents[2]
EVAL_SET = REPO / "docs" / "evals" / "curtail_sentinel.evalset.json"
RESULTS = REPO / "docs" / "evals" / "eval_results.json"


@pytest.fixture(scope="module")
def eval_set() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(EVAL_SET.read_text())
    return data


@pytest.fixture(scope="module")
def results() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(RESULTS.read_text())
    return data


class TestTheEvalSetIsBuiltFromTheBoardsOwnRecord:
    def test_every_case_comes_from_a_real_decision(self, eval_set: dict[str, Any]) -> None:
        """Fixtures written to be passed prove nothing. Each id here is a document
        in the corpus manifest."""
        cases = json.loads((REPO / "data" / "backtest_cases.json").read_text())["cases"]
        real_ids = {c["id"] for c in cases}
        for case in eval_set["eval_cases"]:
            assert case["eval_id"] in real_ids, case["eval_id"]

    def test_it_is_not_empty(self, eval_set: dict[str, Any]) -> None:
        assert eval_set["eval_cases"], "an empty eval set passes every metric vacuously"

    def test_every_case_carries_the_reading_the_agent_must_classify(
        self, eval_set: dict[str, Any]
    ) -> None:
        for case in eval_set["eval_cases"]:
            observation = case["session_input"]["state"]["observation"]
            assert observation["observed_cfs"] is not None
            assert observation["basin"] in {"scott", "shasta"}


class TestNoMetricIsReportedWithoutBeingMeasured:
    ALLOWED: ClassVar[set[str]] = {"measured", "not_applicable", "pending_credentials"}

    def test_every_metric_declares_a_status(self, results: dict[str, Any]) -> None:
        assert results["metrics"], "a result file with no metrics claims nothing"
        for name, entry in results["metrics"].items():
            assert entry["status"] in self.ALLOWED, f"{name}: {entry['status']}"

    def test_an_unmeasured_metric_carries_no_score(self, results: dict[str, Any]) -> None:
        """The whole point. A placeholder number in an evidence artifact reads as
        evidence, and a judge has no way to tell it from a measurement."""
        for name, entry in results["metrics"].items():
            if entry["status"] != "measured":
                assert "score" not in entry, f"{name} is unmeasured but carries a score"

    def test_an_unmeasured_metric_says_why(self, results: dict[str, Any]) -> None:
        for name, entry in results["metrics"].items():
            if entry["status"] != "measured":
                assert entry.get("reason"), f"{name} is unmeasured with no reason given"

    def test_the_result_does_not_restate_the_backtest_figure(self, results: dict[str, Any]) -> None:
        """Two artifacts stating one number is two places for it to go stale, and
        this repository has already shipped that defect once in a manifest note."""
        blob = json.dumps(results)
        assert "6 of 6" not in blob
        assert "docs/FACTS.md" in blob, "it must point at the source instead"


class TestTheArtifactsCannotGoStale:
    def test_regenerating_them_produces_no_diff(self) -> None:
        """Committed artifacts drift from their generator silently. The generator's
        own --check is the gate, run here so the suite fails rather than CI alone."""
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "export_evals.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert result.returncode == 0, result.stdout + result.stderr


class TestTheExpectationIsSomethingTheAgentCanActuallyAnswer:
    """The defect a review caught, and the one that makes an eval set worthless.

    The first export put the Board's verb ("reinstate", "suspend") in
    `final_response` while the fleet answers with the Sentinel's classification
    ("reading_near_threshold", "flow_below_minimum"). Two vocabularies, so no case
    could ever match. The artifact would have scored zero across the board the
    moment credentials arrived, or invited somebody to loosen the metric until it
    passed, and a zero produced by a category error looks exactly like a zero
    produced by a broken agent.

    The expectation is now the DIRECTION both sides can be expressed in, through the
    mapping the backtest already owns.
    """

    def test_every_expected_response_is_a_direction(self, eval_set: dict[str, Any]) -> None:
        from curtail_core.backtest import Direction

        allowed = {d.value for d in Direction}
        for case in eval_set["eval_cases"]:
            answer = case["conversation"][0]["final_response"]["parts"][0]["text"]
            assert answer in allowed, f"{case['eval_id']} expects {answer!r}, not a direction"

    def test_no_expectation_is_left_in_the_boards_verb_vocabulary(
        self, eval_set: dict[str, Any]
    ) -> None:
        """Named explicitly, because the two vocabularies are both plausible strings
        and only one of them is answerable."""
        from curtail_core.backtest import ACTION_DIRECTION

        verbs = set(ACTION_DIRECTION)
        for case in eval_set["eval_cases"]:
            answer = case["conversation"][0]["final_response"]["parts"][0]["text"]
            assert answer not in verbs, (
                f"{case['eval_id']} expects the Board's verb {answer!r}. The agent "
                "answers with a classification, so this can never match."
            )

    def test_the_direction_bridge_is_shared_with_the_backtest(self) -> None:
        """One mapping, not two. A second copy is a second thing to keep correct,
        and the drift would be invisible until a metric ran."""
        import inspect

        from curtail_core import backtest

        source = inspect.getsource(backtest)
        assert "ACTION_DIRECTION" in source
        assert (
            Path(__file__).resolve().parents[2] / "scripts" / "export_evals.py"
        ).read_text().count("ACTION_DIRECTION") >= 2

    async def test_the_agent_answers_every_case_in_the_expected_vocabulary(
        self, eval_set: dict[str, Any]
    ) -> None:
        """The decisive test, and the one whose absence let this ship twice.

        Runs the REAL fleet on EVERY case in the eval set and compares the direction
        the agent itself emits against the direction the case expects. The earlier
        version of this test mapped the agent's classification to a direction inside
        the test, which proved the test's own arithmetic rather than the agent's
        answer, and left the artifact still comparing two vocabularies.
        """
        from datetime import datetime

        from google.adk.sessions import InMemorySessionService

        from curtail_agents.app import APP_NAME, build_fleet_runner, evaluate_direction
        from curtail_agents.events import Provenance
        from curtail_agents.sentinel import Observation
        from curtail_core.basins import Basin

        sessions = InMemorySessionService()  # type: ignore[no-untyped-call]
        runner = build_fleet_runner(sessions)

        compared = 0
        for index, case in enumerate(eval_set["eval_cases"]):
            observation_state = case["session_input"]["state"]["observation"]
            expected = case["conversation"][0]["final_response"]["parts"][0]["text"]
            session_id = f"eval-{index}"
            await sessions.create_session(
                app_name=APP_NAME, user_id="watermaster", session_id=session_id
            )
            answered = await evaluate_direction(
                Observation(
                    Basin(observation_state["basin"]),
                    observation_state["observed_cfs"],
                    datetime.fromisoformat(observation_state["observed_at"]),
                    Provenance.BOARD_DOCUMENT,
                ),
                runner=runner,
                correlation_id=case["eval_id"],
                user_id="watermaster",
                session_id=session_id,
                deadline=30.0,
            )
            assert answered == expected, (
                f"{case['eval_id']}: the Board's record points {expected!r} and the "
                f"agent answered {answered!r}"
            )
            compared += 1

        assert compared == len(eval_set["eval_cases"]), "some cases were never compared"
        assert compared > 0, "an empty eval set compares nothing and proves nothing"
