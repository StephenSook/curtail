"""The backtest is the credibility artifact, so the harness is tested harder than the result.

A harness that reports "6 of 6" is worth nothing unless it can be shown to
report a mismatch when one exists. Most of this file exists to prove the
denominator is real, the numerator can go down, and a refusal is counted in
neither half of the fraction.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from curtail_core.backtest import (
    CASES_PATH,
    Direction,
    Outcome,
    run,
    score_case,
)
from curtail_core.basins import Basin

ERA_START = date(2024, 1, 1)


@pytest.fixture(scope="module")
def report() -> Any:
    """The real backtest, run once. Module scope, not class scope: a
    class-scoped fixture written as an instance method is deprecated in pytest
    and errors here rather than skipping, which is the correct behaviour."""
    return run()


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(Path(CASES_PATH).read_text())
    return payload


def _case(**overrides: Any) -> dict[str, Any]:
    """A known-good case, so each test varies exactly one thing."""
    base = {
        "id": "probe/case",
        "basin": "scott",
        "decision_date": "2025-07-20",
        "reading_cfs": 48.7,
        "board_action": "reinstate",
        "source_quote": "probe",
    }
    base.update(overrides)
    return base


class TestTheHarnessCanReportAMismatch:
    """Non-vacuity. Without this, the headline number means nothing."""

    def test_a_correct_case_matches(self) -> None:
        result = score_case(_case(), earliest_scorable=ERA_START)
        assert result.outcome is Outcome.MATCH

    def test_the_board_relieving_below_the_minimum_is_divergent(self) -> None:
        """48.7 is below the 50 cfs July minimum. If the Board had suspended on
        that reading, the engine must report the disagreement, not absorb it."""
        result = score_case(_case(board_action="suspend"), earliest_scorable=ERA_START)
        assert result.outcome is Outcome.DIVERGENT

    def test_the_board_restricting_above_the_minimum_is_divergent(self) -> None:
        """78.4 cfs is well above the 50 cfs July minimum.

        Suspending on that reading agrees with the engine. Reinstating
        curtailment on it does not, and the harness must say so. Both halves are
        asserted, because a harness that only ever reported MATCH would produce
        exactly the same headline number while measuring nothing.
        """
        agreeing = score_case(
            _case(reading_cfs=78.4, board_action="suspend", decision_date="2025-07-22"),
            earliest_scorable=ERA_START,
        )
        assert agreeing.outcome is Outcome.MATCH

        disagreeing = score_case(
            _case(reading_cfs=78.4, board_action="reinstate", decision_date="2025-07-22"),
            earliest_scorable=ERA_START,
        )
        assert disagreeing.outcome is Outcome.DIVERGENT

    def test_a_divergence_explains_that_it_may_not_be_an_engine_error(self) -> None:
        """875(b)(3) permits the official to decline, narrow, or suspend.

        A harness that presented every divergence as an engine failure would
        misrepresent the law it is modelling.
        """
        result = score_case(_case(board_action="suspend"), earliest_scorable=ERA_START)
        assert "875(b)(3)" in result.reasoning


class TestTheEraGuardRefusesRatherThanScores:
    def test_a_2021_decision_is_refused(self) -> None:
        """The 2021 cycle used a different table and a 135 cfs sustained
        threshold. Scoring it here would mark the Board wrong for correctly
        applying the rule then in force."""
        result = score_case(_case(decision_date="2021-08-30"), earliest_scorable=ERA_START)
        assert result.outcome is Outcome.NOT_SCORABLE

    def test_a_refusal_says_why(self) -> None:
        result = score_case(_case(decision_date="2021-08-30"), earliest_scorable=ERA_START)
        assert "135" in result.reasoning

    def test_a_refusal_is_in_neither_half_of_the_fraction(self) -> None:
        result = score_case(_case(decision_date="2021-08-30"), earliest_scorable=ERA_START)
        assert result.scored is False

    def test_an_unmappable_action_is_refused_not_guessed(self) -> None:
        """An amendment changes who is covered, not whether a threshold was
        crossed. Forcing it into a direction would invent a comparison."""
        result = score_case(_case(board_action="amend"), earliest_scorable=ERA_START)
        assert result.outcome is Outcome.NOT_SCORABLE


class TestTheDenominatorIsWhatWasScored:
    def test_the_headline_denominator_equals_the_scored_count(self, report: Any) -> None:
        assert f"of {len(report.scored)} scored" in report.headline

    def test_refusals_are_excluded_from_the_denominator(self, report: Any) -> None:
        assert len(report.scored) == len(report.results) - len(report.refused)

    def test_matches_never_exceed_scored(self, report: Any) -> None:
        assert len(report.matches) <= len(report.scored)

    def test_the_headline_reports_refusals_and_exclusions_alongside(self, report: Any) -> None:
        """A bare fraction hides the documents that never entered it."""
        assert "refused" in report.headline
        assert "excluded" in report.headline

    def test_there_is_at_least_one_scored_case(self, report: Any) -> None:
        """A harness scoring nothing would report a vacuous 0 of 0."""
        assert len(report.scored) >= 1


class TestEveryCaseCarriesItsEvidence:
    def test_every_case_quotes_the_sentence_it_came_from(self, data: dict[str, Any]) -> None:
        """The retired 39.3 rule, enforced on the backtest input.

        A case whose reading came from a summary rather than a document is not
        evidence, and the quote is what makes that checkable without reopening
        every PDF.
        """
        for case in data["cases"]:
            assert case.get("source_quote"), f"{case['id']} has no source quote"
            assert len(case["source_quote"]) > 30, f"{case['id']} quote is too short to verify"

    def test_every_case_reading_appears_in_its_own_quote(self, data: dict[str, Any]) -> None:
        """The strongest available check: the number must be in the sentence.

        This is what would have caught the retired 39.3, because the quote from Addendum 6
        does not contain it.
        """
        for case in data["cases"]:
            reading = f"{case['reading_cfs']:g}"
            assert reading in case["source_quote"], (
                f"{case['id']}: reading {reading} does not appear in its own source quote"
            )

    def test_every_case_records_how_it_was_read(self, data: dict[str, Any]) -> None:
        for case in data["cases"]:
            assert case["read_method"] in {"text_layer", "vision"}

    def test_every_exclusion_states_a_reason(self, data: dict[str, Any]) -> None:
        """Silent truncation reads as complete coverage when it is not."""
        for item in data["excluded_with_reason"]:
            assert item.get("reason"), f"{item['id']} excluded with no reason"

    def test_the_retired_figure_is_in_no_case(self, data: dict[str, Any]) -> None:
        for case in data["cases"]:
            assert case["reading_cfs"] != 39.3  # retired, appears nowhere in any source


class TestTheJulySequenceScoresAsExpected:
    """The flagship pair, end to end through the harness."""

    def test_the_reinstatement_matches_and_flags_field_verification(self, report: Any) -> None:
        result = next(r for r in report.results if r.case_id == "scott_2024/addenda/7")
        assert result.outcome is Outcome.MATCH
        assert result.engine_direction is Direction.RESTRICT
        assert result.near_threshold is True

    def test_the_suspension_matches_and_raises_no_flag(self, report: Any) -> None:
        result = next(r for r in report.results if r.case_id == "scott_2024/addenda/8")
        assert result.outcome is Outcome.MATCH
        assert result.engine_direction is Direction.RELIEVE
        assert result.near_threshold is False

    def test_both_readings_were_scored_against_the_same_minimum(self, report: Any) -> None:
        """The minimum did not move. The reading did, because people measured."""
        seven = next(r for r in report.results if r.case_id == "scott_2024/addenda/7")
        eight = next(r for r in report.results if r.case_id == "scott_2024/addenda/8")
        assert seven.minimum_cfs == eight.minimum_cfs == 50.0
        assert seven.basin is eight.basin is Basin.SCOTT


class TestTheEraDefenceIsTwoIndependentLayers:
    """The harness refuses pre-era cases, and so does the schedule beneath it.

    Two layers, because they fail for different reasons and a single point of
    control is one edit away from being removed. The harness guard is a policy
    (do not score what we cannot fairly score); the schedule guard is a fact
    (that table was never entered). Either alone would be enough today, and that
    is exactly why both exist.
    """

    def test_the_harness_refuses_a_pre_era_case(self) -> None:
        result = score_case(_case(decision_date="2021-09-15"), earliest_scorable=ERA_START)
        assert result.outcome is Outcome.NOT_SCORABLE

    def test_the_schedule_still_refuses_with_the_harness_guard_disabled(self) -> None:
        """The load-bearing assertion. Widening the harness window, by accident
        or by a hopeful edit, must not produce a scored 2021 result computed from
        the wrong table."""
        result = score_case(_case(decision_date="2021-09-15"), earliest_scorable=date(1900, 1, 1))
        assert result.outcome is Outcome.NOT_SCORABLE
        assert "2021_emergency" in result.reasoning

    def test_a_current_era_case_is_unaffected_by_either_layer(self) -> None:
        """Non-vacuity: the layers must not simply refuse everything."""
        result = score_case(_case(), earliest_scorable=date(1900, 1, 1))
        assert result.outcome is Outcome.MATCH
