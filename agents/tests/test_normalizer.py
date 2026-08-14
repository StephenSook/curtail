"""The Normalizer's guards, exercised without a model.

Every test here injects the generation call, so the suite never needs Ollama, a GPU or
a network. What CANNOT be proven offline is that a real Gemma produces anything useful,
and that is deliberately not faked: `scripts/run_normalizer.py` runs the real model over
a real Board document and writes `docs/NORMALIZER.md`, and
`test_the_local_run_record_is_honest` below reads that record. Split the same way as the
deployment probe, and for the same reason: a test that quietly skips when the model is
absent is a false green, and a test that mocks the model and calls it evidence is worse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from curtail_agents.normalizer import (
    ACTIONS,
    MAX_CHARS,
    NormalizedOrder,
    NormalizerRefusedError,
    NormalizerUnavailableError,
    normalize,
    split_narrative,
)

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "docs" / "NORMALIZER.md"

DOCUMENT = """State Water Resources Control Board
June 16, 2026

Subject: Order WR 2024-0006-DWR, Addendum 6: Reinstatement of Conditional
         Curtailments for Junior Water Rights in Shasta River Watershed

The State Water Board is reinstating conditional curtailments in the Shasta River
watershed. Water right diverters with priority dates between and including
November 25, 1912 and December 31, 1957 are conditionally curtailed.
"""


def _replies(payload: dict[str, object]) -> object:
    def generate(prompt: str, *, model: str) -> str:
        assert "DOCUMENT TEXT:" in prompt, "the document never reached the model"
        return json.dumps(payload)

    return generate


class TestNothingIsTrustedBecauseTheModelSaidIt:
    def test_a_value_absent_from_the_document_is_rejected_not_returned(self) -> None:
        """The failure this module exists to make impossible.

        `WR 2024-9999-DWR` is a perfectly well-formed order number. It passes the shape
        check. It is not in the document, so it is not a value.
        """
        result = normalize(
            DOCUMENT,
            generate=_replies(
                {
                    "order_number": "WR 2024-9999-DWR",
                    "addendum_number": "6",
                    "basin": "shasta",
                    "effective_date": "June 16, 2026",
                }
            ),
        )
        assert "order_number" not in result.values, "a fabricated order number was returned"
        assert "order_number" in result.unverified
        assert any("does not appear in the document" in n for n in result.notes)
        assert result.needs_human_reading

    def test_a_value_present_in_the_document_is_returned(self) -> None:
        """The other branch, so the guard cannot pass by rejecting everything."""
        result = normalize(
            DOCUMENT,
            generate=_replies(
                {
                    "order_number": "WR 2024-0006-DWR",
                    "addendum_number": "6",
                    "basin": "shasta",
                    "effective_date": "June 16, 2026",
                }
            ),
        )
        assert result.values["order_number"] == "WR 2024-0006-DWR"
        assert result.values["addendum_number"] == "6"
        assert result.values["effective_date"] == "June 16, 2026"
        assert not result.unverified

    def test_a_value_broken_across_a_line_still_verifies(self) -> None:
        """OCR and pdftotext break phrases across lines constantly.

        A naive substring test reports a correctly-read value as a fabrication, which
        trains a reader to ignore the guard. This project has already been bitten by a
        newline-bounded pattern failing on OCR output.
        """
        wrapped = DOCUMENT.replace("Order WR 2024-0006-DWR", "Order WR\n2024-0006-DWR")
        result = normalize(wrapped, generate=_replies({"order_number": "WR 2024-0006-DWR"}))
        assert result.values.get("order_number") == "WR 2024-0006-DWR"

    def test_a_well_shaped_value_of_the_wrong_shape_is_rejected(self) -> None:
        result = normalize(DOCUMENT, generate=_replies({"order_number": "2024-0006"}))
        assert "order_number" in result.unverified
        assert any("not the shape" in n for n in result.notes)

    def test_a_basin_outside_the_two_this_system_knows_is_rejected(self) -> None:
        result = normalize(DOCUMENT, generate=_replies({"basin": "Klamath"}))
        assert "basin" in result.unverified
        assert result.values.get("basin") is None


class TestAnInterpretationIsNeverAValue:
    """The distinction a live run cost, and the one a substring test cannot make."""

    def test_the_action_is_reported_separately_from_extracted_values(self) -> None:
        """ "curtail" appears in any document containing "curtailments".

        So a verbatim check "verifies" it even for an addendum whose actual act is
        reinstatement, which is a different legal act carrying a different service
        obligation under Water Code 1121.
        """
        result = normalize(DOCUMENT, generate=_replies({"action": "curtail"}))
        assert result.proposed_action == "curtail"
        assert "action" not in result.values, "an interpretation was returned as a value"
        assert result.needs_human_reading, "a proposed action always needs confirming"
        assert any("not a value quoted from it" in n for n in result.notes)

    def test_an_action_outside_the_vocabulary_is_refused(self) -> None:
        result = normalize(DOCUMENT, generate=_replies({"action": "annul"}))
        assert result.proposed_action is None
        assert "action" in result.unverified

    @pytest.mark.parametrize("action", ACTIONS)
    def test_every_action_in_the_vocabulary_is_accepted(self, action: str) -> None:
        result = normalize(DOCUMENT, generate=_replies({"action": action}))
        assert result.proposed_action == action


class TestItRefusesRatherThanDegrading:
    def test_an_empty_document_is_refused(self) -> None:
        with pytest.raises(NormalizerRefusedError, match="empty"):
            normalize("   ", generate=_replies({}))

    def test_an_oversize_document_is_refused_rather_than_truncated(self) -> None:
        """A truncated order still parses, and yields a confident reading of the half
        that survived. That is worse than no reading at all."""
        with pytest.raises(NormalizerRefusedError, match="Refusing rather than truncating"):
            normalize("x" * (MAX_CHARS + 1), generate=_replies({}))

    def test_non_json_output_is_an_outage_not_an_empty_result(self) -> None:
        def broken(prompt: str, *, model: str) -> str:
            return "I could not read that document."

        with pytest.raises(NormalizerUnavailableError, match="not JSON"):
            normalize(DOCUMENT, generate=broken)

    def test_a_declined_field_is_absent_rather_than_unverified(self) -> None:
        """The model saying "not stated" is the behaviour asked for, not a failure.

        Collapsing the two would make a well-behaved run look like a suspicious one,
        and a reader who cannot tell them apart stops reading either.
        """
        result = normalize(DOCUMENT, generate=_replies({"order_number": None}))
        assert "order_number" in result.absent
        assert "order_number" not in result.unverified


class TestTheNarrativeIsRoutedNotTruncated:
    def test_the_attachment_is_split_off_rather_than_cut_short(self) -> None:
        document = DOCUMENT + "\nEnclosure: Attachment to Addendum 6\nA000448 1916-08-28\n"
        narrative, attachment = split_narrative(document)
        assert "Enclosure" not in narrative
        assert "A000448" in attachment, "the table was discarded rather than routed"
        assert narrative + attachment == document, "splitting lost characters"

    def test_a_document_with_no_attachment_is_returned_whole(self) -> None:
        narrative, attachment = split_narrative(DOCUMENT)
        assert narrative == DOCUMENT
        assert attachment == ""


class TestTheLocalRunRecordIsHonest:
    """CI cannot run Gemma, so it reads what the local run recorded.

    Neither half suffices alone: the record could be stale and this check cannot know,
    which is why it asserts the things that would reveal a hand-edited or vacuous entry
    rather than pretending to verify the model itself.
    """

    def test_the_record_exists_and_names_a_real_model_and_document(self) -> None:
        assert RECORD.exists(), (
            "docs/NORMALIZER.md is missing. Run `make normalizer` on a machine with the "
            "local model, which is the only place the real run can happen."
        )
        text = RECORD.read_text()
        assert "gemma" in text.lower(), "the record does not name a Gemma model"
        assert "WR 2024-0006-DWR" in text, "the record does not name the document read"
        assert "sha256" in text.lower(), "the record does not pin the source it read"

    def test_the_record_states_what_was_not_verified(self) -> None:
        """A record showing only successes is the shape of a vacuous one."""
        text = RECORD.read_text()
        assert "proposed_action" in text, (
            "the record does not carry the model's proposed action, which is the field "
            "that always needs human confirmation and so must always be visible"
        )

    def test_the_record_never_claims_the_model_decided_anything(self) -> None:
        text = RECORD.read_text().casefold()
        for forbidden in ("determined by the model", "the model decided", "gemma determined"):
            assert forbidden not in text, f"the record claims {forbidden!r}"


def test_the_dataclass_reports_a_clean_run_as_clean() -> None:
    """Non-vacuity: `needs_human_reading` must be able to be False."""
    clean = NormalizedOrder(values={"order_number": "WR 2024-0006-DWR"})
    assert clean.needs_human_reading is False
