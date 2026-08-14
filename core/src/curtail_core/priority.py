"""The 23 CCR 875.5 priority ladder, for both basins.

Section 875.5 orders rights "in groupings from lowest to highest water right
priority". Curtailment extends UPWARD from the lowest grouping until there is
reasonable assurance the minimum flow will be met. Restoration runs the other
way: the grouping curtailed first is suspended last.

Two things this module deliberately does NOT do.

It does not decide how far curtailment extends. Section 875(b) says the Deputy
Director "may issue a curtailment order upon a determination", and 875(b)(3)
allows that official to decline to issue, to use a smaller priority grouping,
or to suspend orders already issued, weighing fisheries information, weather
forecasting, ramping needs, voluntary flow contributions and future flow needs.
This module computes who sits where on the ladder. A human decides where the
line falls.

It does not silently apply discretionary provisions. Section 875.5(c) says the
Deputy Director MAY decline to curtail groundwater diversions "of less than two
acre-feet per annum", and 875.5(b)(1)(C) says the Deputy Director MAY limit
overlying groundwater curtailment to larger or highest-impact diversions. A
"may" is a judgment input, so those rights are flagged for the official rather
than dropped from the set. Auto-applying a discretionary exemption would be the
engine quietly making a legal call it has no authority to make.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from curtail_core.adjudications import (
    SCOTT_ADJUDICATIONS,
    AdjudicationId,
    Basin,
    RightClass,
    Schedule,
    WaterRight,
)

#: The two tributary adjudications that share grouping (viii) with Schedule B.
_TRIBUTARY_ADJUDICATIONS: frozenset[AdjudicationId] = frozenset(
    {AdjudicationId.FRENCH_CREEK, AdjudicationId.SHACKLEFORD}
)


class ScottGroup(IntEnum):
    """23 CCR 875.5(a)(1)(A), materialized as Attachment A Groups 1 through 9.

    Group 1 is the lowest priority: curtailed first, suspended last.
    Group 9 is the most senior: curtailed last, suspended first.
    """

    POST_ADJUDICATION_APPROPRIATIVE = 1
    SURPLUS_CLASS = 2
    POST_1914_IN_ADJUDICATIONS = 3
    SCHEDULE_D4 = 4
    SCHEDULE_D3 = 5
    SCHEDULE_D2 = 6
    SCHEDULE_D1 = 7
    FRENCH_CREEK_SHACKLEFORD_SCHEDULE_B = 8
    SCHEDULE_C_AND_OVERLYING_GROUNDWATER = 9


class ShastaTier(IntEnum):
    """23 CCR 875.5(b)(1), lowest to highest priority."""

    POST_ADJUDICATION_APPROPRIATIVE = 1  # (A)
    ADJUDICATION_PRIORITIES = 2  # (B)
    RIPARIAN_AND_OVERLYING = 3  # (C)


class Disposition(StrEnum):
    """How a single right comes out of an evaluation.

    These are the classes the backtest scores per right, alongside the coarser
    action-plus-tier match.
    """

    CURTAILED = "curtailed"
    PROPORTIONALLY_ALLOCATED = "proportionally_allocated"
    NOT_REACHED = "not_reached"
    DISCRETIONARY_REVIEW = "discretionary_review"
    UNPLACEABLE = "unplaceable"


class PlacementError(ValueError):
    """Raised when a right cannot be placed on the ladder.

    Loud on purpose. A right that cannot be placed must reach a human, not be
    silently sorted into the most convenient group. Ending up one grouping too
    junior is the difference between a ranch irrigating and a ranch shut off.
    """


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a right sits, and the statutory reason it sits there."""

    right_id: str
    basin: Basin
    rank: int
    grouping_label: str
    citation: str
    reason: str
    #: Discretionary provisions that apply. Non-empty means a human must
    #: exercise or decline a "may" in the regulation.
    judgment_inputs: tuple[str, ...] = field(default_factory=tuple)
    #: Data-completeness notes. Deliberately a SEPARATE channel: a missing
    #: priority date is not a discretionary provision, and when both lived in
    #: one tuple a data note silently flipped a right's disposition from
    #: CURTAILED to DISCRETIONARY_REVIEW.
    data_quality_flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_official_review(self) -> bool:
        """True only for genuine discretionary provisions."""
        return bool(self.judgment_inputs)


