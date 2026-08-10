"""Regression tests for defects found by an adversarial review on 2026-08-10.

Every test here corresponds to a path that was CONFIRMED by execution to
produce a confident wrong answer from a green test run. They are grouped in one
file so the class of defect stays visible: in each case an unknown, absent, or
malformed input silently became an authoritative statement in a record a state
official signs.

None of these were found by the original test suite. They were found by two
review agents and then verified against source before anything was changed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import curtail_core.adjudications as adj
import curtail_core.flow_minimums as fm
from curtail_agents.gage_client import GageError, Reading
from curtail_core.adjudications import (
    AdjudicationId,
    Basin,
    OverlyingLandFacts,
    RightClass,
    Schedule,
    WaterRight,
)
from curtail_core.priority import Disposition, PlacementError, curtailment_extends_to, place


def _scott(**kw: object) -> WaterRight:
    base: dict[str, object] = {
        "right_id": "R",
        "basin": Basin.SCOTT,
        "right_class": RightClass.APPROPRIATIVE,
        "is_surplus_class": False,
        "is_post_1914": False,
        "source": "scott_main",
    }
    base.update(kw)
    return WaterRight(**base)  # type: ignore[arg-type]


class TestBasinEnumIsSingular:
    """Basin was declared twice, in flow_minimums and in adjudications.

    Both were StrEnum with identical values, so `==` was True across them and
    `is` was False. Every routing decision in priority.py uses `is`, so a Scott
    right built with the wrong copy was placed on the SHASTA ladder and returned
    a citation asserting it was held under the Shasta Adjudication.
    """

    def test_there_is_exactly_one_basin_enum(self) -> None:
        assert adj.Basin is fm.Basin

    def test_identity_comparison_holds_across_modules(self) -> None:
        assert adj.Basin.SCOTT is fm.Basin.SCOTT
        assert adj.Basin.SHASTA is fm.Basin.SHASTA


class TestReadingRejectsImpossibleDischarge:
    """A NaN read as 'river healthy'; -999999 curtailed the whole watershed."""

    def test_nan_is_rejected(self) -> None:
        """NaN fails every comparison, so is_below_minimum returned False.

        The engine reported the river above its minimum and not even worth
        field-verifying, on a reading that does not exist. Python's json module
        parses bare NaN literals, so this arrived straight off the wire.
        """
        with pytest.raises(GageError, match="not finite"):
            Reading("USGS-11519500", datetime.now(UTC), float("nan"), "ft^3/s", None)

    def test_infinity_is_rejected(self) -> None:
        with pytest.raises(GageError, match="not finite"):
            Reading("USGS-11519500", datetime.now(UTC), float("inf"), "ft^3/s", None)

    def test_nwis_no_data_sentinel_is_rejected(self) -> None:
        """-999999 is the standard NWIS no-data sentinel.

        Passed through it curtails at maximum confidence off a dead sensor, and
        sits far outside the near-threshold band so it is not even flagged for
        field verification.
        """
        with pytest.raises(GageError, match="no-data sentinel"):
            Reading("USGS-11519500", datetime.now(UTC), -999999.0, "ft^3/s", None)

    def test_a_non_cfs_unit_is_rejected(self) -> None:
        """1.2 m^3/s is about 42 cfs, above the August Scott minimum of 30.

        Compared as cfs it reads 1.2 < 30 and reports BELOW MINIMUM.
        """
        with pytest.raises(GageError, match="ft\\^3/s"):
            Reading("USGS-11519500", datetime.now(UTC), 1.2, "m^3/s", None)

    def test_a_valid_reading_still_constructs(self) -> None:
        r = Reading("USGS-11517500", datetime.now(UTC), 49.1, "ft^3/s", None)
        assert r.cfs == 49.1


class TestUnknownIsNotFalse:
    """The two ladder flags are read BEFORE the Schedule D branch.

    A bare False meant "definitely not", which pushed a right to a MORE SENIOR
    grouping. A post-1914 right whose flag was dropped in ingestion placed three
    groupings too senior; a dropped surplus flag placed five too senior. That
    produced a real monotonicity violation: the senior right was curtailed while
    a junior right on the same source kept diverting.
    """

    def test_unknown_post_1914_raises(self) -> None:
        with pytest.raises(PlacementError, match="is_post_1914 is unknown"):
            place(
                WaterRight(
                    "J",
                    Basin.SCOTT,
                    RightClass.APPROPRIATIVE,
                    adjudication=AdjudicationId.SCOTT,
                    schedule=Schedule.D2,
                    is_surplus_class=False,
                    source="scott_main",
                )
            )

    def test_unknown_surplus_raises(self) -> None:
        with pytest.raises(PlacementError, match="is_surplus_class is unknown"):
            place(
                WaterRight(
                    "S",
                    Basin.SCOTT,
                    RightClass.APPROPRIATIVE,
                    adjudication=AdjudicationId.SCOTT,
                    schedule=Schedule.D1,
                    is_post_1914=False,
                    source="scott_main",
                )
            )

    def test_a_definite_false_still_places(self) -> None:
        """Unknown must raise; a genuine False must not."""
        assert place(_scott(adjudication=AdjudicationId.SCOTT, schedule=Schedule.D2)).rank == 6


class TestShastaLadderCanRefuse:
    """_shasta_tier ended in an unconditional return of Tier A.

    A right whose decree linkage was merely MISSING was asserted to be
    post-adjudication and placed in the first grouping curtailed, carrying a
    statutory citation that made the guess look authoritative. Both decrees are
    scanned imagery with imperfect OCR, so dropped linkage is a real ingestion
    outcome rather than a hypothetical.
    """

    def test_an_unplaceable_shasta_right_raises(self) -> None:
        facts = OverlyingLandFacts(True, True, False)
        r = WaterRight(
            "S",
            Basin.SHASTA,
            RightClass.OVERLYING_GROUNDWATER,
            overlying_facts=facts,
            is_surplus_class=False,
            is_post_1914=False,
            source="shasta_main",
        )
        # This one IS placeable, at Tier C. Guards against over-raising.
        assert place(r).rank == 3

    def test_both_basins_now_fail_the_same_way(self) -> None:
        """Scott raised and Shasta did not. The asymmetry was the defect."""
        assert issubclass(PlacementError, ValueError)


class TestConstructionTimeValidation:
    def test_shasta_overlying_without_facts_is_refused(self) -> None:
        """Without facts the 875.5(b)(1)(A) three-prong test cannot run.

        The right defaulted into the most senior tier carrying a justification
        byte-identical to one that actually passed the test.
        """
        with pytest.raises(ValueError, match="requires overlying_facts"):
            WaterRight(
                "O",
                Basin.SHASTA,
                RightClass.OVERLYING_GROUNDWATER,
                is_surplus_class=False,
                is_post_1914=False,
                source="shasta_main",
            )

    def test_cross_basin_adjudication_is_refused(self) -> None:
        """A Shasta right carrying a Scott decree produced a FALSE reason string."""
        with pytest.raises(ValueError, match="governs the scott basin"):
            WaterRight(
                "X",
                Basin.SHASTA,
                RightClass.APPROPRIATIVE,
                adjudication=AdjudicationId.SCOTT,
                is_surplus_class=False,
                is_post_1914=False,
                source="shasta_main",
            )

    def test_scott_schedule_on_a_shasta_right_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Scott decree"):
            WaterRight(
                "X",
                Basin.SHASTA,
                RightClass.APPROPRIATIVE,
                schedule=Schedule.D2,
                is_surplus_class=False,
                is_post_1914=False,
                source="shasta_main",
            )

    def test_nan_acre_feet_is_refused(self) -> None:
        """NaN fails `< 2.0`, so the 875.5(c) discretion was silently omitted."""
        with pytest.raises(ValueError, match="must be finite"):
            _scott(right_class=RightClass.OVERLYING_GROUNDWATER, annual_acre_feet=float("nan"))

    def test_negative_acre_feet_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            _scott(annual_acre_feet=-500.0)

    def test_contradictory_priority_date_is_refused(self) -> None:
        from datetime import date

        with pytest.raises(ValueError, match="priority_date_missing is set"):
            _scott(priority_date=date(1902, 1, 1), priority_date_missing=True)


class TestDataQualityDoesNotDecideOutcomes:
    def test_a_missing_date_does_not_flip_the_disposition(self) -> None:
        """A completeness note is not a discretionary provision.

        Both lived in one tuple, and `needs_official_review` was `bool(tuple)`,
        so a Group 1 right with a missing priority date came back
        DISCRETIONARY_REVIEW instead of CURTAILED even with curtailment
        extending across the whole ladder.
        """
        r = _scott(adjudication=None, priority_date_missing=True)
        p = place(r)
        assert p.data_quality_flags
        assert not p.judgment_inputs
        assert curtailment_extends_to(r, through_rank=9) is Disposition.CURTAILED

    def test_unknown_volume_is_surfaced_as_a_data_flag(self) -> None:
        """Previously an unknown volume silently read as 'at or above two'."""
        r = _scott(right_class=RightClass.OVERLYING_GROUNDWATER, annual_acre_feet=None)
        assert any("volume is unknown" in f for f in place(r).data_quality_flags)
