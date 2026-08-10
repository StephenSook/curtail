"""Golden tests for the flow minimum schedule.

Every case here is anchored to a real, citable public event or to the literal
text of 23 CCR 875. Nothing is invented, and no expected value is derived from
a memory ledger: each one traces to an order, an addendum, or the regulation.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from curtail_core.flow_minimums import (
    NEAR_THRESHOLD_BAND_CFS,
    SCHEDULES,
    Basin,
    FlowPeriod,
    ScheduleGapError,
    is_below_minimum,
    is_near_threshold,
    minimum_flow,
)


class TestMidMonthBoundaries:
    """The three mid-month breaks a month-keyed engine would get wrong.

    These are the whole reason the schedule is date-period bounded. A lookup
    keyed on month alone returns the wrong minimum on each of these days.
    """

    def test_scott_june_23_is_still_125(self) -> None:
        assert minimum_flow(Basin.SCOTT, date(2026, 6, 23)) == 125

    def test_scott_june_24_drops_to_90(self) -> None:
        assert minimum_flow(Basin.SCOTT, date(2026, 6, 24)) == 90

    def test_shasta_march_24_is_still_125(self) -> None:
        assert minimum_flow(Basin.SHASTA, date(2026, 3, 24)) == 125

    def test_shasta_march_25_drops_to_105(self) -> None:
        assert minimum_flow(Basin.SHASTA, date(2026, 3, 25)) == 105

    def test_shasta_september_15_is_still_50(self) -> None:
        assert minimum_flow(Basin.SHASTA, date(2026, 9, 15)) == 50

    def test_shasta_september_16_rises_to_75(self) -> None:
        assert minimum_flow(Basin.SHASTA, date(2026, 9, 16)) == 75


class TestHistoricalOrders:
    """Real curtailment events, reproduced from the public record."""

    def test_shasta_addendum_6(self) -> None:
        """Addendum 6 to Order WR 2024-0006-DWR, June 16, 2026.

        Figures read from the document itself, not from a research ledger. The
        Addendum records the Yreka USGS gage at 45.3 cfs at noon on June 13 and
        46.5 cfs at 11:45 am on June 15, against the 50 cfs requirement that runs
        May 1 to September 15.

        An earlier version of this test asserted 39.3 cfs, a figure that appears
        NOWHERE in the document. It came from a research haul and propagated into
        four artifacts including a public README. HARD RULE 56: code-audit every
        figure against the primary source; a memory ledger is not a source.
        """
        when = date(2026, 6, 16)
        assert minimum_flow(Basin.SHASTA, when) == 50
        assert is_below_minimum(Basin.SHASTA, when, 46.5)
        assert is_below_minimum(Basin.SHASTA, when, 45.3)

    def test_scott_addendum_9(self) -> None:
        """May 21, 2026. 145 cfs at Fort Jones against the 150 cfs May minimum.

        Addendum 9 to Order WR 2024-0024-DWR curtailed all surface water
        diversions with immediate cessation. Five cfs under, which also puts it
        inside the near threshold band.
        """
        when = date(2026, 5, 21)
        assert minimum_flow(Basin.SCOTT, when) == 150
        assert is_below_minimum(Basin.SCOTT, when, 145)
        assert is_near_threshold(Basin.SCOTT, when, 145)

    def test_july_2025_rating_curve_revision_lifted_curtailment(self) -> None:
        """The canonical human-overrules-the-machine case.

        The Fort Jones gage read below the July minimum of 50 cfs and Addendum 7
        reinstated curtailment. The Watermaster District then conducted field
        flows, USGS shifted the rating curve, and flows read above 75 cfs.
        Addendum 8 suspended curtailment on July 22, 2025.

        The same gage, the same day, two different readings, two opposite legal
        outcomes. That is the field verification loop in one assertion.
        """
        when = date(2025, 7, 22)
        assert minimum_flow(Basin.SCOTT, when) == 50
        assert is_below_minimum(Basin.SCOTT, when, 48.0)
        assert not is_below_minimum(Basin.SCOTT, when, 75.0)


class TestAlternativeFlowOverride:
    """Section 875(c)(2)(B) through (D) permits CDFW alternative flows."""

    def test_override_takes_precedence(self) -> None:
        """The August 2025 ramping table shape: 45 cfs where baseline says 50."""
        ramping = (FlowPeriod(8, 20, 9, 2, 45),)
        when = date(2025, 8, 25)
        assert minimum_flow(Basin.SHASTA, when) == 50
        assert minimum_flow(Basin.SHASTA, when, override=ramping) == 45

    def test_override_falls_through_outside_its_window(self) -> None:
        ramping = (FlowPeriod(8, 20, 9, 2, 45),)
        when = date(2025, 10, 1)
        assert minimum_flow(Basin.SHASTA, when, override=ramping) == 105


class TestScheduleIntegrity:
    """Structural invariants. A gap here is a silent wrong answer downstream."""

    @pytest.mark.parametrize("basin", list(Basin))
    def test_every_day_of_the_year_is_covered(self, basin: Basin) -> None:
        """2024 is a leap year, so this also exercises February 29."""
        for ordinal in range(date(2024, 1, 1).toordinal(), date(2024, 12, 31).toordinal() + 1):
            day = date.fromordinal(ordinal)
            minimum_flow(basin, day)  # raises ScheduleGapError on a gap

    @pytest.mark.parametrize("basin", list(Basin))
    def test_no_two_periods_overlap(self, basin: Basin) -> None:
        for ordinal in range(date(2024, 1, 1).toordinal(), date(2024, 12, 31).toordinal() + 1):
            day = date.fromordinal(ordinal)
            matches = [p for p in SCHEDULES[basin] if p.contains(day)]
            assert len(matches) == 1, f"{basin} has {len(matches)} periods on {day}"

    def test_gap_raises_rather_than_defaulting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing period must fail loudly, never return a quiet default.

        This installs a deliberately gappy schedule covering only January, then
        asks for a date in May. It must raise rather than invent a number.
        """
        gappy = (FlowPeriod(1, 1, 1, 31, 200),)
        monkeypatch.setitem(SCHEDULES, Basin.SCOTT, gappy)

        assert minimum_flow(Basin.SCOTT, date(2026, 1, 15)) == 200  # covered
        with pytest.raises(ScheduleGapError, match="no minimum flow period covers"):
            minimum_flow(Basin.SCOTT, date(2026, 5, 15))  # not covered

    def test_the_gap_test_above_is_not_vacuous(self) -> None:
        """Guard the guard: the real schedule must NOT raise on that same date.

        Without this, a future edit that made minimum_flow raise unconditionally
        would leave the test above passing while it protected nothing.
        """
        assert minimum_flow(Basin.SCOTT, date(2026, 5, 15)) == 150


