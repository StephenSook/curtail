"""The comparison that carries the headline claim, and its exclusions.

Every case carries the Board's OWN SENTENCE from its own document. That is what makes
the claim checkable rather than asserted: a reader can open the PDF and find the quote.
A comparison without the source text is a scoreboard.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    from curtail_agents.api import app

    return TestClient(app)


@pytest.fixture(scope="module")
def report(client: TestClient) -> dict[str, Any]:
    body = client.get("/api/backtest").json()
    assert isinstance(body, dict)
    return body


class TestEveryCaseIsCheckable:
    def test_each_case_carries_a_verbatim_quote(self, report: dict[str, Any]) -> None:
        """Without the Board's own words the row is an assertion. With them it is an
        invitation to check."""
        for case in report["cases"]:
            assert case["source_quote"], f"{case['case_id']} has no source quote"
            assert len(case["source_quote"]) > 20

    def test_each_case_names_both_directions(self, report: dict[str, Any]) -> None:
        for case in report["cases"]:
            assert case["board_direction"] in {"restrict", "relieve"}
            assert case["engine_direction"] in {"restrict", "relieve"}

    def test_the_outcome_follows_from_the_two_directions(self, report: dict[str, Any]) -> None:
        """The outcome must be derived, not asserted. A case labelled match whose
        directions differ would be a scoreboard lying about its own rows."""
        for case in report["cases"]:
            agree = case["board_direction"] == case["engine_direction"]
            assert (case["outcome"] == "match") == agree, (
                f"{case['case_id']} says {case['outcome']} with "
                f"{case['board_direction']} against {case['engine_direction']}"
            )

    def test_a_reading_without_a_minimum_stays_null(self, report: dict[str, Any]) -> None:
        """`float(None)` would either raise or get "fixed" into a zero, and a zero
        minimum on this surface reads as a river with no protection rather than a rule
        the document did not state.

        **Defensive, and honestly labelled as such: no current case has a null
        minimum**, so nothing here exercises that branch and a mutation replacing the
        null guard with `float(x or 0)` kills no test. The guard stays because the
        field is typed optional and a future case will hit it, but claiming this is
        covered would be the kind of vacuous assertion this suite has already produced
        three times.
        """
        for case in report["cases"]:
            for field in ("reading_cfs", "minimum_cfs"):
                assert case[field] is None or isinstance(case[field], float)


class TestTheExclusionsArePublished:
    def test_every_exclusion_states_its_reason(self, report: dict[str, Any]) -> None:
        """A metric that quietly drops its awkward cases is a metric about the easy
        ones. These are the most interesting rows on the card."""
        assert report["exclusions"], "the exclusions have disappeared"
        for exclusion in report["exclusions"]:
            assert exclusion["id"] and exclusion["reason"]
            assert len(exclusion["reason"]) > 30

    def test_the_bound_versus_measurement_exclusion_survives(self, report: dict[str, Any]) -> None:
        """The clearest one: the document says flows "have been at or above" a figure,
        which is a bound and not a reading. Scoring it would invent precision the
        Board never claimed."""
        reasons = " ".join(e["reason"] for e in report["exclusions"]).lower()
        assert "bound" in reasons and "reading" in reasons

    def test_the_headline_counts_the_exclusions_out_loud(self, report: dict[str, Any]) -> None:
        """A denominator that hides its exclusions is the oldest trick in metrics."""
        assert f"{report['excluded']} excluded" in report["headline"]
        assert str(report["scored"]) in report["headline"]


class TestTheClaimStaysNarrow:
    def test_the_headline_is_about_direction_and_nothing_more(self, report: dict[str, Any]) -> None:
        """The locked wording. This reproduces the DIRECTION of a decision. It does
        not derive a cutoff date, and saying so would be a much larger claim than the
        evidence supports."""
        headline = report["headline"].lower()
        assert "direction" in headline
        for overclaim in ("cutoff date", "derives the cutoff", "predicts"):
            assert overclaim not in headline

    def test_the_response_says_it_is_a_recommendation(self, report: dict[str, Any]) -> None:
        assert "self-execute" in report["disclaimer"]
