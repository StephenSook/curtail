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
