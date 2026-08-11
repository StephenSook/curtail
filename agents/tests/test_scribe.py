"""The Scribe, and the guards that finally have something to guard.

Every case here drives the chain through an INJECTED generator rather than a model. That
is not avoidance: what needs testing is the behaviour when a model returns something
wrong, and a real model returning the right answer proves nothing about that. The live
wiring is exercised separately by `scripts/scribe_demo.py`, which runs the real Gemini
call end to end.

The failure this suite exists to prevent is a drafted order that asserts something the
Allocation Core did not compute. A watermaster signs the prose; the ledger is the only
authority on who is curtailed.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from curtail_agents.routing import Verdict
from curtail_agents.scribe import (
    MAX_ATTEMPTS,
    DraftOutcome,
    ScribeUnavailableError,
    build_prompt,
    draft_order,
    prose_disagrees_with_claims,
)
from curtail_core.adjudications import RightClass, WaterRight
from curtail_core.allocation import Recommendation, recommend
from curtail_core.basins import Basin

WHEN = date(2026, 6, 15)


@pytest.fixture(scope="module")
def recommendation() -> Recommendation:
    """A real Core run on real rights, so the guard has a genuine ledger to check."""
    rights = [
        WaterRight(
            right_id="A031522",
            basin=Basin.SHASTA,
            right_class=RightClass.APPROPRIATIVE,
            priority_date=date(2003, 7, 30),
            source="UNST",
        ),
        WaterRight(
            right_id="D031166",
            basin=Basin.SHASTA,
            right_class=RightClass.APPROPRIATIVE,
            priority_date=date(2001, 1, 17),
            source="UNST",
        ),
    ]
    result = recommend(basin=Basin.SHASTA, when=WHEN, observed_cfs=Decimal("41"), rights=rights)
    assert result.rights_reached, "the fixture must reach some rights or nothing is tested"
    return result


def sound_claims(recommendation: Recommendation, **overrides: Any) -> str:
    """A draft that should pass, so a test can break exactly one thing."""
    reached = list(recommendation.rights_reached)
    dates = [
        {"right_id": e.right_id, "priority_date": e.priority_date.isoformat()}
        for e in recommendation.ledger
        if e.would_be_curtailed and e.priority_date is not None
    ]
    payload: dict[str, Any] = {
        "order_text": (
            "DRAFT ORDER. Issued under 23 CCR 875. The following rights are curtailed: "
            + ", ".join(reached)
            + ". Exceptions apply for human health and safety and minimum livestock "
            "watering. Certification is required."
        ),
        "curtailed_right_ids": reached,
        "asserted_priority_dates": dates,
        "extent_rank": recommendation.recommended_extent_rank,
    }
    payload.update(overrides)
    return json.dumps(payload)


def answering(*responses: str) -> Any:
    """A generator returning each response in turn, so retry behaviour is observable."""
    remaining = list(responses)

    def call(prompt: str) -> str:
        assert prompt, "the drafter called the model with no prompt"
        return remaining.pop(0) if remaining else responses[-1]

    return call


class TestASoundDraftPasses:
    def test_it_reaches_the_pdf_on_the_first_attempt(self, recommendation: Recommendation) -> None:
        outcome = draft_order(recommendation, generate=answering(sound_claims(recommendation)))
        assert outcome.verdict is Verdict.PASS
        assert outcome.attempts == 1
        assert outcome.may_reach_pdf is True
        assert not outcome.violations

    def test_the_prose_survives_with_its_allowed_citation(
        self, recommendation: Recommendation
    ) -> None:
        outcome = draft_order(recommendation, generate=answering(sound_claims(recommendation)))
        assert "23 CCR 875" in outcome.text
        assert outcome.text.strip()


class TestADraftIsNeverTrustedForAFact:
    """Each case is a claim the Core did not compute. The model may write the prose; it
    may not decide who is curtailed."""

    def test_a_right_the_core_never_reached_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        bad = sound_claims(
            recommendation,
            curtailed_right_ids=[*recommendation.rights_reached, "A999999"],
            order_text="DRAFT ORDER under 23 CCR 875 curtailing "
            + ", ".join([*recommendation.rights_reached, "A999999"]),
        )
        outcome = draft_order(recommendation, generate=answering(bad, bad))
        assert outcome.escalated is True
        assert outcome.may_reach_pdf is False
        assert any("no basis in the ledger" in v for v in outcome.violations)

    def test_a_right_the_core_reached_but_the_draft_omits_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        """The failure Order WR 2026-0005-DWR was issued to correct: an order that
        curtails LESS than the law requires, because rights were left off the list."""
        kept = list(recommendation.rights_reached)[:1]
        bad = sound_claims(
            recommendation,
            curtailed_right_ids=kept,
            order_text="DRAFT ORDER under 23 CCR 875 curtailing " + ", ".join(kept),
        )
        outcome = draft_order(recommendation, generate=answering(bad, bad))
        assert outcome.escalated is True
        assert any("ABSENT from the draft" in v for v in outcome.violations)

    def test_a_priority_date_attached_to_the_wrong_right_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        swapped = [
            {"right_id": r, "priority_date": "1885-04-01"} for r in recommendation.rights_reached
        ]
        bad = sound_claims(recommendation, asserted_priority_dates=swapped)
        outcome = draft_order(recommendation, generate=answering(bad, bad))
        assert outcome.escalated is True
        assert any("do not match the right" in v for v in outcome.violations)

    def test_an_extent_the_core_did_not_compute_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        bad = sound_claims(recommendation, extent_rank=9)
        outcome = draft_order(recommendation, generate=answering(bad, bad))
        assert outcome.escalated is True
        assert any("extent" in v for v in outcome.violations)

    def test_an_unverified_authority_is_stripped_and_the_draft_refused(
        self, recommendation: Recommendation
    ) -> None:
        """A prior cycle's model invented a regulatory article number despite a prompt
        forbidding it. A system prompt is not a guardrail; the scrubber is."""
        bad = sound_claims(
            recommendation,
            # SYNTHETIC, and deliberately so. Using a real fabricated authority here
            # would ship that string in a committed artifact, which the citation guard
            # correctly refuses: an example is still text a reader can copy. chaos.py
            # made this same choice for the same reason.
            order_text="DRAFT ORDER under 23 CCR 875 and Placeholder v. Example "
            "curtailing " + ", ".join(recommendation.rights_reached),
        )
        outcome = draft_order(recommendation, generate=answering(bad, bad))
        assert outcome.escalated is True
        assert "Placeholder v. Example" not in outcome.text, (
            "a fabricated authority survived into the text"
        )


class TestTheProseAndTheClaimsMustAgree:
    """Asking for prose AND structured claims opens a gap the ledger guard cannot see.

    The guard reads the claims. A watermaster reads the prose. If they disagree, every
    mechanical check can pass on an order that says something else entirely.
    """

    def test_a_right_claimed_but_never_written_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        bad = sound_claims(
            recommendation,
            order_text="DRAFT ORDER under 23 CCR 875. Curtailment applies as computed.",
        )
        outcome = draft_order(recommendation, generate=answering(bad, bad))
        assert outcome.escalated is True
        assert any("appear nowhere in the order text" in v for v in outcome.violations)

    def test_a_right_written_but_never_claimed_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        reached = list(recommendation.rights_reached)
        bad = sound_claims(
            recommendation,
            curtailed_right_ids=reached[:1],
            order_text="DRAFT ORDER under 23 CCR 875 curtailing " + ", ".join(reached),
        )
        outcome = draft_order(recommendation, generate=answering(bad, bad))
        assert outcome.escalated is True

    def test_the_check_reads_both_directions(self, recommendation: Recommendation) -> None:
        """Directly, because the two directions fail differently: one order curtails
        less than it claims, the other curtails more than anything checked."""
        from curtail_agents.routing import DraftAssertion

        claimed_not_written = DraftAssertion(("A1",), (), None, "order text naming nobody")
        assert "appear nowhere" in prose_disagrees_with_claims(claimed_not_written, ("A1",))

        written_not_claimed = DraftAssertion((), (), None, "order curtailing A1")
        assert "absent from the stated claims" in prose_disagrees_with_claims(
            written_not_claimed, ("A1",)
        )


class TestTheLoopBreaker:
    def test_a_first_failure_retries_with_the_violation_fed_back(
        self, recommendation: Recommendation
    ) -> None:
        seen: list[str] = []

        def call(prompt: str) -> str:
            seen.append(prompt)
            if len(seen) == 1:
                return sound_claims(recommendation, extent_rank=9)
            return sound_claims(recommendation)

        outcome = draft_order(recommendation, generate=call)
        assert outcome.verdict is Verdict.PASS
        assert outcome.attempts == 2
        assert outcome.may_reach_pdf is True
        assert "A PREVIOUS ATTEMPT WAS REJECTED" in seen[1], (
            "the retry did not tell the model what was wrong, so it is a re-roll rather "
            "than a correction"
        )
        assert outcome.violations, "the fixed violation must still be recorded"

    def test_a_second_failure_escalates_rather_than_retrying_forever(
        self, recommendation: Recommendation
    ) -> None:
        calls = 0

        def call(prompt: str) -> str:
            nonlocal calls
            calls += 1
            return sound_claims(recommendation, extent_rank=9)

        outcome = draft_order(recommendation, generate=call)
        assert calls == MAX_ATTEMPTS, f"the drafter called the model {calls} times"
        assert outcome.verdict is Verdict.ESCALATE
        assert outcome.escalated is True
        assert outcome.may_reach_pdf is False

    def test_an_escalated_draft_still_carries_its_text(
        self, recommendation: Recommendation
    ) -> None:
        """A reviewer judging why a draft was refused needs to see what was written. The
        LABEL keeps it out of the PDF generator, not the withholding of the text."""
        outcome = draft_order(
            recommendation, generate=answering(sound_claims(recommendation, extent_rank=9))
        )
        assert outcome.escalated is True
        assert outcome.text.strip()


class TestAMalformedAnswerIsAFailureNotAnAbsence:
    """Every ledger check is of the form "nothing asserted that is unsupported", which an
    EMPTY assertion satisfies completely. Degrading to empty on a parse failure would
    produce a confident pass on the one path whose job is to prevent that."""

    @pytest.mark.parametrize(
        ("label", "raw"),
        [
            ("not json at all", "I could not comply."),
            ("json but not an object", "[1, 2, 3]"),
            ("no order text", '{"curtailed_right_ids": [], "asserted_priority_dates": []}'),
            (
                "empty order text",
                '{"order_text": "   ", "curtailed_right_ids": [], "asserted_priority_dates": []}',
            ),
            (
                "a date claim that is not a record",
                '{"order_text": "x", "curtailed_right_ids": [], "asserted_priority_dates": ["A1"]}',
            ),
            (
                "an unparseable date",
                '{"order_text": "x", "curtailed_right_ids": [], "asserted_priority_dates": '
                '[{"right_id": "A1", "priority_date": "last Tuesday"}]}',
            ),
            (
                "an extent that is not a rank",
                '{"order_text": "x", "curtailed_right_ids": [], '
                '"asserted_priority_dates": [], "extent_rank": "first"}',
            ),
        ],
    )
    def test_it_escalates_rather_than_passing_empty(
        self, recommendation: Recommendation, label: str, raw: str
    ) -> None:
        outcome = draft_order(recommendation, generate=answering(raw, raw))
        assert outcome.verdict is Verdict.ESCALATE, label
        assert outcome.may_reach_pdf is False, label
        assert outcome.violations, label


class TestItRefusesRatherThanStubbing:
    def test_no_project_means_no_draft(
        self, recommendation: Recommendation, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A canned order is the worst artifact this system could produce. It looks
        exactly like a real one and would be signed."""
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        with pytest.raises(ScribeUnavailableError, match="Refusing"):
            draft_order(recommendation)


