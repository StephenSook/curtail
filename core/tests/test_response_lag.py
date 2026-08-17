"""The headline number, and the caveats that must never travel without it.

Offline. `scripts/build_response_lag.py` needs USGS; this asserts the committed record.

**Why this number exists at all.** The backtest reports how often the engine agrees with
the Board, which measures whether the system is RIGHT. A watermaster does not buy
correctness, they buy time, and the playbook rule learned from a previous loss is to
publish the headline in the USER's currency. So this is in days.

**Why the caveats are tested as hard as the number.** A figure that travels without them
will be quoted without them, and the strongest thing this project has is that its numbers
survive being checked. The most important caveat is that this is NOT a measure of
administrative delay: 23 CCR 875(b) directs the Deputy Director to weigh hydrologic and
weather conditions, so part of any gap is judgment, and judgment is what this project says
belongs to a human.
"""

from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "core" / "src" / "curtail_core" / "data" / "response_lag.json"


@pytest.fixture(scope="module")
def record() -> dict[str, Any]:
    loaded = json.loads(DATA.read_text())
    assert isinstance(loaded, dict)
    return loaded


class TestTheArithmeticHolds:
    def test_every_lag_is_its_own_two_dates_apart(self, record: dict[str, Any]) -> None:
        """The one check that cannot be satisfied by a plausible-looking number: each row
        carries both dates, so the lag is recomputable from the row itself."""
        for item in record["scored"]:
            dated = date.fromisoformat(item["document_dated"])
            since = date.fromisoformat(item["river_below_since"])
            assert (dated - since).days == item["lag_days"], item["file"]

    def test_no_lag_is_negative(self, record: dict[str, Any]) -> None:
        """A document dated BEFORE the river crossed would mean the walk-back ran forward,
        and the number would be meaningless in a way nothing else here would notice."""
        assert all(item["lag_days"] >= 0 for item in record["scored"])

    def test_the_summary_matches_the_rows(self, record: dict[str, Any]) -> None:
        lags = [item["lag_days"] for item in record["scored"]]
        counts = record["counts"]
        assert counts["scored"] == len(record["scored"])
        assert counts["median_lag_days"] == int(statistics.median(sorted(lags)))
        assert counts["max_lag_days"] == max(lags)
        assert counts["min_lag_days"] == min(lags)

    def test_the_longest_is_first(self, record: dict[str, Any]) -> None:
        """The response serves `scored[0]` as the longest case, so the ordering is part of
        the contract rather than an incidental property of the builder."""
        lags = [item["lag_days"] for item in record["scored"]]
        assert lags == sorted(lags, reverse=True)


class TestTheTwoKindsOfExclusionStaySeparate:
    """A document nobody can evaluate and a document evaluated and found not to apply are
    completely different statements about the world.

    The first version of the builder conflated them: it caught the flow-schedule refusal
    for every 2021 date and reported "the river was not below its minimum", which is a
    confident finding about a river that nothing here can actually assess. That is the
    errored-query-reads-as-absence defect this repository keeps meeting, and here it was
    pointing straight at the era boundary the flow-minimums module already models.
    """

    def test_the_unevaluable_count_matches_the_reasons(self, record: dict[str, Any]) -> None:
        counted = sum(1 for item in record["excluded"] if "not verified" in item["why"])
        assert record["counts"]["excluded_unevaluable_era"] == counted

    def test_an_unevaluable_document_says_it_is_a_gap_in_knowledge(
        self, record: dict[str, Any]
    ) -> None:
        unevaluable = [i for i in record["excluded"] if "not verified" in i["why"]]
        assert unevaluable, "no era-unverifiable documents, so the corpus or the era changed"
        for item in unevaluable:
            assert "not a finding about the river" in item["why"], (
                f"{item['file']} reports an unevaluable era without saying it is a gap in "
                "what is known, which reads as a finding about the river"
            )

    def test_every_exclusion_names_a_reason(self, record: dict[str, Any]) -> None:
        assert record["counts"]["excluded"] == len(record["excluded"])
        for item in record["excluded"]:
            assert item["why"].strip()

    def test_nothing_is_both_scored_and_excluded(self, record: dict[str, Any]) -> None:
        scored = {item["file"] for item in record["scored"]}
        excluded = {item["file"] for item in record["excluded"]}
        assert not (scored & excluded)