def _scott_group(right: WaterRight) -> tuple[ScottGroup, str, str]:
    """Place a Scott right. Returns (group, citation, reason)."""
    # THE BOARD'S OWN COLUMN WINS, and the reason says so.
    #
    # Where Attachment A states the grouping, that is 875.5(a)(1)(A) applied by the
    # agency that administers it, and inference from whatever attributes we happen to
    # hold is a worse answer. The provenance travels into the ledger so an official
    # reading one line can tell a read value from a derived one.
    if right.stated_group is not None:
        if right.stated_group not in {int(g) for g in ScottGroup}:
            raise PlacementError(
                f"{right.right_id}: the record states group {right.stated_group}, which "
                "is not one of the nine groupings 875.5(a)(1)(A) defines. A misread "
                "column must reach a human rather than be placed on a guess."
            )
        group = ScottGroup(right.stated_group)
        return (
            group,
            "23 CCR 875.5(a)(1)(A)",
            "grouping stated by the State Water Board in Attachment A, not inferred",
        )

    # (i) post-Adjudication appropriative
    #
    # **`adjudication=None` here is a CLAIM that the right sits outside all four decrees**,
    # which for the Scott means post-Adjudication and Group 1. That is the field's
    # documented contract and callers rely on it deliberately.
    #
    # What used to be missing was any way for an ingestion path to say it could not
    # determine membership: it had to pass the same None, and this branch read that as a
    # confident "outside the decrees". `decree_membership_unknown` closes it above, and
    # the refusal is placed BEFORE this branch because there is no recovering from a
    # guess afterwards.
    #
    # Why it matters in numbers rather than in principle: placing the Board's own 384
    # Scott rights from attributes alone lands every one of them here, in Group 1, while
    # the attachment puts 258 in Group 8. Group 1 is curtailed first and Group 8 nearly
    # last, so a wrong read of this branch is the difference between a ranch irrigating
    # and a ranch shutting off. Real Scott rights avoid it entirely by carrying the
    # Board's stated group.
    if not right.is_in_decree and right.right_class is RightClass.APPROPRIATIVE:
        return (
            ScottGroup.POST_ADJUDICATION_APPROPRIATIVE,
            "23 CCR 875.5(a)(1)(A)(i)",
            "appropriative right initiated after the Scott River Adjudication",
        )

    # (ix) Schedule C, plus overlying groundwater not in the decree. Checked
    # before the surplus and post-1914 branches because an overlying
    # groundwater right outside the decree belongs at the senior end, not in
    # group 1 with post-adjudication appropriators.
    if right.schedule is Schedule.C:
        return (
            ScottGroup.SCHEDULE_C_AND_OVERLYING_GROUNDWATER,
            "23 CCR 875.5(a)(1)(A)(ix)",
            "Schedule C, ground water interconnected with the Scott River",
        )
    if right.right_class is RightClass.OVERLYING_GROUNDWATER and not right.is_in_decree:
        return (
            ScottGroup.SCHEDULE_C_AND_OVERLYING_GROUNDWATER,
            "23 CCR 875.5(a)(1)(A)(ix)",
            "overlying ground water diversion not included in the decree",
        )
    if right.right_class is RightClass.RIPARIAN:
        return (
            ScottGroup.SCHEDULE_C_AND_OVERLYING_GROUNDWATER,
            "23 CCR 875.5(a)(1)(A)(ix)",
            "riparian right, correlative and senior to appropriative rights",
        )

    # Unknown flags must not fall through. Both branches below are tested
    # BEFORE Schedule D, so a False reads as a confident "no" and pushes the
    # right to a MORE SENIOR grouping. Unknown is not False.
    if right.is_surplus_class is None or right.is_post_1914 is None:
        unknown = [
            name
            for name, value in (
                ("is_surplus_class", right.is_surplus_class),
                ("is_post_1914", right.is_post_1914),
            )
            if value is None
        ]
        raise PlacementError(
            f"{right.right_id}: cannot place on the Scott ladder because "
            f"{' and '.join(unknown)} is unknown. These are read before the "
            "Schedule D branch, so treating unknown as False would place this "
            "right too senior and could curtail a senior right while leaving a "
            "junior one on the same source diverting. This right must reach a human."
        )

    # (ii) Surplus Class in all schedules
    if right.is_surplus_class:
        return (
            ScottGroup.SURPLUS_CLASS,
            "23 CCR 875.5(a)(1)(A)(ii)",
            "Surplus Class right, in all schedules",
        )

    # (iii) all post-1914 in the three adjudications
    if right.is_post_1914 and right.adjudication in SCOTT_ADJUDICATIONS:
        return (
            ScottGroup.POST_1914_IN_ADJUDICATIONS,
            "23 CCR 875.5(a)(1)(A)(iii)",
            "post-1914 appropriative right within the three Scott adjudications",
        )

    # (iv) through (vii) Schedule D, most junior D-rank curtailed first
    d_ranks = {
        Schedule.D4: (ScottGroup.SCHEDULE_D4, "(iv)"),
        Schedule.D3: (ScottGroup.SCHEDULE_D3, "(v)"),
        Schedule.D2: (ScottGroup.SCHEDULE_D2, "(vi)"),
        Schedule.D1: (ScottGroup.SCHEDULE_D1, "(vii)"),
    }
    if right.schedule in d_ranks:
        group, roman = d_ranks[right.schedule]
        return (
            group,
            f"23 CCR 875.5(a)(1)(A){roman}",
            f"Schedule {right.schedule.value}, natural flow of the Scott River",
        )

    # (viii) French Creek plus Shackleford plus Schedule B
    if right.schedule is Schedule.B or right.adjudication in _TRIBUTARY_ADJUDICATIONS:
        return (
            ScottGroup.FRENCH_CREEK_SHACKLEFORD_SCHEDULE_B,
            "23 CCR 875.5(a)(1)(A)(viii)",
            "French Creek or Shackleford adjudication, or Schedule B independent tributary",
        )

    raise PlacementError(
        f"{right.right_id}: cannot place on the Scott ladder. "
        f"class={right.right_class}, adjudication={right.adjudication}, "
        f"schedule={right.schedule}. This right must reach a human."
    )


