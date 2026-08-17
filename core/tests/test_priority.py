"""Golden tests for the 23 CCR 875.5 priority ladder.

Each test names the subdivision it encodes. When a test and the regulation
disagree, the regulation wins and the test is the bug.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st

from curtail_core.adjudications import (
    ADJUDICATIONS,
    SCOTT_ADJUDICATIONS,
    AdjudicationId,
    Basin,
    OverlyingLandFacts,
    RightClass,
    Schedule,
    WaterRight,
)
from curtail_core.priority import (
    Disposition,
    PlacementError,
    ScottGroup,
    ShastaTier,
    curtailment_extends_to,
    curtailment_order,
    place,
    restoration_order,
)


def scott(**kw: object) -> WaterRight:
    # The two ladder flags are tri-state in production and default to None
    # (unknown), which raises. Tests that are not about the unknown case supply
    # a definite False so they exercise the branch they are actually about.
    base: dict[str, object] = {
        "right_id": "TEST",
        "basin": Basin.SCOTT,
        "right_class": RightClass.APPROPRIATIVE,
        "is_surplus_class": False,
        "is_post_1914": False,
        "source": "scott_main",
    }
    base.update(kw)
    return WaterRight(**base)  # type: ignore[arg-type]


def shasta(**kw: object) -> WaterRight:
    base: dict[str, object] = {
        "right_id": "TEST",
        "basin": Basin.SHASTA,
        "right_class": RightClass.APPROPRIATIVE,
        "is_surplus_class": False,
        "is_post_1914": False,
        "source": "shasta_main",
    }
    base.update(kw)
    return WaterRight(**base)  # type: ignore[arg-type]


class TestFourAdjudications:
    """23 CCR 875.5(d). Four, not two, and three of them govern the Scott."""

    def test_all_four_are_present(self) -> None:
        assert len(ADJUDICATIONS) == 4

    def test_court_numbers_and_decree_dates(self) -> None:
        a = ADJUDICATIONS
        assert a[AdjudicationId.SCOTT].court_number.endswith("No. 30662")
        assert a[AdjudicationId.SCOTT].decree_date == date(1980, 1, 30)
        assert a[AdjudicationId.SHACKLEFORD].court_number == "No. 13775"
        assert a[AdjudicationId.SHACKLEFORD].decree_date == date(1950, 4, 3)
        assert a[AdjudicationId.FRENCH_CREEK].court_number == "No. 14478"
        assert a[AdjudicationId.FRENCH_CREEK].decree_date == date(1959, 7, 1)
        assert a[AdjudicationId.SHASTA].court_number == "No. 7035"
        assert a[AdjudicationId.SHASTA].decree_date == date(1932, 12, 29)

    def test_three_adjudications_govern_the_scott(self) -> None:
        """The ladder's phrase 'the three adjudications' is Scott-side only."""
        assert len(SCOTT_ADJUDICATIONS) == 3
        assert AdjudicationId.SHASTA not in SCOTT_ADJUDICATIONS

    def test_shasta_decree_is_the_oldest(self) -> None:
        dates = [a.decree_date for a in ADJUDICATIONS.values()]
        assert ADJUDICATIONS[AdjudicationId.SHASTA].decree_date == min(dates)


