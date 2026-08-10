"""The Gage Sentinel decides what a reading means, and two bugs here were mine.

Both were caught by running the agent against the July 2025 sequence before
writing a line of test code, which is why that sequence is the fixture: it is the
one case in the record where the right answer is known independently of the
engine, because people went and measured.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from curtail_agents.events import EventType, Provenance
from curtail_agents.sentinel import (
    SUSTAINED_RECOVERY_WINDOW,
    Observation,
    SentinelError,
    evaluate,
)
from curtail_core.basins import Basin

#: The two readings the July 2025 sequence turned on, with their real times.
JULY_20 = datetime(2025, 7, 20, 21, 30, tzinfo=UTC)
JULY_22 = datetime(2025, 7, 22, 7, 30, tzinfo=UTC)


def obs(cfs: float, when: datetime, basin: Basin = Basin.SCOTT) -> Observation:
    return Observation(basin, cfs, when, Provenance.USGS_LIVE)


class TestTheJuly2025SequenceThroughTheAgent:
    def test_the_reading_that_triggered_curtailment_is_flagged_near_threshold(self) -> None:
        """48.7 cfs against a 50 cfs minimum.

        Below the line AND inside the band. It is announced as near-threshold
        rather than as a plain shortfall, because that is the more actionable
        statement: it tells the official the decision rests on a number worth
        checking. Field measurement is what reversed this exact reading.
        """
        event = evaluate(obs(48.7, JULY_20), correlation_id="c")
        assert event.event_type is EventType.READING_NEAR_THRESHOLD

    def test_that_event_carries_both_numbers_so_the_comparison_is_checkable(self) -> None:
        event = evaluate(obs(48.7, JULY_20), correlation_id="c")
        assert event.observed_cfs == 48.7
        assert event.minimum_cfs == 50.0
        assert round(event.shortfall_cfs, 6) == 1.3

    def test_the_note_names_the_precedent(self) -> None:
        """The reader of this event is deciding whether to shut off a watershed.
        The note tells them this exact situation was reversed by measurement."""
        note = evaluate(obs(48.7, JULY_20), correlation_id="c").note
        assert "78.4" in note
        assert "field verification" in note.lower()


class TestRecoveryRequiresEvidence:
    """ "We have not seen it drop" and "it has not dropped" are different claims.

    Only the second justifies releasing water, so recovery requires evidence
    covering the window rather than an absence of contrary evidence.
    """

    def test_a_single_high_reading_is_not_a_recovery(self) -> None:
        event = evaluate(obs(78.4, JULY_22), correlation_id="c")
        assert event.event_type is EventType.FLOW_ABOVE_MINIMUM_UNSUSTAINED

    def test_a_covered_window_above_the_minimum_is_a_recovery(self) -> None:
        """The bug this test exists for.

        The window filter discarded readings from before the cutoff, then asked
        whether the oldest survivor sat near the cutoff. The reading that proves
        the window was covered from its start is exactly the one at or just
        before the cutoff, so it had already been thrown away. Two full days of
        readings above the minimum reported as unsustained, which would hold
        curtailment in force on a river that had recovered.
        """
        history = [
            obs(78.0, JULY_22 - SUSTAINED_RECOVERY_WINDOW - timedelta(hours=1)),
            obs(80.0, JULY_22 - timedelta(days=1)),
        ]
        event = evaluate(obs(78.4, JULY_22), correlation_id="c", recent=history)
        assert event.event_type is EventType.FLOW_RECOVERED_SUSTAINED

    def test_a_dip_inside_the_window_disqualifies_the_recovery(self) -> None:
        history = [
            obs(78.0, JULY_22 - SUSTAINED_RECOVERY_WINDOW - timedelta(hours=1)),
            obs(40.0, JULY_22 - timedelta(days=1)),
        ]
        event = evaluate(obs(78.4, JULY_22), correlation_id="c", recent=history)
        assert event.event_type is not EventType.FLOW_RECOVERED_SUSTAINED

    def test_a_window_that_opened_during_a_shortfall_is_not_a_recovery(self) -> None:
        """The anchor reading must itself be above the minimum, or the window
        began mid-shortfall and proves nothing about its start."""
        history = [
            obs(20.0, JULY_22 - SUSTAINED_RECOVERY_WINDOW - timedelta(hours=1)),
            obs(80.0, JULY_22 - timedelta(days=1)),
        ]
        event = evaluate(obs(78.4, JULY_22), correlation_id="c", recent=history)
        assert event.event_type is EventType.FLOW_ABOVE_MINIMUM_UNSUSTAINED

    def test_history_shorter_than_the_window_is_not_a_recovery(self) -> None:
        history = [obs(80.0, JULY_22 - timedelta(hours=6))]
        event = evaluate(obs(78.4, JULY_22), correlation_id="c", recent=history)
        assert event.event_type is EventType.FLOW_ABOVE_MINIMUM_UNSUSTAINED


class TestNotYetAndYesAreDifferentEvents:
    """The second bug, and the more dangerous one.

    The unsustained branch originally fell through to FLOW_RECOVERED_SUSTAINED
    with an empty note, announcing a recovery that had not happened. Downstream
    that is a suspension issued on a single reading, which is the exact failure
    hysteresis exists to prevent.
    """

    def test_an_unsustained_reading_never_announces_a_sustained_recovery(self) -> None:
        event = evaluate(obs(200.0, JULY_22), correlation_id="c")
        assert event.event_type is not EventType.FLOW_RECOVERED_SUSTAINED

    def test_an_unsustained_reading_says_it_is_not_a_basis_for_suspending(self) -> None:
        """An empty note is how this bug hid. The note must state the limit."""
        event = evaluate(obs(200.0, JULY_22), correlation_id="c")
        assert event.note
        assert "not a basis for suspending" in event.note.lower()


class TestTheAgentRefusesRatherThanGuessing:
    def test_an_unencoded_era_raises_rather_than_defaulting(self) -> None:
        """The schedule refuses for a 2021 Shasta date. That refusal is passed
        through, not converted into a plausible number."""
        with pytest.raises(SentinelError):
            evaluate(
                obs(100.0, datetime(2022, 4, 15, tzinfo=UTC), Basin.SHASTA), correlation_id="c"
            )

    def test_a_naive_timestamp_is_refused_at_construction(self) -> None:
        """These events feed statutory clocks measured in days."""
        with pytest.raises(ValueError):
            # DTZ001 is suppressed for exactly one line, and only here. The
            # naive datetime IS the input under test: this asserts that the
            # agent refuses one. Fixing the lint by making it aware would delete
            # the test.
            naive = datetime(2025, 7, 20, 21, 30)  # noqa: DTZ001
            Observation(Basin.SCOTT, 50.0, naive, Provenance.USGS_LIVE)

    def test_an_event_without_a_correlation_id_cannot_exist(self) -> None:
        """A message that dead-letters with no correlation ID cannot be traced,
        and tracing it is what the chaos drill has to demonstrate."""
        with pytest.raises(ValueError):
            evaluate(obs(48.7, JULY_20), correlation_id="")


class TestProvenanceGatesAction:
    def test_a_sourced_event_is_actionable(self) -> None:
        assert evaluate(obs(48.7, JULY_20), correlation_id="c").actionable is True

    def test_an_unsourced_event_is_never_actionable(self) -> None:
        """The structural form of the rule that a figure with no traceable
        origin does not enter a legal record. Enforced here rather than trusted
        to each consumer."""
        unsourced = Observation(Basin.SCOTT, 48.7, JULY_20, Provenance.UNSOURCED)
        assert evaluate(unsourced, correlation_id="c").actionable is False

    def test_provenance_survives_into_the_event(self) -> None:
        """A console showing a cached reading must be able to say so on screen."""
        cached = Observation(Basin.SCOTT, 48.7, JULY_20, Provenance.USGS_CACHED)
        assert evaluate(cached, correlation_id="c").provenance is Provenance.USGS_CACHED


class TestExactlyOneEventPerReading:
    """An agent that emits several events per reading forces every consumer to
    reason about combinations. 48.7 against 50 is both below the minimum and
    inside the band, and it produces one event carrying both facts."""

    @pytest.mark.parametrize("cfs", [10.0, 48.7, 50.0, 55.0, 78.4, 200.0])
    def test_evaluate_returns_a_single_event_for_any_reading(self, cfs: float) -> None:
        event = evaluate(obs(cfs, JULY_22), correlation_id="c")
        assert event.event_type in set(EventType)
        assert event.minimum_cfs == 50.0
