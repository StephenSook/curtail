"""Penalty exposure for violating a curtailment order.

This module computes from the STATUTE and treats the gap between the statute
and the published regulation as a first-class output rather than a comment,
because that gap is the clearest evidence that the coordination layer this
project builds is missing.

The controlling provision is Water Code 1846(b), as amended by AB 460
(Stats. 2024, Ch. 342), signed September 22, 2024, effective January 1, 2025:

    1846(b)(1)  "Ten thousand dollars ($10,000) for each day in which the
                violation occurs."
    1846(b)(2)  up to "Two thousand five hundred dollars ($2,500) for each
                acre-foot of water diverted."

Meanwhile 23 CCR 875.9(b), the regulation a diverter or an administrator would
actually read, still prints the pre-2025 figure: each violation "can carry a
fine of up to five hundred dollars ($500) for each day in which the violation
occurs." The published rule is stale on its face while the statute moved.

**A system computing liability from the regulation text is wrong by a factor of
twenty.** So `exposure()` returns both numbers and the multiple between them.
Reporting only the correct figure would hide the finding; reporting only the
regulation figure would be the defect itself.

The history that produced AB 460, verified: in August 2022 a water association
serving about 80 ranchers ran pumps for eight days against a standing
curtailment order. The Board proposed $4,000 in total, which is $500 per day
for eight days, roughly $50 per rancher. A rancher on the record: "We could
have kept going for $500 a day."

What this module deliberately does NOT do: assert that penalties stack. Water
Code 1058.5(d) creates separate infraction exposure of up to $500 per day for
violating an emergency regulation, and Water Code 1052 creates separate
trespass liability. Whether 1052 independently applies depends on the character
of the diversion, which is a legal judgment and not arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

#: Water Code 1846(b)(1). Per day of violation.
DAILY_PENALTY = Decimal("10000")

#: Water Code 1846(b)(2). Per acre-foot diverted, a ceiling rather than a rate.
PER_ACRE_FOOT_MAXIMUM = Decimal("2500")

#: 23 CCR 875.9(b). Obsolete since January 1, 2025, and still in the published
#: regulation. Retained ONLY so the gap can be computed and shown.
OBSOLETE_REGULATION_DAILY = Decimal("500")

#: The date AB 460's amendment took effect.
AB_460_EFFECTIVE = date(2025, 1, 1)

#: Water Code 1058.5(d). A separate infraction, not additive by default.
EMERGENCY_REGULATION_INFRACTION_DAILY = Decimal("500")


class PenaltyBasis(StrEnum):
    """Which authority a figure came from. Never inferred, always labeled."""

    STATUTE_1846B = "water_code_1846b"
    OBSOLETE_REGULATION_875_9B = "23_ccr_875.9b_obsolete"


@dataclass(frozen=True, slots=True)
class Exposure:
    """Computed penalty exposure, with the stale-regulation comparison attached."""

    days_in_violation: int
    acre_feet_diverted: Decimal | None

    #: Computed from Water Code 1846(b). The correct figure.
    statutory_daily: Decimal
    statutory_volumetric_maximum: Decimal | None
    statutory_total_maximum: Decimal

    #: What the published regulation's obsolete figure would have produced.
    regulation_daily: Decimal
    regulation_total: Decimal

    citation: str
    obsolescence_note: str

    @property
    def understatement_multiple(self) -> Decimal:
        """How many times too low the published regulation is on the daily figure.

        Twenty, as of AB 460. Exposed as a computed property rather than a
        hardcoded 20 so it stays true if either figure is ever amended again.
        """
        return self.statutory_daily / self.regulation_daily

    @property
    def understatement_amount(self) -> Decimal:
        """Absolute dollars a regulation-based calculation would miss."""
        return self.statutory_total_maximum - self.regulation_total


def exposure(
    *,
    days_in_violation: int,
    acre_feet_diverted: Decimal | float | None = None,
    violation_date: date | None = None,
) -> Exposure:
    """Compute penalty exposure under Water Code 1846(b).

    Args:
        days_in_violation: Days the violation occurred. Must be positive.
        acre_feet_diverted: Volume diverted in excess of the right, if known.
            The per-acre-foot figure is a statutory MAXIMUM ("up to"), not a
            rate, so this produces a ceiling and never a assessed amount.
        violation_date: Used to refuse a calculation for conduct predating the
            amendment, rather than silently applying today's law backwards.

    Raises:
        ValueError: on non-positive days, negative volume, or a violation date
            before AB 460 took effect.
    """
    if days_in_violation <= 0:
        raise ValueError(f"days_in_violation must be positive, got {days_in_violation}")

    if violation_date is not None and violation_date < AB_460_EFFECTIVE:
        raise ValueError(
            f"violation date {violation_date.isoformat()} predates the AB 460 amendment "
            f"effective {AB_460_EFFECTIVE.isoformat()}. Applying the current figure to "
            "earlier conduct would overstate exposure by twenty times. Compute the "
            "era-correct figure instead."
        )

    volume: Decimal | None = None
    if acre_feet_diverted is not None:
        volume = Decimal(str(acre_feet_diverted))
        if volume < 0:
            raise ValueError(f"acre_feet_diverted cannot be negative, got {volume}")

    days = Decimal(days_in_violation)
    statutory_daily_total = DAILY_PENALTY * days
    volumetric_max = PER_ACRE_FOOT_MAXIMUM * volume if volume is not None else None
    statutory_total = statutory_daily_total + (volumetric_max or Decimal("0"))

    return Exposure(
        days_in_violation=days_in_violation,
        acre_feet_diverted=volume,
        statutory_daily=DAILY_PENALTY,
        statutory_volumetric_maximum=volumetric_max,
        statutory_total_maximum=statutory_total,
        regulation_daily=OBSOLETE_REGULATION_DAILY,
        regulation_total=OBSOLETE_REGULATION_DAILY * days,
        citation="Water Code 1846(b), as amended by AB 460 (Stats. 2024, Ch. 342)",
        obsolescence_note=(
            "23 CCR 875.9(b) still prints a maximum of five hundred dollars ($500) per "
            "day. That figure has been obsolete since January 1, 2025. A liability "
            "calculation taken from the published regulation understates exposure by a "
            "factor of twenty."
        ),
    )


def separate_exposures() -> tuple[str, ...]:
    """Other liabilities that exist but are NOT added to the 1846(b) figure.

    Returned as prose for the record rather than summed, because whether they
    apply is a legal judgment about the character of the diversion. Silently
    adding them would be the engine deciding a question it has no authority
    over, and the resulting number would be indefensible on review.
    """
    return (
        "Water Code 1058.5(d): violating an emergency regulation is a separate "
        f"infraction punishable by up to ${EMERGENCY_REGULATION_INFRACTION_DAILY} per day. "
        "Distinct from 1846(b) and not automatically additive.",
        "Water Code 1052: unauthorized diversion is a trespass carrying separate civil "
        "liability. Whether it independently applies depends on the character of the "
        "diversion and is not determined here.",
    )