class TestScottLadder:
    """23 CCR 875.5(a)(1)(A), groupings (i) through (ix)."""

    def test_i_post_adjudication_appropriative_is_group_1(self) -> None:
        p = place(scott(adjudication=None))
        assert p.rank == ScottGroup.POST_ADJUDICATION_APPROPRIATIVE
        assert p.citation == "23 CCR 875.5(a)(1)(A)(i)"

    def test_ii_surplus_class_is_group_2(self) -> None:
        p = place(scott(adjudication=AdjudicationId.SCOTT, is_surplus_class=True))
        assert p.rank == ScottGroup.SURPLUS_CLASS

    def test_iii_post_1914_in_the_three_adjudications_is_group_3(self) -> None:
        for adj in SCOTT_ADJUDICATIONS:
            p = place(scott(adjudication=adj, is_post_1914=True))
            assert p.rank == ScottGroup.POST_1914_IN_ADJUDICATIONS, adj

    @pytest.mark.parametrize(
        ("schedule", "expected"),
        [
            (Schedule.D4, ScottGroup.SCHEDULE_D4),
            (Schedule.D3, ScottGroup.SCHEDULE_D3),
            (Schedule.D2, ScottGroup.SCHEDULE_D2),
            (Schedule.D1, ScottGroup.SCHEDULE_D1),
        ],
    )
    def test_iv_to_vii_schedule_d_ranks_descend(
        self, schedule: Schedule, expected: ScottGroup
    ) -> None:
        """D4 is the most junior of the D schedules and is curtailed first."""
        p = place(scott(adjudication=AdjudicationId.SCOTT, schedule=schedule))
        assert p.rank == expected

    def test_viii_schedule_b_is_group_8(self) -> None:
        p = place(scott(adjudication=AdjudicationId.SCOTT, schedule=Schedule.B))
        assert p.rank == ScottGroup.FRENCH_CREEK_SHACKLEFORD_SCHEDULE_B

    @pytest.mark.parametrize("adj", [AdjudicationId.FRENCH_CREEK, AdjudicationId.SHACKLEFORD])
    def test_viii_tributary_adjudications_are_group_8(self, adj: AdjudicationId) -> None:
        """This is why the two extra decrees are load-bearing, not enrichment.

        Without Shackleford and French Creek in the model, these rights have no
        home on the ladder at all.
        """
        p = place(scott(adjudication=adj))
        assert p.rank == ScottGroup.FRENCH_CREEK_SHACKLEFORD_SCHEDULE_B

    def test_ix_schedule_c_is_group_9(self) -> None:
        p = place(scott(adjudication=AdjudicationId.SCOTT, schedule=Schedule.C))
        assert p.rank == ScottGroup.SCHEDULE_C_AND_OVERLYING_GROUNDWATER

    def test_ix_overlying_groundwater_outside_the_decree_is_group_9(self) -> None:
        """Not group 1. Being outside the decree does not make it junior."""
        p = place(scott(right_class=RightClass.OVERLYING_GROUNDWATER, adjudication=None))
        assert p.rank == ScottGroup.SCHEDULE_C_AND_OVERLYING_GROUNDWATER

    def test_riparian_sits_at_the_senior_end(self) -> None:
        p = place(scott(right_class=RightClass.RIPARIAN, adjudication=None))
        assert p.rank == ScottGroup.SCHEDULE_C_AND_OVERLYING_GROUNDWATER

    def test_unplaceable_right_raises_rather_than_guessing(self) -> None:
        """A right that cannot be placed must reach a human.

        One grouping too junior is the difference between a ranch irrigating
        and a ranch shut off.
        """
        with pytest.raises(PlacementError, match="must reach a human"):
            place(scott(adjudication=AdjudicationId.SCOTT, schedule=None, is_post_1914=False))


class TestShastaLadder:
    """23 CCR 875.5(b)(1), tiers (A) through (C)."""

    def test_a_post_adjudication_appropriative_is_tier_a(self) -> None:
        p = place(shasta(adjudication=None))
        assert p.rank == ShastaTier.POST_ADJUDICATION_APPROPRIATIVE

    def test_b_adjudication_priorities_is_tier_b(self) -> None:
        p = place(shasta(adjudication=AdjudicationId.SHASTA))
        assert p.rank == ShastaTier.ADJUDICATION_PRIORITIES

    def test_c_riparian_is_tier_c(self) -> None:
        p = place(shasta(right_class=RightClass.RIPARIAN))
        assert p.rank == ShastaTier.RIPARIAN_AND_OVERLYING


class TestOverlyingVersusAppropriativeTest:
    """23 CCR 875.5(b)(1)(A). Any one prong makes it appropriative."""

    def test_true_overlying_use_stays_senior(self) -> None:
        facts = OverlyingLandFacts(True, True, False)
        p = place(shasta(right_class=RightClass.OVERLYING_GROUNDWATER, overlying_facts=facts))
        assert p.rank == ShastaTier.RIPARIAN_AND_OVERLYING

    def test_prong_1_does_not_own_overlying_land(self) -> None:
        facts = OverlyingLandFacts(False, True, False)
        p = place(shasta(right_class=RightClass.OVERLYING_GROUNDWATER, overlying_facts=facts))
        assert p.rank == ShastaTier.POST_ADJUDICATION_APPROPRIATIVE
        assert "does not own the overlying land" in p.reason

    def test_prong_2_uses_water_on_non_overlying_land(self) -> None:
        facts = OverlyingLandFacts(True, False, False)
        p = place(shasta(right_class=RightClass.OVERLYING_GROUNDWATER, overlying_facts=facts))
        assert p.rank == ShastaTier.POST_ADJUDICATION_APPROPRIATIVE
        assert "non-overlying land" in p.reason

    def test_prong_3_sells_or_distributes_to_another(self) -> None:
        facts = OverlyingLandFacts(True, True, True)
        p = place(shasta(right_class=RightClass.OVERLYING_GROUNDWATER, overlying_facts=facts))
        assert p.rank == ShastaTier.POST_ADJUDICATION_APPROPRIATIVE
        assert "sold or distributed" in p.reason

    def test_the_reason_names_which_prong_fired(self) -> None:
        """'Because a flag said so' is not a reviewable justification."""
        facts = OverlyingLandFacts(False, False, True)
        p = place(shasta(right_class=RightClass.OVERLYING_GROUNDWATER, overlying_facts=facts))
        assert len(facts.deciding_prongs()) == 3
        for fragment in ("does not own", "non-overlying", "sold or distributed"):
            assert fragment in p.reason