class TestProperties:
    """Hypothesis invariants that must hold for any reading."""

    @given(
        basin=st.sampled_from(list(Basin)),
        month=st.integers(min_value=1, max_value=12),
        day=st.integers(min_value=1, max_value=28),
        observed=st.floats(min_value=0, max_value=5000, allow_nan=False),
    )
    def test_below_minimum_is_exactly_the_complement_of_at_or_above(
        self, basin: Basin, month: int, day: int, observed: float
    ) -> None:
        when = date(2026, month, day)
        minimum = minimum_flow(basin, when)
        assert is_below_minimum(basin, when, observed) == (observed < minimum)

    @given(
        basin=st.sampled_from(list(Basin)),
        month=st.integers(min_value=1, max_value=12),
        day=st.integers(min_value=1, max_value=28),
    )
    def test_a_reading_exactly_at_the_minimum_is_not_below_it(
        self, basin: Basin, month: int, day: int
    ) -> None:
        """Curtailment triggers below the minimum, not at it. An off-by-one here
        would curtail an entire watershed one cfs early."""
        when = date(2026, month, day)
        assert not is_below_minimum(basin, when, minimum_flow(basin, when))

    @given(
        basin=st.sampled_from(list(Basin)),
        month=st.integers(min_value=1, max_value=12),
        day=st.integers(min_value=1, max_value=28),
    )
    def test_minimum_flow_is_deterministic(self, basin: Basin, month: int, day: int) -> None:
        when = date(2026, month, day)
        assert minimum_flow(basin, when) == minimum_flow(basin, when)

    @given(
        basin=st.sampled_from(list(Basin)),
        month=st.integers(min_value=1, max_value=12),
        day=st.integers(min_value=1, max_value=28),
        delta=st.floats(min_value=0, max_value=NEAR_THRESHOLD_BAND_CFS, allow_nan=False),
    )
    def test_near_threshold_band_is_symmetric(
        self, basin: Basin, month: int, day: int, delta: float
    ) -> None:
        when = date(2026, month, day)
        minimum = minimum_flow(basin, when)
        assert is_near_threshold(basin, when, minimum + delta)
        assert is_near_threshold(basin, when, minimum - delta)
