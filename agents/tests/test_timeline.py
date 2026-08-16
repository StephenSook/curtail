"""The timeline, and the two heuristics that were wrong before it existed.

The whole design of this artifact is that **every date says what KIND of date it is**.
These documents do not print an adoption date, and inventing one was tried twice:

- The modal date in the text works on Scott Addendum 12, which names its own date 17
  times, and fails on Shasta Addendum 6, which lists gage readings by date so the mode
  is noise. It returned June 11 where the true date is June 16.
- The latest date in the text fit BOTH anchors and looked principled, since a document
  cannot reference a date after it was written. Measured against the corpus it collapsed:
  73 of 101 documents name a date AFTER their file was created, because an addendum
  states when it expires, and one names the regulation's 2031 sunset.

**Two anchors is not a sample.** A heuristic that fits every anchor you have can still be
wrong about the population, and the only reason that was caught is that it was measured
against all 101 rather than against the two it was built on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
ARTIFACT = REPO / "core" / "src" / "curtail_core" / "data" / "timeline.json"


@pytest.fixture(scope="module")
def artifact() -> dict[str, Any]:
    loaded = json.loads(ARTIFACT.read_text())
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture(scope="module")
def client() -> TestClient:
    from curtail_agents.api import app

    return TestClient(app)


class TestNoDateIsClaimedWithoutASource:
    def test_only_the_two_anchored_documents_claim_an_adoption_date(
        self, artifact: dict[str, Any]
    ) -> None:
        """An adoption date is a legal fact about an order. Two of 101 have one that
        this system can establish independently, and no other entry may assert one."""
        claimed = [e["file"] for e in artifact["entries"] if e["adoption_date"]]
        assert sorted(claimed) == sorted(artifact["source"]["anchors"]), (
            f"{len(claimed)} documents claim an adoption date and only "
            f"{len(artifact['source']['anchors'])} have a source for one"
        )

    def test_the_anchors_match_the_rights_loader(self, artifact: dict[str, Any]) -> None:
        """Established independently, from the same documents, by different code."""
        from curtail_core.basins import Basin
        from curtail_core.rights import rights_for

        expected = {
            "scott_2024__addenda__12.pdf": rights_for(Basin.SCOTT).issued.isoformat(),
            "shasta_2024__addenda__6.pdf": rights_for(Basin.SHASTA).issued.isoformat(),
        }
        by_file = {e["file"]: e for e in artifact["entries"]}
        for file, issued in expected.items():
            assert by_file[file]["adoption_date"] == issued

    def test_no_entry_carries_a_bare_date_field(self, artifact: dict[str, Any]) -> None:
        """A field called `date` is how an inference gets promoted into a fact by a
        later edit. Every date here is named: file_date, adoption_date, or an evidence
        field that nothing plots."""
        for entry in artifact["entries"]:
            assert "date" not in entry, f"{entry['file']} carries an unqualified date"

    def test_the_evidence_fields_are_carried_but_never_plotted(
        self, artifact: dict[str, Any]
    ) -> None:
        """Both failed heuristics are kept so a reader can see what was tried and why
        it was rejected. Dropping them would hide the reasoning; promoting them would
        repeat the mistake."""
        entry = next(e for e in artifact["entries"] if e["file"] == "shasta_2024__addenda__6.pdf")
        assert entry["modal_text_date"] == "2026-06-11", (
            "the modal heuristic no longer reproduces the wrong answer it gave, so the "
            "record of why it was rejected has drifted"
        )
        assert entry["adoption_date"] == "2026-06-16"
        assert entry["modal_text_date"] != entry["adoption_date"]


class TestTheOffsetIsMeasuredNotAssumed:
    def test_the_gap_between_file_and_adoption_is_reported(self, artifact: dict[str, Any]) -> None:
        """The timeline plots the file date. How far that sits from the legal date is
        knowable on exactly two documents, so it is measured on those two and stated,
        rather than assumed to hold for the other 99."""
        offsets = artifact["source"]["measured_offsets"]
        assert len(offsets) == 2
        for offset in offsets:
            assert 0 <= offset["file_days_after_adoption"] <= 7, (
                f"{offset['file']} was created "
                f"{offset['file_days_after_adoption']} days after adoption, which is "
                "far enough that the file date is no longer a reasonable proxy"
            )

    def test_the_method_says_what_is_plotted(self, artifact: dict[str, Any]) -> None:
        method = artifact["source"]["method"]
        assert "do not print an adoption date" in method
        assert "not the date an order was adopted" in method


class TestTheCountsReconcile:
    def test_gaps_account_for_every_unplaceable_document(self, artifact: dict[str, Any]) -> None:
        placeable = sum(1 for e in artifact["entries"] if e["file_date"])
        assert len(artifact["gaps"]) == len(artifact["entries"]) - placeable

    def test_the_corpus_is_the_size_the_manifest_says(self, artifact: dict[str, Any]) -> None:
        assert artifact["counts"]["documents"] == len(artifact["entries"]) == 101

    def test_the_actions_come_from_the_manifest_not_from_here(
        self, artifact: dict[str, Any]
    ) -> None:
        """Suspensions outnumber impositions roughly five to one across five years,
        which is the shape of the administration this project describes."""
        actions = artifact["counts"]["by_action"]
        assert actions["suspend"] > actions["impose"]
        assert sum(actions.values()) == len(artifact["entries"])


class TestTheEndpoint:
    def test_it_serves_only_placeable_events(self, client: TestClient) -> None:
        body = client.get("/api/timeline").json()
        assert all(event["file_date"] for event in body["events"])
        assert len(body["events"]) == body["counts"]["with_file_date"]

    def test_it_carries_the_method_and_the_gaps(self, client: TestClient) -> None:
        """A reader who sees only the dots learns the wrong thing."""
        body = client.get("/api/timeline").json()
        assert "do not print an adoption date" in body["method"]
        assert body["gaps"]

    def test_a_missing_artifact_is_503_naming_the_generator(self, monkeypatch: Any) -> None:
        import pathlib

        from curtail_agents import api

        # Patched on `pathlib` itself rather than reaching through the api module's
        # re-export. mypy is right that `api.Path` is something the module never
        # promised, and a test that reaches for an unexported name is one refactor
        # away from silently testing nothing.
        monkeypatch.setattr(api, "_TIMELINE", None)
        monkeypatch.setattr(pathlib.Path, "is_file", lambda self: False)
        with TestClient(api.app) as client:
            response = client.get("/api/timeline")
        assert response.status_code == 503
        assert "build_timeline" in response.json()["detail"]
