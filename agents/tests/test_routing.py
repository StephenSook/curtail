"""Failure-tolerant routing: the feature the rubric scores by name.

Two independent guards, tested for both directions. A guard that never fires and
a guard that always fires are the same defect wearing different clothes, and this
module has already produced one of each:

* The citation scrubber stripped VERIFIED authorities out of a lawful draft,
  because it compared candidate strings against allowlist strings and the two
  pattern sets disagreed on span boundaries.
* The ledger guard rejected every draft that named a priority date, because the
  ledger carried no dates to compare against. A draft citing the CORRECT cutoff
  was refused. A guard that blocks lawful orders gets switched off, which makes
  it worse than no guard at all.

The fabricated citations used here are SYNTHETIC. Writing a real fabricated
authority into a test ships that string in an artifact, and this repository's
own citation guard refuses it: an example is still text a reader can copy.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from curtail_agents.routing import (
    DraftAssertion,
    Verdict,
    scrub_citations,
    validate_draft,
)
from curtail_core.allocation import (
    Recommendation,
    RecommendedAction,
    RightLedgerEntry,
)
from curtail_core.basins import Basin
from curtail_core.priority import Placement

#: A body citing only verified authorities.
LAWFUL_BODY = "Under 23 CCR 875(b) and Water Code 1846(b), curtailment extends to Group 8."

#: Synthetic, on purpose. See the module docstring.
FABRICATED = " See also Fictional v. Invented, 111 P.9d 222."


@pytest.fixture
def recommendation() -> Recommendation:
    placement = Placement("A-001", Basin.SCOTT, 8, "Group 8", "875.5(a)(1)(A)(viii)", "post-1914")
    entry = RightLedgerEntry(
        right_id="A-001",
        placement=placement,
        reached_by_extent=True,
        lcs_protected=False,
        lcs_id=None,
        diversion_cfs=None,
        note="reached by the extent",
        priority_date=date(1912, 11, 25),
    )
    return Recommendation(
        basin=Basin.SCOTT,
        evaluated_for=date(2026, 6, 15),
        action=RecommendedAction.CONSIDER_CURTAILMENT,
        observed_cfs=Decimal("45.3"),
        operative_minimum_cfs=Decimal("50"),
        shortfall_cfs=Decimal("4.7"),
        near_threshold=False,
        recommended_extent_rank=8,
        ledger=(entry,),
    )


def draft(
    rights: tuple[str, ...] = ("A-001",),
    dates: tuple[date, ...] = (date(1912, 11, 25),),
    extent: int | None = 8,
    body: str = LAWFUL_BODY,
) -> DraftAssertion:
    return DraftAssertion(rights, dates, extent, body)


class TestALawfulDraftPasses:
    """The direction that matters most, and the one that was broken.

    Everything else in this file is about catching bad drafts. If a good draft
    cannot get through, the guard is removed in week two and none of the rest
    matters.
    """

    def test_a_draft_matching_the_ledger_passes(self, recommendation: Recommendation) -> None:
        assert validate_draft(draft(), recommendation).verdict is Verdict.PASS

    def test_a_passing_draft_may_reach_the_pdf_generator(
        self, recommendation: Recommendation
    ) -> None:
        assert validate_draft(draft(), recommendation).may_reach_pdf is True

    def test_the_correct_priority_cutoff_is_not_treated_as_unsupported(
        self, recommendation: Recommendation
    ) -> None:
        """November 25 1912 is the real Shasta Addendum 6 cutoff, and the guard
        refused it because the ledger carried no dates at all."""
        result = validate_draft(draft(dates=(date(1912, 11, 25),)), recommendation)
        assert result.unsupported_dates == ()

    def test_verified_authorities_survive_the_scrubber(self) -> None:
        _, stripped = scrub_citations(LAWFUL_BODY)
        assert stripped == ()


class TestTheLedgerGuardCatchesEachViolationClass:
    def test_a_right_absent_from_the_ledger_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        """A fluent order curtailing a right the Core never reached is the
        failure that survives human review, because it looks correct."""
        result = validate_draft(draft(rights=("A-001", "GHOST-9")), recommendation)
        assert result.verdict is not Verdict.PASS
        assert "GHOST-9" in result.unsupported_rights

    def test_a_priority_date_absent_from_the_ledger_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        """November 1 rather than November 25, 1912. Several research hauls
        carried the wrong one, and a draft asserting it must not pass."""
        result = validate_draft(draft(dates=(date(1912, 11, 1),)), recommendation)
        assert date(1912, 11, 1) in result.unsupported_dates

    def test_an_extent_that_disagrees_with_the_core_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        """Rank 3 instead of 8 is five priority groupings of difference, which
        is the gap between a ranch irrigating and a ranch shut off."""
        result = validate_draft(draft(extent=3), recommendation)
        assert result.extent_mismatch is not None
        assert result.verdict is not Verdict.PASS

    def test_an_unverifiable_citation_is_refused(self, recommendation: Recommendation) -> None:
        result = validate_draft(draft(body=LAWFUL_BODY + FABRICATED), recommendation)
        assert result.stripped_citations
        assert result.verdict is not Verdict.PASS

    def test_the_reason_names_what_was_wrong(self, recommendation: Recommendation) -> None:
        """A refusal with no diff is an assertion the reviewer cannot check."""
        result = validate_draft(draft(rights=("A-001", "GHOST-9")), recommendation)
        assert "GHOST-9" in result.reason


class TestRetryOnceThenEscalate:
    """A model that fails the same check twice is looping, not learning."""

    def test_a_first_failure_retries(self, recommendation: Recommendation) -> None:
        result = validate_draft(draft(rights=("GHOST-9",)), recommendation, attempt=1)
        assert result.verdict is Verdict.RETRY

    def test_a_second_failure_escalates_rather_than_retrying(
        self, recommendation: Recommendation
    ) -> None:
        result = validate_draft(draft(rights=("GHOST-9",)), recommendation, attempt=2)
        assert result.verdict is Verdict.ESCALATE

    def test_neither_retry_nor_escalate_reaches_the_pdf(
        self, recommendation: Recommendation
    ) -> None:
        for attempt in (1, 2):
            result = validate_draft(draft(rights=("GHOST-9",)), recommendation, attempt=attempt)
            assert result.may_reach_pdf is False

    def test_escalation_says_why_it_stopped_retrying(self, recommendation: Recommendation) -> None:
        result = validate_draft(draft(rights=("GHOST-9",)), recommendation, attempt=2)
        assert "looping, not learning" in result.reason


class TestTheScrubberIsAnAllowlistNotABlacklist:
    """You cannot blacklist a citation nobody has invented yet.

    Six external models reviewing this concept produced fabricated case law
    including two cases that exist in no database. The scrubber therefore strips
    anything citation-SHAPED that is not on the verified allowlist, rather than
    matching known-bad strings.
    """

    def test_an_invented_case_name_is_stripped(self) -> None:
        _, stripped = scrub_citations("Per Fictional v. Invented, curtailment is optional.")
        assert stripped

    def test_an_invented_reporter_cite_is_stripped(self) -> None:
        _, stripped = scrub_citations("See 111 P.9d 222 for the contrary view.")
        assert stripped

    def test_a_stripped_citation_leaves_a_visible_marker(self) -> None:
        """A silent strip is its own failure. The official needs to see that the
        draft tried to cite something unverifiable."""
        cleaned, _ = scrub_citations("Per Fictional v. Invented, curtailment is optional.")
        assert "CITATION REMOVED" in cleaned

    def test_mixed_text_strips_only_the_unverifiable(self) -> None:
        """The precise failure the span-overlap fix addressed."""
        cleaned, stripped = scrub_citations(LAWFUL_BODY + FABRICATED)
        assert len(stripped) >= 1
        assert "23 CCR 875(b)" in cleaned
        assert "Water Code 1846(b)" in cleaned

    @pytest.mark.parametrize(
        "verified",
        [
            "23 CCR 875.5",
            "Water Code 1122",
            "Water Code 1126(b)",
            "Stanford Vina Ranch Irrigation Co. v. State of California",
            "California Water Curtailment Cases",
            "Lux v. Haggin",
            "Board Resolution No. 2012-0061",
        ],
    )
    def test_each_verified_authority_survives(self, verified: str) -> None:
        """Every entry on the allowlist must actually pass its own scrubber.

        An allowlist whose entries the matcher cannot recognise is an allowlist
        that strips everything, which is how the first version behaved.
        """
        _, stripped = scrub_citations(f"As stated in {verified}, curtailment applies.")
        assert stripped == (), f"{verified} was stripped despite being verified"


class TestAnchoredFabricationsAreTheLikeliestOnes:
    """Both blockers found in review shared one root cause.

    `is_allowed` tested whether a candidate OVERLAPPED anything verified, which
    a fabrication satisfies simply by being built on one. A model steered toward
    the verified authorities will hallucinate on top of them, so the anchored
    fabrication is the likeliest output this system will ever see, and overlap
    gave it a free pass. Full coverage asks the right question: is every
    character of this claim vouched for?
    """

    @pytest.mark.parametrize(
        "body",
        [
            "Under Water Code 11223, the diverter must stop.",
            "Penalties under Water Code 18460 apply.",
            "Under 23 CCR 8759(z), curtailment applies.",
            "Under 23 CCR 875.55(q)(9), curtailment applies.",
        ],
    )
    def test_a_section_number_extending_a_verified_one_is_stripped(self, body: str) -> None:
        """Water Code 18460 does not exist. Water Code 1846 does, and its
        allowlist pattern matched the prefix."""
        _, stripped = scrub_citations(body)
        assert stripped, f"fabricated section survived: {body!r}"

    @pytest.mark.parametrize(
        "body",
        [
            "See California Water Curtailment Cases v. Siskiyou Water Users Assn.",
            "See Lux v. Haggin Irrigation Co. v. Fabricated Water District.",
        ],
    )
    def test_a_case_name_built_on_a_verified_stem_is_stripped(self, body: str) -> None:
        _, stripped = scrub_citations(body)
        assert stripped, f"anchored fabrication survived: {body!r}"

    @pytest.mark.parametrize(
        "body",
        [
            "Under Water Code section 9999, the diverter must stop.",
            "Under Water Code § 9999, the diverter must stop.",
        ],
    )
    def test_the_section_symbol_and_word_forms_are_seen(self, body: str) -> None:
        """The section symbol is the standard California statutory form, so it
        is the form a model trained on California legal text most likely emits.
        It was invisible to the shape matcher, so the allowlist was never even
        consulted."""
        _, stripped = scrub_citations(body)
        assert stripped, f"fabricated section in symbol form survived: {body!r}"


class TestAFullyCitedVerifiedAuthoritySurvives:
    """The other direction, and the one that gets a guard switched off.

    The reporter cite was being stripped out of a correctly cited authority,
    so a lawful draft citing the project's own preferred anchor was refused and
    escalated as UNVERIFIED.
    """

    @pytest.mark.parametrize(
        "body",
        [
            "Stanford Vina Ranch Irrigation Co. v. State of California (2020) "
            "50 Cal.App.5th 976 upheld it.",
            "See California Water Curtailment Cases, 83 Cal.App.5th 164.",
            "Lux v. Haggin (1886) 69 Cal. 255 is foundational.",
            "Water Code 1122 sets the window and Water Code 1126(b) the writ.",
            "Under 23 CCR 875(b) and Water Code 1846(b), curtailment extends to Group 8.",
        ],
    )
    def test_the_full_bluebook_form_is_untouched(self, body: str) -> None:
        _, stripped = scrub_citations(body)
        assert stripped == (), f"verified authority stripped: {list(stripped)}"


class TestReplacementIsPositionalNotTextual:
    def test_an_allowed_occurrence_is_not_clobbered_by_a_rejected_twin(self) -> None:
        """`str.replace` is positional-blind. A candidate disallowed at one
        offset and allowed at another was replaced at BOTH, deleting a verified
        citation as collateral damage while reporting only one removal."""
        body = (
            "Under 23 CCR 875(b) the Deputy Director acts. "
            "As further provided by CCR 875(b), exceptions apply."
        )
        cleaned, _ = scrub_citations(body)
        assert "23 CCR 875(b)" in cleaned, "the verified occurrence was destroyed"

    def test_a_sentence_final_period_is_not_swallowed(self) -> None:
        """The section shape captured the terminating period, so replacement
        merged two sentences in a legal document."""
        body = "Curtailment is imposed under Water Code 9999. The order is effective."
        cleaned, _ = scrub_citations(body)
        assert "The order is effective." in cleaned


class TestTheGuardCannotPassADraftItNeverRead:
    """Every other check is "nothing asserted that is unsupported", which an
    EMPTY draft satisfies completely. An extraction failure degraded to a
    confident PASS whose reason affirmatively said the draft matched the ledger.
    """

    def test_an_empty_draft_is_refused(self, recommendation: Recommendation) -> None:
        result = validate_draft(DraftAssertion((), (), None, ""), recommendation)
        assert result.verdict is not Verdict.PASS
        assert result.may_reach_pdf is False

    def test_the_refusal_says_the_draft_was_never_read(
        self, recommendation: Recommendation
    ) -> None:
        result = validate_draft(DraftAssertion((), (), None, ""), recommendation)
        assert "never have been read" in result.reason or "not to have been read" in result.reason

    def test_a_draft_omitting_a_reached_right_is_refused(
        self, recommendation: Recommendation
    ) -> None:
        """The failure this project's headline artifact is about. Order
        WR 2026-0005-DWR exists solely because rights were left OUT of an
        earlier order despite being within Priority Groups 1 through 8. A guard
        checking only over-inclusion would have passed that defective order.
        """
        entry = recommendation.ledger[0]
        second = RightLedgerEntry(
            right_id="B-002",
            placement=Placement("B-002", Basin.SCOTT, 8, "Group 8", "875.5", "post-1914"),
            reached_by_extent=True,
            lcs_protected=False,
            lcs_id=None,
            diversion_cfs=None,
            note="reached",
            priority_date=date(1912, 11, 25),
        )
        wider = Recommendation(
            basin=recommendation.basin,
            evaluated_for=recommendation.evaluated_for,
            action=recommendation.action,
            observed_cfs=recommendation.observed_cfs,
            operative_minimum_cfs=recommendation.operative_minimum_cfs,
            shortfall_cfs=recommendation.shortfall_cfs,
            near_threshold=recommendation.near_threshold,
            recommended_extent_rank=recommendation.recommended_extent_rank,
            ledger=(entry, second),
        )
        result = validate_draft(draft(rights=("A-001",)), wider)
        assert "B-002" in result.missing_rights
        assert result.verdict is not Verdict.PASS


class TestTheGuardValidatesItsOwnInputs:
    @pytest.mark.parametrize("attempt", [0, -1, -5])
    def test_an_attempt_below_one_is_refused(
        self, recommendation: Recommendation, attempt: int
    ) -> None:
        """A zero-indexed caller would silently get an extra retry and escalate
        a round later than documented."""
        with pytest.raises(ValueError):
            validate_draft(draft(), recommendation, attempt=attempt)


class TestAnAllowlistThatCouldDisarmTheGuardIsRefused:
    def test_a_pattern_matching_the_empty_string_raises(self, tmp_path: object) -> None:
        """One entry able to match empty produces zero-width allowed spans at
        every offset and makes the whole scrubber inert, while every test
        asserting a VERIFIED authority survives keeps passing. That is the
        conditionally-skipped-guard shape: green because nothing is exercised.
        """
        import json
        from pathlib import Path

        import curtail_agents.routing as routing

        original = routing._CITATIONS_PATH
        data = json.loads(original.read_text())
        data["allowlist"].append({"pattern": "(?:Rev\\. )?", "authority": "disarming"})
        probe = Path(str(tmp_path)) / "citations.json"
        probe.write_text(json.dumps(data))
        routing._CITATIONS_PATH = probe
        try:
            with pytest.raises(ValueError):
                scrub_citations("Per Fictional v. Invented, 111 P.9d 222.")
        finally:
            routing._CITATIONS_PATH = original

    def test_the_real_allowlist_still_strips_a_planted_fabrication(self) -> None:
        """Non-vacuity against the file that actually ships."""
        _, stripped = scrub_citations("Per Fictional v. Invented, 111 P.9d 222.")
        assert stripped
