"""One entry point for a basin's rights, and it never hands back an empty list.

**The failure this closes.** Every call site used to load the Shasta record and filter by
basin, so asking for a basin the record did not contain returned an EMPTY LIST rather
than an error. An empty rights list is a valid input to the Core: it computes a
well-formed recommendation that reaches nobody, and an official reads that as "nothing to
do" rather than "we have no data for this river". Three call sites each carried their own
guard against it, at three different levels of care.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from curtail_core.basins import Basin
from curtail_core.rights import rights_for
from curtail_core.rights_record import RightsRecordUnavailableError
from curtail_core.scott_rights import load_scott_rights


class TestBothBasinsLoad:
    def test_shasta_loads_and_names_its_document(self) -> None:
        loaded = rights_for(Basin.SHASTA)
        assert loaded.rights, "an empty result must have raised instead"
        assert loaded.document
        assert all(r.basin is Basin.SHASTA for r in loaded.rights)

    def test_scott_loads_with_the_boards_stated_groups(self) -> None:
        loaded = rights_for(Basin.SCOTT)
        assert len(loaded.rights) == 384
        assert all(r.basin is Basin.SCOTT for r in loaded.rights)
        assert all(r.stated_group is not None for r in loaded.rights), (
            "a Scott right without a stated group would fall through to inference, "
            "which places it in group 1 and curtails it first"
        )

    def test_the_scott_record_never_invents_a_right_class(self) -> None:
        """The attachment states none. Inventing one would be a false claim in the data,
        and it is not needed: a stated group is read before class is ever consulted."""
        assert all(r.right_class is None for r in rights_for(Basin.SCOTT).rights)

    def test_scott_carries_what_its_record_does_not_settle(self) -> None:
        """An empty open-questions list reads as 'nothing to say'."""
        questions = " ".join(rights_for(Basin.SCOTT).open_questions)
        assert "right class" in questions


class TestItRefusesRatherThanReturningNothing:
    def test_a_missing_record_is_a_refusal_with_a_reason(self, tmp_path: Path) -> None:
        with pytest.raises(RightsRecordUnavailableError) as caught:
            load_scott_rights(tmp_path / "absent.json")
        assert "is not at" in str(caught.value)

    def test_a_record_edited_after_generation_is_refused(self, tmp_path: Path) -> None:
        """**The digest is checked at LOAD, not only in CI.** A record edited between the
        commit and the run would otherwise be trusted by the running service even though
        CI would have rejected it."""
        import json

        from curtail_core.scott_rights import record_path

        raw = json.loads(record_path().read_text())
        raw["rights"][0]["curtailment_group"] = 9
        target = tmp_path / "edited.json"
        target.write_text(json.dumps(raw))

        with pytest.raises(RightsRecordUnavailableError) as caught:
            load_scott_rights(target)
        assert "digest" in str(caught.value)

    def test_a_group_outside_the_regulation_is_refused(self, tmp_path: Path) -> None:
        """One rung is the difference between irrigating and shutting off, so a misread
        column must reach a human rather than be placed on a guess."""
        import json

        from curtail_core.scott_rights import _digest, record_path

        raw = json.loads(record_path().read_text())
        raw["rights"][0]["curtailment_group"] = 42
        raw["counts"]["rights_sha256"] = _digest(raw["rights"])
        target = tmp_path / "bad_group.json"
        target.write_text(json.dumps(raw))

        with pytest.raises(RightsRecordUnavailableError) as caught:
            load_scott_rights(target)
        assert "outside the nine groupings" in str(caught.value)


class TestUnknownDecreeMembershipRefuses:
    """The hole that `adjudication=None` could not express.

    `adjudication=None` is a CLAIM that a right sits outside all four decrees, which for
    the Scott means Group 1: the rung curtailed FIRST. That contract is deliberate and
    the ladder's own fixtures rely on it.

    An ingestion path that simply FAILED to determine membership had no way to say so. It
    passed the same None, and the ladder read a confident "outside the decrees" where the
    honest answer was "we do not know". Two sibling flags are tri-state for exactly this
    reason under the comment "Unknown is not False"; this one was two-state.

    Latent rather than live: the only Scott ingestion path carries the Board's stated
    group, so nothing in production reaches the inference. This closes it for the next
    path rather than fixing a bug that was firing.
    """

    def test_a_right_with_unknown_membership_is_refused_not_placed_in_group_one(
        self,
    ) -> None:
        from curtail_core.adjudications import RightClass, WaterRight
        from curtail_core.priority import PlacementError, place

        unknown = WaterRight(
            right_id="A000001",
            basin=Basin.SCOTT,
            right_class=RightClass.APPROPRIATIVE,
            decree_membership_unknown=True,
        )
        with pytest.raises(PlacementError) as caught:
            place(unknown)
        message = str(caught.value)
        assert "could not be determined" in message
        assert "Group 1" in message, "the refusal does not say what the guess would cost"

    def test_a_stated_claim_of_no_decree_still_places(self) -> None:
        """The other direction, and it is why this had to be additive. A caller that
        genuinely knows a right is post-adjudication says so with `adjudication=None`,
        and that must keep working or the ladder loses its most common Scott case."""
        from curtail_core.adjudications import RightClass, WaterRight
        from curtail_core.priority import place

        known = WaterRight(
            right_id="A000002",
            basin=Basin.SCOTT,
            right_class=RightClass.APPROPRIATIVE,
        )
        assert place(known).rank == 1

    def test_the_refusal_covers_the_shasta_ladder_too(self) -> None:
        """**A guard on one ladder is half a guard.**

        The first version of this check lived inside `_scott_group`, which left the
        identical hole one basin over: a Shasta right with unknown membership placed at
        Tier A, rank 1, the most junior tier and the one curtailed FIRST. Both ladders
        read absence of an adjudication as a confident "initiated after", so both need
        the refusal, and it belongs at the level that sees every right rather than inside
        either branch.
        """
        from curtail_core.adjudications import RightClass, WaterRight
        from curtail_core.priority import PlacementError, place

        for basin, rung in ((Basin.SHASTA, "Tier A"), (Basin.SCOTT, "Group 1")):
            unknown = WaterRight(
                right_id=f"X-{basin.value}",
                basin=basin,
                right_class=RightClass.APPROPRIATIVE,
                decree_membership_unknown=True,
            )
            with pytest.raises(PlacementError) as caught:
                place(unknown)
            assert rung in str(caught.value), (
                f"the {basin.value} refusal does not name the rung the guess would cost"
            )

    def test_a_stated_group_cannot_attach_to_a_shasta_right(self) -> None:
        """**The bypass: a field that meant nothing on one path bought a pass on another.**

        `stated_group` is a Scott concept. Set on a Shasta right it was silently discarded
        by the Shasta ladder, which does not read it, while still satisfying the exemption
        in the unknown-membership refusal. The right then placed at Tier A, rank 1, the
        rung curtailed first, on a flag that said its membership was unknown.

        Rejected at construction so the state cannot exist, following the precedent
        already set for `schedule`, which is refused on non-Scott rights for the same
        reason.
        """
        from curtail_core.adjudications import RightClass, WaterRight

        with pytest.raises(ValueError) as caught:
            WaterRight(
                right_id="SG-BYPASS",
                basin=Basin.SHASTA,
                right_class=RightClass.APPROPRIATIVE,
                decree_membership_unknown=True,
                stated_group=8,
            )
        message = str(caught.value)
        assert "Scott concept" in message
        assert "suppressed the unknown-membership refusal" in message, (
            "the rejection does not say what the field would have bought"
        )

    def test_the_exemption_is_scott_only_even_if_construction_were_bypassed(self) -> None:
        """Defence in depth, because the project's own rule is never one layer.

        Construction now makes the bad state unrepresentable, so this asserts the guard
        would hold anyway. `object.__setattr__` is used deliberately to build the state
        the constructor refuses, which is the only way to test the second layer at all.
        """
        from curtail_core.adjudications import RightClass, WaterRight
        from curtail_core.priority import PlacementError, place

        smuggled = WaterRight(
            right_id="SG-SMUGGLED",
            basin=Basin.SHASTA,
            right_class=RightClass.APPROPRIATIVE,
            decree_membership_unknown=True,
        )
        object.__setattr__(smuggled, "stated_group", 8)

        with pytest.raises(PlacementError):
            place(smuggled)

    def test_a_stated_group_outranks_the_unknown_flag(self) -> None:
        """Where the Board states the grouping there is nothing left to be unknown about,
        so the stated column is read and the refusal never fires."""
        from curtail_core.adjudications import WaterRight
        from curtail_core.priority import place

        stated = WaterRight(
            right_id="A000003",
            basin=Basin.SCOTT,
            decree_membership_unknown=True,
            stated_group=8,
        )
        assert place(stated).rank == 8