class TestThePromptCannotDriftFromTheGuard:
    def test_it_offers_only_authorities_the_scrubber_allows(
        self, recommendation: Recommendation
    ) -> None:
        """Telling a model it may cite something the scrubber strips produces a draft
        that fails for a reason nobody intended."""
        from curtail_agents.routing import _CITATIONS_PATH

        allowed = {e["authority"] for e in json.loads(_CITATIONS_PATH.read_text())["allowlist"]}
        prompt = build_prompt(recommendation)
        assert allowed, "the allowlist is empty, so this proves nothing"
        for authority in allowed:
            assert authority in prompt, f"{authority} is allowed but never offered"

    def test_it_carries_every_reached_right_as_data(self, recommendation: Recommendation) -> None:
        prompt = build_prompt(recommendation)
        for right_id in recommendation.rights_reached:
            assert right_id in prompt

    def test_it_forbids_stating_a_penalty(self, recommendation: Recommendation) -> None:
        """23 CCR 875.9(b) still prints an obsolete $500 per day figure while Water Code
        1846(b) has said $10,000 since January 1 2025. A draft computing from the
        regulation text would be wrong by a factor of twenty."""
        assert "penalty" in build_prompt(recommendation).lower()

    def test_it_says_the_output_is_a_draft(self, recommendation: Recommendation) -> None:
        assert "DRAFT" in build_prompt(recommendation).upper()


