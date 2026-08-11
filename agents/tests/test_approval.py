"""The approval queue, tested against the way human review actually fails.

Biro et al. measured 35 to 45 percent of erroneous drafts submitted entirely
unedited by clinicians who were confident the drafts were safe. A queue tested only
on its happy path would reproduce exactly that: everything works, and the one case
that matters is the one nobody exercised.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from curtail_agents.approval import (
    ApprovalError,
    Officer,
    QueueItem,
    QueueState,
    digest_of,
    sign,
)
from curtail_agents.routing import GuardResult, Verdict
from curtail_core.clocks import SignatoryRole

NOW = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
DRAFT = "ORDER WR 2026-0009-DWR\nCurtailment extends to Priority Group 4.\n"


def _clean_item(order_id: str = "WR 2026-0009-DWR") -> QueueItem:
    return QueueItem(
        order_id=order_id,
        draft_text=DRAFT,
        requires_role=SignatoryRole.DEPUTY_DIRECTOR,
        guard=GuardResult(verdict=Verdict.PASS),
        created_at=NOW,
    )


def _flagged_item() -> QueueItem:
    return QueueItem(
        order_id="WR 2026-0010-DWR",
        draft_text=DRAFT,
        requires_role=SignatoryRole.DEPUTY_DIRECTOR,
        guard=GuardResult(
            verdict=Verdict.ESCALATE,
            unsupported_rights=("A-003",),
            missing_rights=("A-002",),
            unsupported_dates=(date(1912, 11, 25),),
            stripped_citations=("Doe v. Roe",),
            reason="two layers rejected it",
        ),
        created_at=NOW,
    )


class TestNothingSelfExecutes:
    def test_approval_produces_a_record_and_not_an_action(self) -> None:
        """Hard rule 3 as a type. The output is evidence; no method on it acts."""
        decision = sign(
            _clean_item(),
            officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "test-harness"),
            reviewed_digest=_clean_item().digest,
            decided_at=NOW,
        )
        assert decision.approved
        assert decision.order_id == "WR 2026-0009-DWR"
        actions = [n for n in dir(decision) if n in {"send", "serve", "execute", "issue", "file"}]
        assert actions == [], f"a signed decision must not be able to act: {actions}"

    def test_the_record_names_the_officer_not_just_the_role(self) -> None:
        """An administrative record naming only a role cannot be audited."""
        with pytest.raises(ApprovalError):
            sign(
                _clean_item(),
                officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "   ", "test-harness"),
                reviewed_digest=_clean_item().digest,
                decided_at=NOW,
            )


class TestTheRegulationDrawsTheRoleLine:
    def test_the_wrong_officer_cannot_sign(self) -> None:
        """875(b) assigns curtailment to the Deputy Director. A signature from the
        Executive Director on that determination is not a lesser signature."""
        with pytest.raises(ApprovalError, match="875"):
            sign(
                _clean_item(),
                officer=Officer(SignatoryRole.EXECUTIVE_DIRECTOR, "e-director", "test-harness"),
                reviewed_digest=_clean_item().digest,
                decided_at=NOW,
            )

    def test_the_health_and_safety_determination_needs_the_executive_director(self) -> None:
        """875(b)(2), the case a generic human-in-the-loop queue gets wrong."""
        item = QueueItem(
            order_id="WR 2026-0011-DWR",
            draft_text=DRAFT,
            requires_role=SignatoryRole.EXECUTIVE_DIRECTOR,
            guard=GuardResult(verdict=Verdict.PASS),
            created_at=NOW,
        )
        with pytest.raises(ApprovalError):
            sign(
                item,
                officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "d-director", "test-harness"),
                reviewed_digest=item.digest,
                decided_at=NOW,
            )
        assert sign(
            item,
            officer=Officer(SignatoryRole.EXECUTIVE_DIRECTOR, "e-director", "test-harness"),
            reviewed_digest=item.digest,
            decided_at=NOW,
        ).approved


class TestAnApprovalBindsToTheBytesReviewed:
    def test_a_changed_draft_voids_the_approval(self) -> None:
        """The automation-bias case with teeth. A re-drafted order must not inherit
        the signature of the one a human actually read."""
        reviewed = digest_of(DRAFT)
        redrafted = QueueItem(
            order_id="WR 2026-0009-DWR",
            draft_text=DRAFT + "Curtailment extends to Priority Group 5.\n",
            requires_role=SignatoryRole.DEPUTY_DIRECTOR,
            guard=GuardResult(verdict=Verdict.PASS),
            created_at=NOW,
        )
        with pytest.raises(ApprovalError, match="changed after it was reviewed"):
            sign(
                redrafted,
                officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "test-harness"),
                reviewed_digest=reviewed,
                decided_at=NOW,
            )

    def test_the_digest_is_not_recomputed_from_the_current_draft(self) -> None:
        """If it were, the check would always pass and the stale-draft case it
        exists to catch would be invisible. Proven by the test above failing."""
        item = _clean_item()
        assert item.digest == digest_of(DRAFT)

    def test_two_different_drafts_never_share_an_identity(self) -> None:
        assert digest_of("abc") != digest_of("ab") + "c"
        assert digest_of("a\nb") != digest_of("ab")


class TestAnUnverifiedDraftCostsMoreToApprove:
    def test_it_is_not_merely_pending(self) -> None:
        """Collapsing the two states is how an unverified legal order reaches a
        reviewer looking exactly like a clean one."""
        assert _clean_item().state is QueueState.AWAITING_SIGNATURE
        assert _flagged_item().state is QueueState.UNVERIFIED

    def test_approving_it_blind_is_refused(self) -> None:
        item = _flagged_item()
        with pytest.raises(ApprovalError, match="naming what is being overridden"):
            sign(
                item,
                officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "test-harness"),
                reviewed_digest=item.digest,
                decided_at=NOW,
            )

    def test_overriding_a_subset_is_refused(self) -> None:
        """Acknowledging one finding and signing past three would record a signature
        against violations the officer never saw."""
        item = _flagged_item()
        with pytest.raises(ApprovalError, match="not acknowledged"):
            sign(
                item,
                officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "test-harness"),
                reviewed_digest=item.digest,
                decided_at=NOW,
                overriding=("unsupported right: A-003",),
            )

    def test_naming_every_violation_is_accepted_and_recorded_as_an_override(self) -> None:
        """The officer may overrule the machine. That is the point of a human in the
        loop, and the record has to say plainly that it happened."""
        item = _flagged_item()
        decision = sign(
            item,
            officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "test-harness"),
            reviewed_digest=item.digest,
            decided_at=NOW,
            overriding=item.blocking_violations,
            note="field measurement supersedes the gage reading",
        )
        assert decision.approved
        assert decision.is_override
        assert len(decision.overridden) == len(item.blocking_violations)

    def test_declining_needs_no_override(self) -> None:
        """Refusing to sign is always available, and never requires ceremony."""
        item = _flagged_item()
        decision = sign(
            item,
            officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "test-harness"),
            reviewed_digest=item.digest,
            decided_at=NOW,
            approved=False,
        )
        assert not decision.approved
        assert not decision.is_override

    def test_a_clean_draft_records_no_override(self) -> None:
        """Non-vacuity: `is_override` must distinguish, not always report True."""
        item = _clean_item()
        decision = sign(
            item,
            officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "test-harness"),
            reviewed_digest=item.digest,
            decided_at=NOW,
        )
        assert not decision.is_override


class TestTheReviewerIsToldWhichChecksFailed:
    def test_every_guard_finding_becomes_a_named_violation(self) -> None:
        """A reviewer told "2 issues" learns nothing. One told which rights are
        unsupported can go and look, which is the whole design against automation
        bias: force engagement with specifics rather than with a colour."""
        violations = _flagged_item().blocking_violations
        joined = " | ".join(violations)
        assert "A-003" in joined
        assert "A-002" in joined
        assert "1912-11-25" in joined
        assert "Doe v. Roe" in joined

    def test_a_clean_item_has_none(self) -> None:
        assert _clean_item().blocking_violations == ()


class TestAnItemCannotBeMalformed:
    def test_an_empty_draft_is_refused(self) -> None:
        """An approval on an empty document records a signature against nothing."""
        with pytest.raises(ValueError, match="empty draft"):
            QueueItem(
                order_id="WR-1",
                draft_text="   ",
                requires_role=SignatoryRole.DEPUTY_DIRECTOR,
                guard=GuardResult(verdict=Verdict.PASS),
                created_at=NOW,
            )

    def test_a_naive_timestamp_is_refused(self) -> None:
        with pytest.raises(ValueError):
            QueueItem(
                order_id="WR-1",
                draft_text=DRAFT,
                requires_role=SignatoryRole.DEPUTY_DIRECTOR,
                guard=GuardResult(verdict=Verdict.PASS),
                created_at=datetime(2026, 6, 10, 9, 0),  # noqa: DTZ001
            )

    def test_a_naive_decision_time_is_refused(self) -> None:
        item = _clean_item()
        with pytest.raises(ApprovalError):
            sign(
                item,
                officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "test-harness"),
                reviewed_digest=item.digest,
                decided_at=datetime(2026, 6, 10, 9, 0),  # noqa: DTZ001
            )


class TestTheOfficerIsNotAuthenticatedHereAndSaysSo:
    """A review's finding, answered honestly rather than papered over.

    A function called `sign` minting a `SignedDecision` from caller-supplied strings
    looks like a trust boundary and is not one. In-process forgery cannot be
    prevented from inside a domain module: whoever can call this can construct its
    inputs. What is preventable is the boundary arriving quietly.
    """

    def test_an_officer_must_record_how_identity_was_established(self) -> None:
        with pytest.raises(ApprovalError, match="authentication method"):
            Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "")

    @pytest.mark.parametrize("placeholder", ["unknown", "none", "N/A", "TODO"])
    def test_a_placeholder_authentication_method_is_refused(self, placeholder: str) -> None:
        """ "unknown" in that field is worse than an empty queue: it is a record that
        looks like evidence and asserts nothing."""
        with pytest.raises(ApprovalError, match="placeholder"):
            Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", placeholder)

    def test_the_decision_carries_the_authentication_method(self) -> None:
        item = _clean_item()
        decision = sign(
            item,
            officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "iap-oidc"),
            reviewed_digest=item.digest,
            decided_at=NOW,
        )
        assert decision.officer.authenticated_via == "iap-oidc"

    def test_no_module_outside_the_boundary_constructs_an_officer(self) -> None:
        """The enforcement available before an auth layer exists.

        Same shape as the run_async guard: it cannot stop forgery, but it fails on
        the commit that adds a caller minting its own officer, which is how the gap
        would actually arrive. Untracked files included, because a guard that cannot
        see the file about to ship is a false green this repo has already paid for.
        """
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()

        sanctioned = {"agents/src/curtail_agents/approval.py"}
        offenders = [
            rel
            for rel in listed
            if rel.endswith(".py")
            and rel not in sanctioned
            and not rel.startswith("agents/tests/")
            and (root / rel).is_file()
            and "Officer(" in (root / rel).read_text(errors="ignore")
        ]
        assert not offenders, (
            f"these construct an Officer outside the authentication boundary: "
            f"{offenders}. It must come from an authenticated session, never from "
            "user-supplied input."
        )


class TestAGuardThatRejectsWithoutSayingWhyCannotBeOverridden:
    """The hole a review found, and it approved a rejected draft on arbitrary text.

    A `GuardResult` can be ESCALATE with only a `reason` and no structured findings.
    The subset check is then vacuously satisfied, because an empty blocking set is a
    subset of anything, so any override text at all walked through.
    """

    @staticmethod
    def _findingless() -> QueueItem:
        return QueueItem(
            order_id="WR 2026-0012-DWR",
            draft_text=DRAFT,
            requires_role=SignatoryRole.DEPUTY_DIRECTOR,
            guard=GuardResult(verdict=Verdict.ESCALATE, reason="rejected, nothing named"),
            created_at=NOW,
        )

    def test_arbitrary_override_text_no_longer_approves_it(self) -> None:
        item = self._findingless()
        assert item.blocking_violations == ()
        with pytest.raises(ApprovalError, match="named no "):
            sign(
                item,
                officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "iap-oidc"),
                reviewed_digest=item.digest,
                decided_at=NOW,
                overriding=("whatever I like",),
            )

    def test_it_fails_closed_even_with_no_override_offered(self) -> None:
        item = self._findingless()
        with pytest.raises(ApprovalError):
            sign(
                item,
                officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "iap-oidc"),
                reviewed_digest=item.digest,
                decided_at=NOW,
            )

    def test_declining_it_is_still_allowed(self) -> None:
        """Refusing to sign a broken draft must never be blocked by the same guard
        that blocks approving it."""
        item = self._findingless()
        decision = sign(
            item,
            officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "iap-oidc"),
            reviewed_digest=item.digest,
            decided_at=NOW,
            approved=False,
        )
        assert not decision.approved

    def test_inventing_a_finding_the_guard_never_made_is_refused(self) -> None:
        """Free text in an override field is how a signature comes to rest on
        something nobody checked."""
        item = _flagged_item()
        with pytest.raises(ApprovalError, match="not findings the guard made"):
            sign(
                item,
                officer=Officer(SignatoryRole.DEPUTY_DIRECTOR, "jchristian-smith", "iap-oidc"),
                reviewed_digest=item.digest,
                decided_at=NOW,
                overriding=(*item.blocking_violations, "and one I made up"),
            )
