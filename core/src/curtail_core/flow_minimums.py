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


class ScheduleGapError(LookupError):
    """Raised when no period covers a date.

    This fails loudly rather than returning a default. A silently defaulted
    minimum flow would produce a confident curtailment recommendation built on
    a number nobody chose.
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

    for period in SCHEDULES[basin]:
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