def test_the_outcome_type_exposes_one_question(recommendation: Recommendation) -> None:
    """`may_reach_pdf` is a property rather than a caller's judgement, because "did the
    guard pass" is the one question every downstream consumer must ask."""
    outcome = draft_order(recommendation, generate=answering(sound_claims(recommendation)))
    assert isinstance(outcome, DraftOutcome)
    assert outcome.may_reach_pdf is True


class TestTheProseMustUseTheRightBasinsLadder:
    """Found by reading the first real drafted order.

    Every checkable claim passed and the prose described Shasta rights as curtailed by
    their "post-1914" status, which is a Scott concept. The ledger guard reads right ids,
    dates, the extent and the authorities. It does not read the sentences, and a
    watermaster signs the sentences.
    """

    def test_scott_vocabulary_in_a_shasta_order_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        bad = sound_claims(
            recommendation,
            order_text="DRAFT ORDER under 23 CCR 875 curtailing all post-1914 rights: "
            + ", ".join(recommendation.rights_reached),
        )
        outcome = draft_order(recommendation, generate=answering(bad, bad))
        assert outcome.escalated is True
        assert any("ladder vocabulary" in v for v in outcome.violations)

    def test_the_check_names_which_terms_it_found(self) -> None:
        from curtail_agents.scribe import wrong_basin_vocabulary

        assert wrong_basin_vocabulary("curtailing Priority Group 3", "shasta") == (
            "priority group",
        )
        assert wrong_basin_vocabulary("curtailing Tier A rights", "scott") == ("tier a",)

    def test_the_right_basins_own_language_passes(self, recommendation: Recommendation) -> None:
        """Non-vacuity, and it matters more than usual: a check that flagged everything
        would refuse every lawful Shasta order, which is worse than not having it."""
        from curtail_agents.scribe import wrong_basin_vocabulary

        assert (
            wrong_basin_vocabulary("curtailing Tier A rights under 875.5(b)(1)(A)", "shasta") == ()
        )
        assert wrong_basin_vocabulary("curtailing Priority Group 3 under Schedule D", "scott") == ()
        outcome = draft_order(recommendation, generate=answering(sound_claims(recommendation)))
        assert outcome.may_reach_pdf is True
