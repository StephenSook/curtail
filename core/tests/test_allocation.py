"""Golden tests for the Allocation Core.

The headline case is Shasta Addendum 6, June 16, 2026. Figures are read from
the document: the Yreka USGS gage at 46.5 cfs on June 15 against the 50 cfs
requirement, and conditional curtailment reinstated on priority dates between
and including November 25, 1912 and December 31, 1957.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from curtail_core.adjudications import AdjudicationId, Basin, RightClass, Schedule, WaterRight
from curtail_core.allocation import RecommendedAction, recommend
from curtail_core.clocks import SignatoryRole
from curtail_core.lcs import LcsState, LocalCooperativeSolution


def right(rid: str, **kw: object) -> WaterRight:
    base: dict[str, object] = {
        "right_id": rid,
        "basin": Basin.SCOTT,
        "right_class": RightClass.APPROPRIATIVE,
        "adjudication": AdjudicationId.SCOTT,
        "is_surplus_class": False,
        "is_post_1914": False,
        "source": "scott_main",
    }
    base.update(kw)
    return WaterRight(**base)  # type: ignore[arg-type]


LADDER = [
    right("junior", adjudication=None),  # group 1
    right("d4", schedule=Schedule.D4),  # group 4
    right("d1", schedule=Schedule.D1),  # group 7
    right("senior", schedule=Schedule.C),  # group 9
]


class TestShastaAddendum6:
    """June 16, 2026. 46.5 cfs against the 50 cfs minimum at Yreka.

    Read from Addendum 6 itself. The 39.3 figure an earlier version used appears
    nowhere in the document.
    """

    def test_the_shortfall_is_computed_from_the_operative_minimum(self) -> None:
        rec = recommend(
            basin=Basin.SHASTA,
            when=date(2026, 6, 16),
            observed_cfs=46.5,
            rights=[],
        )
        assert rec.operative_minimum_cfs == Decimal("50")
        assert rec.shortfall_cfs == Decimal("3.5")

    def test_the_engine_and_the_board_diverge_here_and_that_is_correct(self) -> None:
        """The clearest illustration of why this engine recommends and does not decide.

        On the single reading the Addendum records, 46.5 cfs against 50, the
        engine returns FIELD_VERIFICATION_FIRST: the reading is 3.5 cfs under,
        well inside the 10 cfs band where gage accuracy alone decides the
        outcome.

        The Board reinstated curtailment. Its stated basis was not that one
        reading. The Addendum cites flows "repeatedly falling below the minimum
        requirement of 50 cfs for June-September 15", no significant snowpack
        remaining, a negligible 10-day precipitation forecast, and
        warmer-than-average temperatures.

        Those are precisely the "hydrologic, weather, and other conditions"
        section 875(b)(1) directs the official to weigh, and the engine does not
        have them. A version of this engine that reached the Board's conclusion
        from the reading alone would be pretending to a judgment it cannot make.
        The divergence is the design working.
        """
        rec = recommend(basin=Basin.SHASTA, when=date(2026, 6, 16), observed_cfs=46.5, rights=[])
        assert rec.near_threshold
        assert rec.action is RecommendedAction.FIELD_VERIFICATION_FIRST
        assert rec.needs_official_review

    def test_it_recommends_and_never_determines(self) -> None:
        rec = recommend(basin=Basin.SHASTA, when=date(2026, 6, 16), observed_cfs=46.5, rights=[])
        assert rec.needs_official_review
        assert rec.determination_belongs_to is SignatoryRole.DEPUTY_DIRECTOR


class TestNearThresholdDefersToTheField:
    """The case the whole field-verification layer exists for."""

    def test_a_reading_just_under_the_minimum_asks_for_verification_first(self) -> None:
        """Scott Addendum 9: 145 cfs against the 150 cfs May minimum.

        Five cfs under, inside the 10 cfs band. Gage accuracy alone separates
        curtailment from no curtailment.
        """
        rec = recommend(basin=Basin.SCOTT, when=date(2026, 5, 21), observed_cfs=145, rights=[])
        assert rec.near_threshold
        assert rec.action is RecommendedAction.FIELD_VERIFICATION_FIRST
        assert any(
            "Field verification" in j or "field measurements" in j for j in rec.judgment_inputs
        )

    def test_a_reading_far_below_does_not_defer(self) -> None:
        """Scott today: 6.28 against 30. No ambiguity to resolve in the field."""
        rec = recommend(basin=Basin.SCOTT, when=date(2026, 8, 10), observed_cfs=6.28, rights=[])
        assert not rec.near_threshold
        assert rec.action is RecommendedAction.CONSIDER_CURTAILMENT

    def test_at_or_above_minimum_but_inside_the_band_still_defers(self) -> None:
        """The July 22, 2025 shape: recovery, but not unambiguous recovery."""
        rec = recommend(basin=Basin.SCOTT, when=date(2026, 8, 10), observed_cfs=35, rights=[])
        assert rec.shortfall_cfs == 0
        assert rec.near_threshold
        assert rec.action is RecommendedAction.FIELD_VERIFICATION_FIRST


class TestExtentWalksTheLadderJuniorFirst:
    def test_curtailment_stops_once_the_shortfall_is_covered(self) -> None:
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),  # minimum 30
            observed_cfs=20,  # shortfall 10
            rights=LADDER,
            diversion_rates={"junior": 4, "d4": 7, "d1": 50, "senior": 50},
        )
        assert rec.shortfall_cfs == Decimal("10")
        assert rec.shortfall_arithmetic_closed
        # junior (4) + d4 (7) = 11 >= 10, so the extent stops at d4's group.
        assert rec.recommended_extent_rank == 4
        assert set(rec.rights_reached) == {"junior", "d4"}

    def test_the_most_senior_right_is_never_reached_first(self) -> None:
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),
            observed_cfs=29,
            rights=LADDER,
            diversion_rates={"junior": 100, "d4": 1, "d1": 1, "senior": 1},
        )
        assert rec.rights_reached == ("junior",)
        assert "senior" not in rec.rights_reached


class TestLcsProtection:
    def test_a_protected_right_is_excluded_but_listed(self) -> None:
        """Excluded from the curtailment set, still visible in the ledger."""
        lcs = LocalCooperativeSolution(
            lcs_id="LCS-1",
            basin=Basin.SCOTT,
            state=LcsState.APPROVED,
            coordinating_entity="Scott River Water Trust",
            covered_right_ids=frozenset({"junior"}),
            baseline_year=2022,
        )
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),
            observed_cfs=20,
            rights=LADDER,
            diversion_rates={"junior": 4, "d4": 7, "d1": 50, "senior": 50},
            lcs_solutions=[lcs],
        )
        assert "junior" in rec.rights_protected_by_lcs
        assert "junior" not in rec.rights_reached
        entry = next(e for e in rec.ledger if e.right_id == "junior")
        assert entry.lcs_id == "LCS-1"
        assert "875(f)" in entry.note

    def test_a_rescinded_lcs_does_not_protect(self) -> None:
        lcs = LocalCooperativeSolution(
            lcs_id="LCS-2",
            basin=Basin.SCOTT,
            state=LcsState.RESCINDED,
            coordinating_entity="Siskiyou Resource Conservation District",
            covered_right_ids=frozenset({"junior"}),
            baseline_year=2021,
        )
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),
            observed_cfs=20,
            rights=LADDER,
            diversion_rates={"junior": 4, "d4": 7, "d1": 50, "senior": 50},
            lcs_solutions=[lcs],
        )
        assert "junior" not in rec.rights_protected_by_lcs
        assert "junior" in rec.rights_reached


class TestIncompleteDataIsReportedNotExtrapolated:
    def test_unknown_volumes_leave_the_arithmetic_open(self) -> None:
        """A recommendation that guessed volumes would look identical to one that knew."""
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),
            observed_cfs=20,
            rights=LADDER,
            diversion_rates={"junior": 1},  # three unknown
        )
        assert not rec.shortfall_arithmetic_closed
        assert any("could not be closed" in f for f in rec.data_quality_flags)
        assert any("FLOOR" in f for f in rec.data_quality_flags)

    def test_a_missing_rate_is_never_treated_as_zero(self) -> None:
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),
            observed_cfs=20,
            rights=LADDER,
            diversion_rates={},
        )
        for entry in rec.ledger:
            assert entry.diversion_cfs is None
            if entry.reached_by_extent:
                assert "unknown" in entry.note

    def test_when_it_does_not_close_the_official_is_told_so_explicitly(self) -> None:
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),
            observed_cfs=1,
            rights=LADDER,
            diversion_rates={"junior": 1, "d4": 1, "d1": 1, "senior": 1},
        )
        assert not rec.shortfall_arithmetic_closed
        assert any("does not close" in j for j in rec.judgment_inputs)
        assert any("determination for the official" in j for j in rec.judgment_inputs)


class TestTheLedgerIsReviewable:
    def test_every_entry_carries_its_placement_and_citation(self) -> None:
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),
            observed_cfs=20,
            rights=LADDER,
            diversion_rates={"junior": 100},
        )
        assert len(rec.ledger) == len(LADDER)
        for entry in rec.ledger:
            assert entry.placement.citation.startswith("23 CCR 875.5")
            assert entry.note

    def test_the_ledger_is_ordered_junior_to_senior(self) -> None:
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),
            observed_cfs=20,
            rights=LADDER,
            diversion_rates={"junior": 100},
        )
        ranks = [e.placement.rank for e in rec.ledger]
        assert ranks == sorted(ranks)


class TestRefusals:
    def test_a_negative_reading_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sensor sentinel"):
            recommend(basin=Basin.SCOTT, when=date(2026, 8, 10), observed_cfs=-999999, rights=[])

    def test_an_unplaceable_right_aborts_the_whole_recommendation(self) -> None:
        """Better to produce nothing than a set that is quietly missing someone."""
        from curtail_core.priority import PlacementError

        bad = WaterRight(
            "unknown-flags",
            Basin.SCOTT,
            RightClass.APPROPRIATIVE,
            adjudication=AdjudicationId.SCOTT,
            schedule=Schedule.D2,
            source="scott_main",
        )
        with pytest.raises(PlacementError):
            recommend(basin=Basin.SCOTT, when=date(2026, 8, 10), observed_cfs=20, rights=[bad])


class TestProperties:
    @given(observed=st.decimals(min_value=0, max_value=500, places=1))
    def test_shortfall_is_never_negative(self, observed: Decimal) -> None:
        rec = recommend(basin=Basin.SCOTT, when=date(2026, 8, 10), observed_cfs=observed, rights=[])
        assert rec.shortfall_cfs >= 0

    @given(observed=st.decimals(min_value=0, max_value=500, places=1))
    def test_recommendation_is_idempotent(self, observed: Decimal) -> None:
        kw = {
            "basin": Basin.SCOTT,
            "when": date(2026, 8, 10),
            "rights": LADDER,
            "diversion_rates": {"junior": 4, "d4": 7, "d1": 50, "senior": 50},
        }
        a = recommend(observed_cfs=observed, **kw)  # type: ignore[arg-type]
        b = recommend(observed_cfs=observed, **kw)  # type: ignore[arg-type]
        assert a.recommended_extent_rank == b.recommended_extent_rank
        assert a.rights_reached == b.rights_reached

    @given(observed=st.decimals(min_value=0, max_value=29, places=1))
    def test_a_shortfall_always_reaches_the_most_junior_right_first(
        self, observed: Decimal
    ) -> None:
        """Monotonicity at the recommendation level, through the real code path."""
        rec = recommend(
            basin=Basin.SCOTT,
            when=date(2026, 8, 10),
            observed_cfs=observed,
            rights=LADDER,
            diversion_rates={"junior": 4, "d4": 7, "d1": 50, "senior": 50},
        )
        if rec.rights_reached:
            assert "junior" in rec.rights_reached