class TestDiscretionIsNeverAutoApplied:
    """A 'may' in the regulation belongs to the official, not the engine."""

    def test_small_groundwater_is_flagged_not_dropped(self) -> None:
        r = shasta(
            right_class=RightClass.OVERLYING_GROUNDWATER,
            annual_acre_feet=1.5,
            overlying_facts=OverlyingLandFacts(True, True, False),
        )
        p = place(r)
        assert p.needs_official_review
        assert any("875.5(c)" in j for j in p.judgment_inputs)
        # Still on the ladder. Flagged, not exempted.
        assert p.rank == ShastaTier.RIPARIAN_AND_OVERLYING

    def test_two_acre_feet_exactly_is_not_under_the_threshold(self) -> None:
        """The regulation says 'less than two acre-feet per annum'."""
        r = shasta(
            right_class=RightClass.OVERLYING_GROUNDWATER,
            annual_acre_feet=2.0,
            overlying_facts=OverlyingLandFacts(True, True, False),
        )
        assert not any("875.5(c)" in j for j in place(r).judgment_inputs)

    def test_missing_priority_date_is_surfaced_never_imputed(self) -> None:
        r = shasta(adjudication=AdjudicationId.SHASTA, priority_date_missing=True)
        p = place(r)
        assert any("recorded as missing" in f for f in p.data_quality_flags)

    def test_a_data_quality_flag_does_not_flip_the_disposition(self) -> None:
        """A missing date is a completeness note, not a discretionary provision.

        When both lived in one tuple, `needs_official_review` could not tell
        them apart, so a Group 1 right with a missing priority date came back
        DISCRETIONARY_REVIEW instead of CURTAILED even with curtailment
        extending across the entire ladder. A data note silently changed a
        curtailment outcome.
        """
        r = scott(adjudication=None, priority_date_missing=True)
        p = place(r)
        assert p.data_quality_flags, "the note must still be surfaced"
        assert not p.judgment_inputs, "but it is not a discretionary provision"
        assert not p.needs_official_review
        assert curtailment_extends_to(r, through_rank=9) is Disposition.CURTAILED

    def test_discretionary_review_is_its_own_disposition(self) -> None:
        r = shasta(
            right_class=RightClass.OVERLYING_GROUNDWATER,
            annual_acre_feet=0.5,
            overlying_facts=OverlyingLandFacts(True, True, False),
        )
        assert curtailment_extends_to(r, through_rank=3) is Disposition.DISCRETIONARY_REVIEW


class TestExtentAndOrdering:
    def test_a_right_above_the_extent_is_not_reached(self) -> None:
        senior = scott(adjudication=AdjudicationId.SCOTT, schedule=Schedule.C)  # group 9
        assert curtailment_extends_to(senior, through_rank=3) is Disposition.NOT_REACHED

    def test_a_right_at_the_extent_is_curtailed(self) -> None:
        junior = scott(adjudication=None)  # group 1
        assert curtailment_extends_to(junior, through_rank=1) is Disposition.CURTAILED

    def test_restoration_is_the_inverse_of_curtailment(self) -> None:
        """Curtailed first, suspended last. Inverting this inverts the doctrine."""
        rights = [
            scott(right_id="g1", adjudication=None),
            scott(right_id="g7", adjudication=AdjudicationId.SCOTT, schedule=Schedule.D1),
            scott(right_id="g9", adjudication=AdjudicationId.SCOTT, schedule=Schedule.C),
        ]
        placements = [place(r) for r in rights]
        assert [p.right_id for p in curtailment_order(placements)] == ["g1", "g7", "g9"]
        assert [p.right_id for p in restoration_order(placements)] == ["g9", "g7", "g1"]


