"""Golden tests for penalty exposure.

The August 2022 case is a real enforcement action and is the reason AB 460
exists. It is encoded here as a test so the arithmetic that made a curtailment
order cheaper to violate than to obey stays visible.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from curtail_core.penalties import (
    AB_460_EFFECTIVE,
    DAILY_PENALTY,
    OBSOLETE_REGULATION_DAILY,
    PER_ACRE_FOOT_MAXIMUM,
    exposure,
    separate_exposures,
)


class TestStatutoryFigures:
    def test_daily_penalty_is_ten_thousand(self) -> None:
        """Water Code 1846(b)(1), as amended by AB 460."""
        assert DAILY_PENALTY == Decimal("10000")

    def test_per_acre_foot_maximum_is_twenty_five_hundred(self) -> None:
        """1846(b)(2). A ceiling, 'up to', not a rate."""
        assert PER_ACRE_FOOT_MAXIMUM == Decimal("2500")

    def test_amendment_took_effect_january_2025(self) -> None:
        assert AB_460_EFFECTIVE == date(2025, 1, 1)


class TestTheStaleRegulationGap:
    """The clearest evidence that the coordination layer is missing."""

    def test_the_published_regulation_understates_by_twenty_times(self) -> None:
        e = exposure(days_in_violation=1)
        assert e.understatement_multiple == Decimal("20")

    def test_both_figures_are_reported_not_just_the_correct_one(self) -> None:
        """Reporting only the right number would hide the finding."""
        e = exposure(days_in_violation=10)
        assert e.statutory_total_maximum == Decimal("100000")
        assert e.regulation_total == Decimal("5000")
        assert e.understatement_amount == Decimal("95000")

    def test_the_obsolescence_note_is_carried_on_every_result(self) -> None:
        e = exposure(days_in_violation=1)
        assert "875.9(b)" in e.obsolescence_note
        assert "January 1, 2025" in e.obsolescence_note

    def test_the_multiple_is_computed_not_hardcoded(self) -> None:
        """Stays true if either figure is amended again."""
        e = exposure(days_in_violation=7)
        assert e.understatement_multiple == e.statutory_daily / e.regulation_daily


class TestTheAugust2022Case:
    """The real enforcement action that produced AB 460.

    A water association serving about 80 ranchers ran pumps for eight days
    against a standing curtailment order. The Board proposed $4,000 total.
    """

    RANCHERS = 80
    DAYS = 8
    PROPOSED_TOTAL = Decimal("4000")

    def test_the_proposed_penalty_was_the_old_daily_figure_times_eight_days(self) -> None:
        assert OBSOLETE_REGULATION_DAILY * self.DAYS == self.PROPOSED_TOTAL

    def test_which_worked_out_to_about_fifty_dollars_per_rancher(self) -> None:
        per_rancher = self.PROPOSED_TOTAL / self.RANCHERS
        assert per_rancher == Decimal("50")

    def test_the_same_conduct_today_would_be_eighty_thousand(self) -> None:
        """The whole thesis in one assertion.

        Same violation, same duration. The arithmetic that made it rational to
        keep pumping no longer holds.
        """
        e = exposure(days_in_violation=self.DAYS, violation_date=date(2026, 8, 10))
        assert e.statutory_total_maximum == Decimal("80000")
        assert e.regulation_total == self.PROPOSED_TOTAL
        assert e.understatement_amount == Decimal("76000")


class TestVolumetricComponent:
    def test_acre_feet_produce_a_ceiling_not_an_assessment(self) -> None:
        e = exposure(days_in_violation=1, acre_feet_diverted=40)
        assert e.statutory_volumetric_maximum == Decimal("100000")
        assert e.statutory_total_maximum == Decimal("110000")

    def test_unknown_volume_omits_the_component_rather_than_assuming_zero(self) -> None:
        e = exposure(days_in_violation=3)
        assert e.statutory_volumetric_maximum is None
        assert e.statutory_total_maximum == Decimal("30000")

    def test_decimal_arithmetic_throughout(self) -> None:
        """Money is never binary float. A signed order cannot say $99999.99999997."""
        e = exposure(days_in_violation=3, acre_feet_diverted=0.1)
        assert isinstance(e.statutory_total_maximum, Decimal)
        assert e.statutory_volumetric_maximum == Decimal("250.0")


class TestRefusals:
    def test_conduct_predating_the_amendment_is_refused(self) -> None:
        """Applying today's figure backwards would overstate by twenty times."""
        with pytest.raises(ValueError, match="predates the AB 460 amendment"):
            exposure(days_in_violation=8, violation_date=date(2022, 8, 15))

    def test_zero_days_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            exposure(days_in_violation=0)

    def test_negative_volume_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            exposure(days_in_violation=1, acre_feet_diverted=-5)


class TestSeparateLiabilitiesAreNotSummed:
    def test_other_exposures_are_prose_not_arithmetic(self) -> None:
        """Whether 1052 applies is a legal judgment, not addition.

        Silently summing them would be the engine deciding a question it has no
        authority over, and the total would be indefensible on review.
        """
        others = separate_exposures()
        assert len(others) == 2
        assert any("1058.5(d)" in o for o in others)
        assert any("1052" in o for o in others)
        assert all("not automatically additive" in o or "not determined here" in o for o in others)

    def test_the_headline_total_excludes_them(self) -> None:
        e = exposure(days_in_violation=1)
        assert e.statutory_total_maximum == DAILY_PENALTY
