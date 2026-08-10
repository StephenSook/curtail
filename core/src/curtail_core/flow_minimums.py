"""Drought emergency minimum instream flows for the Scott and Shasta compliance gages.

Encoded verbatim from California Code of Regulations title 23, section 875,
as readopted effective January 27, 2025 and extended by AB 263.

The single most important structural point: these are NOT monthly constants.
Three of the boundaries fall mid-month, so a rules engine keyed on month alone
computes the wrong minimum on five days of the year and would mis-evaluate any
order issued in those windows:

    Scott   June 24     125 cfs -> 90 cfs
    Shasta  March 25    125 cfs -> 105 cfs
    Shasta  September 16 50 cfs -> 75 cfs

Section 875(c)(2)(B) through (D) also lets the California Department of Fish
and Wildlife notify the Deputy Director of lower or alternative flows, which
has happened in practice: the August 20, 2025 Temporary Amendment to Addendum 4
ran a 14 day ramping table. So the schedule is modeled as an overridable
sequence of periods, never as a frozen constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from curtail_core.basins import COMPLIANCE_GAGE, Basin


@dataclass(frozen=True, slots=True)
class FlowPeriod:
    """A closed date range, month and day only, carrying one minimum flow.

    Year is deliberately absent. The schedule recurs annually, and binding it to
    a year would silently break every season after the one it was written for.
    """

    start_month: int
    start_day: int
    end_month: int
    end_day: int
    cfs: float

    def contains(self, when: date) -> bool:
        """True when `when` falls inside this period, ignoring year."""
        key = (when.month, when.day)
        return (self.start_month, self.start_day) <= key <= (self.end_month, self.end_day)


#: Scott River minimums, 23 CCR 875(c)(1). Note the June 24 break.
SCOTT_SCHEDULE: tuple[FlowPeriod, ...] = (
    FlowPeriod(1, 1, 1, 31, 200),
    FlowPeriod(2, 1, 2, 29, 200),
    FlowPeriod(3, 1, 3, 31, 200),
    FlowPeriod(4, 1, 4, 30, 150),
    FlowPeriod(5, 1, 5, 31, 150),
    FlowPeriod(6, 1, 6, 23, 125),
    FlowPeriod(6, 24, 6, 30, 90),
    FlowPeriod(7, 1, 7, 31, 50),
    FlowPeriod(8, 1, 8, 31, 30),
    FlowPeriod(9, 1, 9, 30, 33),
    FlowPeriod(10, 1, 10, 31, 40),
    FlowPeriod(11, 1, 11, 30, 60),
    FlowPeriod(12, 1, 12, 31, 150),
)

#: Shasta River minimums, 23 CCR 875(c)(2). Note the March 25 and September 16 breaks.
SHASTA_SCHEDULE: tuple[FlowPeriod, ...] = (
    FlowPeriod(1, 1, 1, 31, 125),
    FlowPeriod(2, 1, 2, 29, 125),
    FlowPeriod(3, 1, 3, 24, 125),
    FlowPeriod(3, 25, 3, 31, 105),
    FlowPeriod(4, 1, 4, 30, 70),
    FlowPeriod(5, 1, 5, 31, 50),
    FlowPeriod(6, 1, 6, 30, 50),
    FlowPeriod(7, 1, 7, 31, 50),
    FlowPeriod(8, 1, 8, 31, 50),
    FlowPeriod(9, 1, 9, 15, 50),
    FlowPeriod(9, 16, 9, 30, 75),
    FlowPeriod(10, 1, 10, 31, 105),
    FlowPeriod(11, 1, 11, 30, 125),
    FlowPeriod(12, 1, 12, 31, 125),
)

SCHEDULES: dict[Basin, tuple[FlowPeriod, ...]] = {
    Basin.SCOTT: SCOTT_SCHEDULE,
    Basin.SHASTA: SHASTA_SCHEDULE,
}


class RegulatoryEra(StrEnum):
    """Which emergency regulation governed on a given date.

    The tables above are the readopted regulation. The 2021 emergency cycle is a
    separate adoption whose table has NOT been verified in full against a primary
    source, and its orders were still being amended into 2023: Scott Addendum 51
    issued that year. Applying an unverified table to a 2021 decision could mark
    the Board wrong for correctly applying the rule actually in force, which is a
    worse failure than refusing to answer.

    **What the documents actually show, measured 2026-08-10 across 60 fetched
    2021-era documents.** Every Scott month those documents state MATCHES the
    readopted table: January 200, February 200, March 200, April 150, May 150,
    June 125, July 50. Scott Addendum 30 also confirms the June 24 mid-month
    break, 125 cfs through June 23 and 90 cfs from June 24, existed in 2022.

    An earlier draft of this module asserted the 2021 cycle used "a 135 cfs
    sustained suspension threshold". That figure appears in NONE of the 60
    documents and is not asserted here. It came from a research summary, which is
    the same path the retired 39.3 cfs reading took.
    """

    ERA_2021 = "2021_emergency"
    ERA_2024 = "2024_readopted"


#: Conservative boundary, chosen where the evidence begins rather than where the
#: regulation changed.
#:
#: Direct document confirmation of the encoded table starts in September 2024:
#: Scott Addendum 3 states 33 cfs for September, Addendum 5 states 40 cfs for
#: October, Shasta Addendum 2 states 105 cfs for October. Dates earlier in 2024
#: rest on the regulation's continuity rather than on a document, and that is
#: stated here rather than hidden. Move this boundary only with a primary source.
ERA_2024_BEGINS = date(2024, 1, 1)

#: Used in the refusal message. States the real reason: not that the tables are
#: known to differ, but that the older one has not been verified.
VERIFIED_PHRASE = "not been verified in full against a primary source"


def era_for(when: date) -> RegulatoryEra:
    """Which regulatory era governs a date."""
    return RegulatoryEra.ERA_2024 if when >= ERA_2024_BEGINS else RegulatoryEra.ERA_2021


#: Schedules per era. The 2021 entry is DELIBERATELY EMPTY.
#:
#: Leaving it empty rather than omitting the era makes the gap explicit and makes
#: the wrong answer unrepresentable: there is no path by which a 2021 date
#: quietly receives the readopted table. It gets filled only from the 2021
#: regulation or the 2021 orders themselves, read directly.
#: Scott, 2021 era. PARTIAL BY DESIGN: exactly the periods a 2021-era document
#: states, nothing more.
#:
#: Each entry cites the document that stated it. Every one of these MATCHES the
#: readopted table, which is the empirical answer to whether the tables differ,
#: at least for these months. The remaining five months are absent rather than
#: copied across, so a date in August through December of this era raises instead
#: of receiving a number nobody read off a page.
#:
#: Extending this by assuming the rest of the readopted table would recreate
#: exactly the failure the era split exists to prevent.
SCOTT_SCHEDULE_2021: tuple[FlowPeriod, ...] = (
    FlowPeriod(1, 1, 1, 31, 200),  # stated in a 2021-era addendum
    FlowPeriod(
        2, 1, 2, 29, 200
    ),  # "the required February minimum flow requirement (200 cfs)", Addendum 40
    FlowPeriod(3, 1, 3, 31, 200),  # stated in a 2021-era addendum
    FlowPeriod(
        4, 1, 4, 30, 150
    ),  # "the required minimum flow ... for April is 150 cfs", Addendum 20
    FlowPeriod(5, 1, 5, 31, 150),  # stated in a 2021-era addendum
    FlowPeriod(6, 1, 6, 23, 125),  # "125 cfs through June 23", Addendum 30
    FlowPeriod(6, 24, 6, 30, 90),  # "90 cfs from June 24 through June 30", Addendum 30
    FlowPeriod(
        7, 1, 7, 31, 50
    ),  # "the required July minimum flow requirement (50 cfs)", Addendum 51
)

#: Shasta, 2021 era. EMPTY: no fetched 2021-era Shasta document states a monthly
#: minimum in a form that could be read off the page. The Shasta orders recite
#: that "the Regulation establishes minimum instream flows" without printing the
#: table, so there is nothing to encode yet and every Shasta date in this era
#: refuses.
SHASTA_SCHEDULE_2021: tuple[FlowPeriod, ...] = ()

SCHEDULES_BY_ERA: dict[RegulatoryEra, dict[Basin, tuple[FlowPeriod, ...]]] = {
    RegulatoryEra.ERA_2024: SCHEDULES,
    # Shasta is present and EMPTY, not absent. The behaviour is identical, but a
    # review pointed out the previous version left SHASTA_SCHEDULE_2021 unwired,
    # so the refusal actually came from a missing dict key while the comment
    # credited the empty tuple. A maintainer wiring the constant in would have
    # changed nothing while believing they had.
    RegulatoryEra.ERA_2021: {
        Basin.SCOTT: SCOTT_SCHEDULE_2021,
        Basin.SHASTA: SHASTA_SCHEDULE_2021,
    },
}


class ScheduleGapError(LookupError):
    """Raised when no period covers a date.

    This fails loudly rather than returning a default. A silently defaulted
    minimum flow would produce a confident curtailment recommendation built on
    a number nobody chose.
    """


class EraNotEncodedError(ScheduleGapError):
    """Raised when the governing era's table has not been encoded.

    A subclass of ScheduleGapError so existing callers that already refuse on a
    gap keep refusing, and so nothing has to be taught a new failure mode to stay
    safe. The distinction exists because the two are different problems: a gap
    means the encoded table has a hole, an unencoded era means the right table
    was never entered at all.
    """


def minimum_flow(
    basin: Basin,
    when: date,
    *,
    override: tuple[FlowPeriod, ...] | None = None,
) -> float:
    """Return the operative minimum instream flow in cfs for a basin on a date.

    Args:
        basin: Which watershed.
        when: The date being evaluated.
        override: An alternative schedule adopted under 875(c)(2)(B) through (D),
            for example the CDFW ramping table. When supplied it is consulted
            first, and the baseline schedule is the fallback.

    Raises:
        ScheduleGapError: If no period covers the date.
    """
    if override is not None:
        for period in override:
            if period.contains(when):
                return period.cfs

    era = era_for(when)
    schedule = SCHEDULES_BY_ERA[era].get(basin)
    if not schedule:
        raise EraNotEncodedError(
            f"{when.isoformat()} falls in the {era.value} era, whose flow table is "
            f"not encoded for this basin. That table has {VERIFIED_PHRASE}, so "
            f"answering from the readopted one risks applying a rule that was not "
            f"in force and marking the Board wrong for following the one that was. "
            f"Encode the {era.value} schedule for this basin from a primary source "
            f"before evaluating this date."
        )

    for period in schedule:
        if period.contains(when):
            return period.cfs

    raise ScheduleGapError(f"no minimum flow period covers {basin} on {when.isoformat()}")


def is_below_minimum(
    basin: Basin,
    when: date,
    observed_cfs: float,
    *,
    override: tuple[FlowPeriod, ...] | None = None,
) -> bool:
    """True when the observed discharge is under the operative minimum."""
    return observed_cfs < minimum_flow(basin, when, override=override)


#: Readings within this band of the minimum get flagged for field verification
#: rather than treated as decisive. This mirrors real practice at Fort Jones,
#: where the gage has documented accuracy concerns and readings have been
#: revised upward after Watermaster District field measurements.
NEAR_THRESHOLD_BAND_CFS = 10.0


def is_near_threshold(
    basin: Basin,
    when: date,
    observed_cfs: float,
    *,
    override: tuple[FlowPeriod, ...] | None = None,
) -> bool:
    """True when a reading sits within the near threshold band of the minimum."""
    minimum = minimum_flow(basin, when, override=override)
    return abs(observed_cfs - minimum) <= NEAR_THRESHOLD_BAND_CFS


__all__ = [
    "COMPLIANCE_GAGE",
    "NEAR_THRESHOLD_BAND_CFS",
    "SCHEDULES",
    "Basin",
    "FlowPeriod",
    "ScheduleGapError",
    "is_below_minimum",
    "is_near_threshold",
    "minimum_flow",
]