class TestPriorityMonotonicity:
    """The invariant the whole doctrine rests on.

    A senior right is never curtailed while a junior right on the same source
    is left alone.
    """

    #: Real Scott rights spanning the whole ladder, each built through the same
    #: constructor production uses. The previous version of the property below
    #: generated three bare integers, never constructed a WaterRight, never
    #: called place(), and asserted only that max <= n implies min <= n, which
    #: is true for all integers. It passed against a FULLY INVERTED ladder,
    #: proven by mutation. The invariant this class is named for had no
    #: coverage at all.
    LADDER_SAMPLES: ClassVar[list[tuple[str, dict[str, object]]]] = [
        (
            "post-adjudication",
            {"adjudication": None, "is_surplus_class": False, "is_post_1914": False},
        ),
        (
            "surplus",
            {"adjudication": AdjudicationId.SCOTT, "is_surplus_class": True, "is_post_1914": False},
        ),
        (
            "post-1914",
            {"adjudication": AdjudicationId.SCOTT, "is_surplus_class": False, "is_post_1914": True},
        ),
        (
            "D4",
            {
                "adjudication": AdjudicationId.SCOTT,
                "schedule": Schedule.D4,
                "is_surplus_class": False,
                "is_post_1914": False,
            },
        ),
        (
            "D3",
            {
                "adjudication": AdjudicationId.SCOTT,
                "schedule": Schedule.D3,
                "is_surplus_class": False,
                "is_post_1914": False,
            },
        ),
        (
            "D2",
            {
                "adjudication": AdjudicationId.SCOTT,
                "schedule": Schedule.D2,
                "is_surplus_class": False,
                "is_post_1914": False,
            },
        ),
        (
            "D1",
            {
                "adjudication": AdjudicationId.SCOTT,
                "schedule": Schedule.D1,
                "is_surplus_class": False,
                "is_post_1914": False,
            },
        ),
        (
            "scheduleB",
            {
                "adjudication": AdjudicationId.SCOTT,
                "schedule": Schedule.B,
                "is_surplus_class": False,
                "is_post_1914": False,
            },
        ),
        (
            "scheduleC",
            {
                "adjudication": AdjudicationId.SCOTT,
                "schedule": Schedule.C,
                "is_surplus_class": False,
                "is_post_1914": False,
            },
        ),
    ]

    @given(
        extent=st.integers(min_value=1, max_value=9),
        i=st.integers(min_value=0, max_value=8),
        j=st.integers(min_value=0, max_value=8),
    )
    def test_senior_is_never_curtailed_while_junior_diverts(
        self, extent: int, i: int, j: int
    ) -> None:
        """The invariant the whole doctrine rests on, exercised through place().

        Builds two real rights on the same source, places them through the
        production code path, and asserts that if the more senior one is
        reached by a given extent then the more junior one must be too.
        """
        name_a, kw_a = self.LADDER_SAMPLES[i]
        name_b, kw_b = self.LADDER_SAMPLES[j]

        a = place(scott(right_id=name_a, source="scott_main", **kw_a))
        b = place(scott(right_id=name_b, source="scott_main", **kw_b))
        junior, senior = (a, b) if a.rank <= b.rank else (b, a)

        junior_reached = curtailment_extends_to(
            scott(
                right_id=junior.right_id,
                source="scott_main",
                **dict(self.LADDER_SAMPLES[i if junior is a else j][1]),
            ),
            through_rank=extent,
        )
        senior_reached = curtailment_extends_to(
            scott(
                right_id=senior.right_id,
                source="scott_main",
                **dict(self.LADDER_SAMPLES[j if senior is b else i][1]),
            ),
            through_rank=extent,
        )

        if senior_reached is not Disposition.NOT_REACHED:
            assert junior_reached is not Disposition.NOT_REACHED, (
                f"{senior.right_id} (rank {senior.rank}) was reached at extent "
                f"{extent} while {junior.right_id} (rank {junior.rank}), which is "
                f"more junior on the same source, was not"
            )

    def test_the_ladder_is_ordered_junior_to_senior(self) -> None:
        """Pins the actual sequence, so an inverted ladder cannot pass.

        This is the assertion the old tautology should have been.
        """
        ranks = [
            place(scott(right_id=name, source="scott_main", **kw)).rank
            for name, kw in self.LADDER_SAMPLES
        ]
        assert ranks == [1, 2, 3, 4, 5, 6, 7, 8, 9], ranks

    def test_d_schedule_ranks_are_strictly_ordered(self) -> None:
        ranks = [
            place(scott(adjudication=AdjudicationId.SCOTT, schedule=s)).rank
            for s in (Schedule.D4, Schedule.D3, Schedule.D2, Schedule.D1)
        ]
        assert ranks == sorted(ranks), "D4 must be curtailed before D3 before D2 before D1"
        assert len(set(ranks)) == 4

    def test_every_placement_carries_a_statutory_citation(self) -> None:
        """No placement without a cite. The ledger has to be reviewable."""
        samples = [
            scott(adjudication=None),
            scott(adjudication=AdjudicationId.SCOTT, schedule=Schedule.D2),
            scott(adjudication=AdjudicationId.FRENCH_CREEK),
            shasta(adjudication=AdjudicationId.SHASTA),
            shasta(right_class=RightClass.RIPARIAN),
        ]
        for r in samples:
            p = place(r)
            assert p.citation.startswith("23 CCR 875.5")
            assert p.reason