def _shasta_tier(right: WaterRight) -> tuple[ShastaTier, str, str]:
    """Place a Shasta right. Returns (tier, citation, reason)."""
    # (C) riparian and overlying groundwater, the senior end
    if right.right_class is RightClass.RIPARIAN:
        return (
            ShastaTier.RIPARIAN_AND_OVERLYING,
            "23 CCR 875.5(b)(1)(C)",
            "riparian diversion",
        )
    if right.right_class is RightClass.OVERLYING_GROUNDWATER:
        # The three-prong test can reclassify this as appropriative.
        facts = right.overlying_facts
        if facts is not None and facts.is_appropriative():
            prongs = "; ".join(facts.deciding_prongs())
            return (
                ShastaTier.POST_ADJUDICATION_APPROPRIATIVE,
                "23 CCR 875.5(b)(1)(A)",
                f"claimed as overlying but appropriative in character: {prongs}",
            )
        return (
            ShastaTier.RIPARIAN_AND_OVERLYING,
            "23 CCR 875.5(b)(1)(C)",
            "overlying ground water diversion",
        )

    # (B) rights under the Shasta Adjudication priorities
    if right.is_in_decree:
        return (
            ShastaTier.ADJUDICATION_PRIORITIES,
            "23 CCR 875.5(b)(1)(B)",
            "right held under the priorities set forth in the Shasta Adjudication",
        )

    # (A) appropriative initiated after the Shasta Adjudication.
    # Positive evidence only. Previously this was an unconditional fallthrough,
    # so a right whose decree linkage was merely MISSING was asserted to be
    # post-adjudication and placed in the first grouping curtailed, with a
    # statutory citation that made the guess look authoritative.
    if right.right_class is RightClass.APPROPRIATIVE:
        return (
            ShastaTier.POST_ADJUDICATION_APPROPRIATIVE,
            "23 CCR 875.5(b)(1)(A)",
            "appropriative diversion initiated after the Shasta Adjudication",
        )

    raise PlacementError(
        f"{right.right_id}: cannot place on the Shasta ladder. "
        f"class={right.right_class}, adjudication={right.adjudication}. "
        "This right must reach a human."
    )