class TestTheCaveatsCannotBeDropped:
    """Guards on the honest half. A figure that travels without these will be quoted
    without them, and the strongest asset this project has is that its numbers survive
    being checked."""

    def test_it_says_what_it_is_not(self, record: dict[str, Any]) -> None:
        text = record["source"]["not_a_claim"]
        assert "875(b)" in text, "the provision that makes this not-delay is not cited"
        assert "judgment" in text
        assert "removes the wait for somebody to notice" in text, (
            "the narrow claim this number DOES support has been dropped, which leaves the "
            "caveat without the thing it is qualifying"
        )

    def test_it_says_why_it_is_conservative(self, record: dict[str, Any]) -> None:
        """Daily mean rather than instantaneous can only UNDERSTATE the time below, and a
        number that could only be larger is a different kind of claim from one that could
        go either way."""
        text = record["source"]["conservative_because"]
        assert "daily mean" in text.lower()
        assert "understate" in text

    def test_it_admits_the_date_is_a_proxy(self, record: dict[str, Any]) -> None:
        text = record["source"]["date_caveat"]
        assert "creation timestamp" in text
        assert "not the adoption date" in text

    def test_only_imposing_actions_are_scored(self, record: dict[str, Any]) -> None:
        """A suspension is the opposite event and its lag would mean something entirely
        different. Mixing them would produce an average of two incomparable things."""
        assert {item["action"] for item in record["scored"]} <= {"impose", "reinstate"}


class TestTheEndpoint:
    @staticmethod
    def client() -> Any:
        from fastapi.testclient import TestClient

        from curtail_agents.api import app

        return TestClient(app)

    def test_the_caveats_ship_in_the_same_response_as_the_number(
        self, record: dict[str, Any]
    ) -> None:
        """Not on a separate page, not in a footnote. In the same object, so a client
        cannot render the figure without having been handed its limits."""
        body = self.client().get("/api/response-lag").json()
        assert body["median_lag_days"] >= 0
        assert "875(b)" in body["what_this_is_not"]
        assert body["why_conservative"].strip()
        assert body["date_caveat"].strip()
        # NOT `>= 0`. That is satisfiable by a hardcoded zero, which is exactly what a
        # mutation of this line did while the test kept passing: a count assertion that
        # cannot see a wrong value in the right shape. Pinned to the record instead.
        assert (
            body["excluded_because_era_unverified"] == record["counts"]["excluded_unevaluable_era"]
        )
        assert body["excluded_because_era_unverified"] > 0, (
            "the corpus holds 2021 documents whose flow schedule is unverified, so a zero "
            "here means the two kinds of exclusion have been collapsed again"
        )

    def test_the_headline_states_the_denominator(self) -> None:
        """A median with no N behind it invites the reader to assume the whole corpus."""
        body = self.client().get("/api/response-lag").json()
        assert str(body["actions_scored"]) in body["headline"]
        assert str(body["median_lag_days"]) in body["headline"]
        assert "verified regulatory era" in body["headline"]

    def test_an_unreadable_record_is_a_503_not_a_zero(self, monkeypatch: Any) -> None:
        """A lag of zero days is a strong claim about the Board's speed. It must never
        stand in for a file that could not be read."""
        from curtail_agents import api

        monkeypatch.setattr(api, "RESPONSE_LAG_PATH", REPO / "does-not-exist.json")
        response = self.client().get("/api/response-lag")
        assert response.status_code == 503
        assert "could not be read" in response.json()["detail"]
