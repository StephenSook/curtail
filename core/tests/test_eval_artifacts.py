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