def _judgment_inputs(right: WaterRight, rank: int) -> tuple[str, ...]:
    """Discretionary provisions that apply to this right.

    Never auto-applied. Each one is a "may" in the regulation, so it belongs to
    the official, and the signed record should show it was put in front of them.
    """
    inputs: list[str] = []

    if (
        right.right_class is RightClass.OVERLYING_GROUNDWATER
        and right.annual_acre_feet is not None
        and right.annual_acre_feet < 2.0
    ):
        inputs.append(
            f"23 CCR 875.5(c): the Deputy Director may decline to curtail ground water "
            f"diversions of less than two acre-feet per annum. This right reports "
            f"{right.annual_acre_feet} af/yr."
        )

    if right.basin is Basin.SHASTA and rank == ShastaTier.RIPARIAN_AND_OVERLYING:
        inputs.append(
            "23 CCR 875.5(b)(1)(C): the Deputy Director may limit overlying ground water "
            "curtailment to larger diversions or those with the highest potential impact "
            "on surface flows."
        )

    return tuple(inputs)


def _data_quality_flags(right: WaterRight) -> tuple[str, ...]:
    """Completeness notes. Surfaced to the official, but never a disposition."""
    flags: list[str] = []
    if right.priority_date_missing:
        flags.append(
            "Priority date is missing and is recorded as missing. Riparian and pre-1914 "
            "claims are self-reported Statements of Water Diversion and Use, not permits "
            "reviewed by the Division, so no date is imputed."
        )
    if right.annual_acre_feet is None and right.right_class is RightClass.OVERLYING_GROUNDWATER:
        flags.append(
            "Annual diversion volume is unknown, so the 875.5(c) discretion over "
            "groundwater under two acre-feet per annum cannot be evaluated."
        )
    return tuple(flags)


def place(right: WaterRight) -> Placement:
    """Place one right on its basin's ladder, with citation and reasoning.

    Raises:
        PlacementError: when the right cannot be placed. Never guesses.
    """
    # **Unknown decree membership refuses on BOTH ladders, and it is checked here rather
    # than inside either one.**
    #
    # The first version guarded only the Scott ladder, which left the identical hole one
    # basin over: a Shasta right with this flag placed at Tier A, rank 1, the most junior
    # tier and the one curtailed FIRST. Both ladders read absence of decree membership the
    # same way, as a confident "initiated after the adjudication", so a guard on one of
    # them is half a guard. Membership is a property of the RIGHT, not of a ladder, so the
    # refusal belongs at the level that sees every right.
    #
    # `stated_group` is the one exemption and only Scott has it: where the Board prints the
    # grouping, there is nothing left to be unknown about and the column is read directly.
    if right.decree_membership_unknown and right.stated_group is None:
        rung = "Group 1" if right.basin is Basin.SCOTT else "Tier A"
        raise PlacementError(
            f"{right.right_id}: cannot place on the {right.basin.value} ladder because "
            "decree membership could not be determined. Absence of an adjudication is "
            f"read as initiated-after, which places the right in {rung}, the rung "
            "curtailed FIRST, so treating an unknown as a confident claim can shut off a "
            "diverter the ladder never actually reached. This right must reach a human."
        )

    if right.basin is Basin.SCOTT:
        group, citation, reason = _scott_group(right)
        rank, label = int(group), f"Group {int(group)}"
    else:
        tier, citation, reason = _shasta_tier(right)
        rank = int(tier)
        label = f"Tier {'ABC'[int(tier) - 1]}"

    return Placement(
        right_id=right.right_id,
        basin=right.basin,
        rank=rank,
        grouping_label=label,
        citation=citation,
        reason=reason,
        judgment_inputs=_judgment_inputs(right, rank),
        data_quality_flags=_data_quality_flags(right),
    )


def curtailment_extends_to(right: WaterRight, *, through_rank: int) -> Disposition:
    """Disposition for one right when curtailment extends through a rank.

    Curtailment runs from the lowest grouping upward, so a right is reached when
    its rank is at or below the stated extent.
    """
    placement = place(right)
    if placement.rank > through_rank:
        return Disposition.NOT_REACHED
    if placement.needs_official_review:
        return Disposition.DISCRETIONARY_REVIEW
    return Disposition.CURTAILED


def restoration_order(placements: list[Placement]) -> list[Placement]:
    """Order for lifting curtailment: most senior first.

    The grouping curtailed first is suspended last. Getting this backwards
    would restore the most junior diverters before the most senior, inverting
    the doctrine the whole system exists to administer.
    """
    return sorted(placements, key=lambda p: p.rank, reverse=True)


def curtailment_order(placements: list[Placement]) -> list[Placement]:
    """Order for imposing curtailment: most junior first."""
    return sorted(placements, key=lambda p: p.rank)
