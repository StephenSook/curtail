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
    SCHEDULES_BY_ERA,
    Basin,
    EraNotEncodedError,
    FlowPeriod,
    RegulatoryEra,
    ScheduleGapError,
    era_for,
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


class TestTheEraGuardMakesTheWrongTableUnreachable:
    """The 2021 emergency cycle is a separate adoption whose table has not been
    verified in full against a primary source, and its orders ran into 2023.

    Answering a 2021 question from the readopted table would apply the wrong rule
    and mark the Board wrong for following the one actually in force. That is a
    worse failure than refusing, so the 2021 schedule is deliberately empty and
    every path through it raises.
    """

    def test_a_2021_shasta_date_refuses(self) -> None:
        """No fetched 2021-era Shasta document prints a monthly table. The orders
        recite that "the Regulation establishes minimum instream flows" without
        reproducing the values, so there is nothing to encode."""
        with pytest.raises(EraNotEncodedError):
            minimum_flow(Basin.SHASTA, date(2022, 4, 15))

    def test_a_2021_scott_month_with_no_document_evidence_refuses(self) -> None:
        """September is absent from the 2021 Scott table because no 2021-era
        document states it. Copying it across from the readopted table would
        recreate exactly the failure the era split exists to prevent."""
        with pytest.raises(ScheduleGapError):
            minimum_flow(Basin.SCOTT, date(2021, 9, 15))

    def test_a_2023_date_refuses_too(self) -> None:
        """Scott Addendum 51 issued in 2023 under the 2021 orders, so the old
        era extends well past 2021 and the guard has to cover it."""
        with pytest.raises(EraNotEncodedError):
            minimum_flow(Basin.SHASTA, date(2023, 7, 1))

    def test_the_refusal_explains_which_era_and_why(self) -> None:
        with pytest.raises(EraNotEncodedError) as excinfo:
            minimum_flow(Basin.SHASTA, date(2022, 4, 15))
        message = str(excinfo.value)
        assert "2021_emergency" in message
        assert "not been verified" in message

    def test_the_boundary_is_exact(self) -> None:
        """One day either side, so a drifting boundary is visible."""
        assert era_for(date(2023, 12, 31)) is RegulatoryEra.ERA_2021
        assert era_for(date(2024, 1, 1)) is RegulatoryEra.ERA_2024

    def test_a_current_era_date_still_answers(self) -> None:
        """Non-vacuity for the guard: it must not refuse everything."""
        assert minimum_flow(Basin.SCOTT, date(2025, 7, 20)) == 50.0

    def test_the_2021_scott_table_is_partial_on_purpose(self) -> None:
        """It holds exactly the months a 2021-era document states, and no more.

        If it ever grows to twelve months by copying the readopted table across,
        every refusal above silently becomes a confident answer nobody read off a
        page. The partialness IS the safety property, so it is asserted directly.
        """
        scott_2021 = SCHEDULES_BY_ERA[RegulatoryEra.ERA_2021][Basin.SCOTT]
        months = {p.start_month for p in scott_2021}
        assert months == {1, 2, 3, 4, 5, 6, 7}, (
            "the 2021 Scott table should cover only the months with document "
            f"evidence; it now covers {sorted(months)}"
        )
        assert Basin.SHASTA not in SCHEDULES_BY_ERA[RegulatoryEra.ERA_2021]

    def test_the_verified_2021_months_match_the_readopted_table(self) -> None:
        """The empirical finding, locked.

        Every Scott month the 2021-era documents state is IDENTICAL to the
        readopted table. That is why the era guard's stated reason is "not
        verified" rather than "known to differ": the evidence so far points the
        other way.
        """
        for when, expected in (
            (date(2022, 2, 15), 200.0),
            (date(2022, 4, 15), 150.0),
            (date(2022, 6, 23), 125.0),
            (date(2022, 6, 24), 90.0),
            (date(2023, 7, 15), 50.0),
        ):
            assert minimum_flow(Basin.SCOTT, when) == expected
            assert minimum_flow(Basin.SCOTT, when.replace(year=2026)) == expected

    def test_an_unencoded_era_is_still_a_schedule_gap(self) -> None:
        """Subclassing matters: callers that already refuse on ScheduleGapError
        stay safe without being taught a new exception type."""
        assert issubclass(EraNotEncodedError, ScheduleGapError)
        with pytest.raises(ScheduleGapError):
            minimum_flow(Basin.SCOTT, date(2021, 9, 15))

    def test_an_explicit_override_still_wins(self) -> None:
        """A CDFW alternative-flow table under 875(c)(2)(B) to (D) is an
        authoritative instruction, so it answers even in an unencoded era. The
        caller supplied the number; nothing was guessed."""
        ramp = (FlowPeriod(9, 1, 9, 30, 42),)
        assert minimum_flow(Basin.SCOTT, date(2021, 9, 15), override=ramp) == 42
