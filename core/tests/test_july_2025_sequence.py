"""The July 2025 sequence: the machine is overruled by people who went and measured.

This is the project's central fixture, and every figure in it comes from the two
Addenda themselves, both of which are scans with no text layer and were read from
their rendered pages.

The sequence, from the documents:

    July 20 2025, 21:30   Fort Jones reads 48.7 cfs against a 50 cfs July minimum.
                          Addendum 7 reinstates curtailment for all surface water
                          and groundwater diverters in the Scott River watershed.

    July 22 2025, 07:30   Fort Jones reads 78.4 cfs. Addendum 8 suspends all
                          curtailments. Signed by Erik Ekdahl, Chief Deputy
                          Director.

What happened in between, in the Board's own words in Addendum 8: "Several
community members expressed concern regarding the accuracy of the measurement,
and USGS has revised its flow measurements upward based on measurements taken by
the Scott Valley and Shasta Valley Watermaster District (Watermaster)."

The river did not rise by 30 cfs in 34 hours. The rating curve moved. The same
water was 48.7 cfs on Sunday night and 78.4 cfs on Tuesday morning because people
waded into it with a current meter, and a legal shutoff was lifted as a result.

**The claim these tests protect, and its honest limit.** At 48.7 cfs the engine
recommends curtailment AND raises a near-threshold flag reading "field
verification recommended", because 48.7 sits inside the 10 cfs band below the
minimum. Field verification is precisely what changed the answer.

That is not a tuned result. `NEAR_THRESHOLD_BAND_CFS` and the Scott July and
August minimums were both introduced in commit 3b83012, the first scaffold commit
in this repository. Addenda 7 and 8 were not fetched to disk until 8457e33,
fifteen commits later. Anyone can verify that ordering from git history, and the
tests below are worth nothing if it is ever untrue.

The limit: the engine flags that a reading is close to the line. It does not know
the reading is wrong, and it cannot. Only a person standing in the river knew
that. The engine's job here is to say "this decision rests on a number worth
checking" loudly enough that someone checks it.
"""

from __future__ import annotations

from datetime import date

from curtail_core.basins import COMPLIANCE_GAGE, Basin
from curtail_core.flow_minimums import (
    NEAR_THRESHOLD_BAND_CFS,
    is_below_minimum,
    is_near_threshold,
    minimum_flow,
)

#: Addendum 7, page 1: "Flows at the USGS Fort Jones gage are 48.7 cfs as of
#: July 20, 2025, at 9:30 pm."
ADDENDUM_7_DATE = date(2025, 7, 20)
ADDENDUM_7_CFS = 48.7

#: Addendum 8: "As of 7:30 am this morning, flows at the Fort Jones USGS gage
#: were measured at 78.4 cfs."
ADDENDUM_8_DATE = date(2025, 7, 22)
ADDENDUM_8_CFS = 78.4

#: Addendum 7, page 1: "the required minimum flow at the Fort Jones USGS gage for
#: July is 50 cubic feet per second (cfs) and 30 cfs for August."
JULY_MINIMUM_STATED_BY_THE_BOARD = 50.0
AUGUST_MINIMUM_STATED_BY_THE_BOARD = 30.0


class TestTheScheduleMatchesWhatTheBoardWrote:
    """Independent confirmation of a schedule encoded before these were read."""

    def test_the_july_minimum_matches_the_addendum(self) -> None:
        assert minimum_flow(Basin.SCOTT, ADDENDUM_7_DATE) == JULY_MINIMUM_STATED_BY_THE_BOARD

    def test_the_august_minimum_matches_the_addendum(self) -> None:
        """August drops to 30 cfs. An engine holding July's 50 through August
        would curtail a watershed that the regulation permits to run lower."""
        assert minimum_flow(Basin.SCOTT, date(2025, 8, 15)) == AUGUST_MINIMUM_STATED_BY_THE_BOARD

    def test_the_compliance_gage_is_fort_jones(self) -> None:
        assert COMPLIANCE_GAGE[Basin.SCOTT] == "USGS-11519500"


class TestAddendum7TheReinstatement:
    def test_the_engine_agrees_curtailment_was_required(self) -> None:
        assert is_below_minimum(Basin.SCOTT, ADDENDUM_7_DATE, ADDENDUM_7_CFS) is True

    def test_the_engine_flags_the_reading_as_near_the_threshold(self) -> None:
        """The whole point. 48.7 is 1.3 cfs below a 50 cfs minimum.

        A system that reported "below minimum, curtail" and stopped would have
        handed an official a shutoff decision resting on a number that was about
        to move by 30 cfs, with nothing on the screen suggesting the number was
        worth checking.
        """
        assert is_near_threshold(Basin.SCOTT, ADDENDUM_7_DATE, ADDENDUM_7_CFS) is True

    def test_the_shortfall_is_well_inside_the_band(self) -> None:
        """Non-vacuity for the flag above.

        If the band were ever widened to something that flags everything, the
        flag would stop carrying information. 1.3 cfs is comfortably inside a 10
        cfs band, and the assertion states both facts so a change to either is
        visible.
        """
        shortfall = JULY_MINIMUM_STATED_BY_THE_BOARD - ADDENDUM_7_CFS
        assert shortfall < NEAR_THRESHOLD_BAND_CFS
        assert 1.0 < shortfall < 2.0


class TestAddendum8TheSuspension:
    def test_the_engine_agrees_curtailment_was_no_longer_required(self) -> None:
        assert is_below_minimum(Basin.SCOTT, ADDENDUM_8_DATE, ADDENDUM_8_CFS) is False

    def test_the_revised_reading_is_not_near_the_threshold(self) -> None:
        """78.4 against 50 is clear of the line, so no flag is raised.

        A flag on every reading would be noise. This one is silent here.
        """
        assert is_near_threshold(Basin.SCOTT, ADDENDUM_8_DATE, ADDENDUM_8_CFS) is False

    def test_the_two_readings_bracket_the_minimum(self) -> None:
        """The legal consequence turned on which side of 50 the gage sat.

        Same river, same gage, 34 hours apart, opposite answers.
        """
        assert ADDENDUM_7_CFS < JULY_MINIMUM_STATED_BY_THE_BOARD < ADDENDUM_8_CFS


class TestTheRevisionIsLargeEnoughToMatter:
    """A REVISION_IMPACT subscription is only worth building if revisions are big."""

    def test_the_revision_moved_the_reading_by_more_than_half_again(self) -> None:
        ratio = ADDENDUM_8_CFS / ADDENDUM_7_CFS
        assert ratio > 1.5, f"revision moved the reading by {ratio:.2f}x"

    def test_the_revision_exceeds_the_near_threshold_band_severalfold(self) -> None:
        """29.7 cfs of movement against a 10 cfs band.

        The band cannot be widened to absorb a revision of this size; that is
        why the answer is field verification and a revisions feed, not a bigger
        tolerance.
        """
        movement = ADDENDUM_8_CFS - ADDENDUM_7_CFS
        assert movement > 2 * NEAR_THRESHOLD_BAND_CFS

    def test_a_curtailment_decision_rested_on_the_superseded_value(self) -> None:
        """Addendum 7 was issued on 48.7 and 48.7 was later superseded.

        This is the fixture that makes REVISION_IMPACT a real subscription
        rather than a simulated one: an order existed, in force, resting on a
        number that no longer holds.
        """
        assert is_below_minimum(Basin.SCOTT, ADDENDUM_7_DATE, ADDENDUM_7_CFS) is True
        assert is_below_minimum(Basin.SCOTT, ADDENDUM_7_DATE, ADDENDUM_8_CFS) is False
