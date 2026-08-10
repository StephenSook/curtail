"""A computed field that never reaches the manifest may as well not exist.

An adversarial review found three fields silently dropped at the serialisation
boundary, including `expires_on`, which the parser's own comment calls legally
load-bearing: a lapsed suspension reverts rights to curtailment with no further
order, so a model that ignores the expiry believes a suspension is still running
months after it ended.

Worse than a silent drop. The dropped expiry still appeared in the record under
`priority_dates`, because that pattern scans the whole document. An expiry was
being filed as a water-right priority date, which is a different legal object
that the Allocation Core ranks rights by.
"""

from __future__ import annotations

import sys
from dataclasses import fields
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from extract_corpus import _as_json, serialisable_field_names

from curtail_core.order_parser import (
    Extraction,
    ExtractionMethod,
    OrderAction,
    SuspensionQualifier,
)


def _full() -> Extraction:
    """Every field populated with a distinguishable value."""
    return Extraction(
        method=ExtractionMethod.TEXT_LAYER,
        action=OrderAction.SUSPEND,
        qualifier=SuspensionQualifier.LIMITED_CONDITIONAL,
        action_evidence="header slice",
        priority_groups=(8,),
        affects_all=False,
        priority_dates=(date(1912, 11, 25),),
        cfs_values=(45.3,),
        body_action=OrderAction.REINSTATE,
        title_body_conflict=True,
        expires_on=date(2024, 9, 30),
        text_chars=4321,
        notes=("a note",),
    )


class TestEveryComputedFieldSurvivesSerialisation:
    def test_no_dataclass_field_is_silently_dropped(self) -> None:
        """The guard that would have caught the original defect.

        Adding a field to Extraction without handling it here now fails, instead
        of quietly never reaching the manifest.
        """
        emitted = set(_as_json(_full()))
        expected = serialisable_field_names()
        missing = expected - emitted
        assert not missing, f"computed but never serialised: {sorted(missing)}"

    def test_the_exclusion_list_is_honest(self) -> None:
        """Whatever is excluded must actually be a field, so the list cannot rot
        into a place where a real omission hides behind a stale name."""
        declared = {f.name for f in fields(Extraction)}
        assert serialisable_field_names() <= declared

    def test_the_expiry_is_carried(self) -> None:
        assert _as_json(_full())["expires_on"] == "2024-09-30"

    def test_an_absent_expiry_serialises_as_null_not_as_missing(self) -> None:
        """Absent and unrecorded are different states. A missing key reads as
        'this field does not apply'; null reads as 'this document states none'."""
        payload = _as_json(Extraction(method=ExtractionMethod.REQUIRES_OCR))
        assert "expires_on" in payload
        assert payload["expires_on"] is None

    def test_the_conflict_flag_is_carried(self) -> None:
        """The most important state the parser detects. It previously survived
        only indirectly, as prose inside `notes`."""
        assert _as_json(_full())["title_body_conflict"] is True

    def test_the_body_action_is_carried(self) -> None:
        assert _as_json(_full())["body_action"] == "reinstate"
