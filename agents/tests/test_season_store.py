"""The Season Ledger's store: what survives, and what says it did not.

The suite runs against the in-memory implementation, because `conftest.py` sets
CURTAIL_DISABLE_FIRESTORE so no test can write to the real seasons collection. What
cannot be proven here is that a real Firestore round trip works, and that is not faked:
`scripts/probe_season_store.py` writes with one client and reads with a SECOND,
independently built one, and records the result in `docs/SEASON.md`. Reading back
through the same object in memory proves nothing about durability.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from curtail_agents.api import app, basin_of_order
from curtail_agents.ledger import LedgerIntegrityError, record_order
from curtail_agents.season_store import (
    InMemorySeasonStore,
    SeasonStore,
    SeasonStoreUnavailableError,
    season_payload,
    store_for,
)
from curtail_core.basins import Basin
from curtail_core.clocks import SignatoryRole

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "docs" / "SEASON.md"

ADOPTED = datetime(2026, 6, 16, 17, 0, tzinfo=UTC)


def _entry(order_id: str = "WR 2024-0006-DWR Addendum 6") -> Any:
    return record_order(
        ADOPTED,
        order_id=order_id,
        order_type="reinstatement",
        signatory=SignatoryRole.DEPUTY_DIRECTOR,
        certification_days=7,
        information_response_days=5,
    )


class TestTheClocksAreTheWholePoint:
    def test_an_adopted_order_opens_the_statutory_windows(self) -> None:
        """Weeks of asynchronous state, as a mechanism rather than an adjective.

        These run in calendar time while nobody is looking, and a watermaster who
        misses the 30-day window has lost the right to challenge the order.
        """
        store: SeasonStore = InMemorySeasonStore()
        store.append_entry(Basin.SHASTA, _entry())
        payload = season_payload(store, Basin.SHASTA, now=datetime(2026, 6, 20, tzinfo=UTC))

        open_now = {c["clock"]: c for c in payload["open_clocks"]}
        assert "reconsideration_petition" in open_now
        assert "board_response" in open_now
        assert open_now["reconsideration_petition"]["closes_at"].startswith("2026-07-16"), (
            "the Water Code 1122 window is 30 days from adoption"
        )
        assert open_now["board_response"]["closes_at"].startswith("2026-09-14"), (
            "the Board's response window is 90 days, and it is in 1122, not 1123"
        )

    def test_the_delegation_exception_is_carried_not_inferred(self) -> None:
        """These orders issue under delegated authority, so reconsideration is a
        mandatory prerequisite to judicial review rather than an optional first step.
        A reader who cannot see that will treat a closed window as a lost month rather
        than as a foreclosed challenge."""
        store: SeasonStore = InMemorySeasonStore()
        store.append_entry(Basin.SHASTA, _entry())
        payload = season_payload(store, Basin.SHASTA, now=datetime(2026, 6, 20, tzinfo=UTC))
        petition = next(
            c for c in payload["open_clocks"] if c["clock"] == "reconsideration_petition"
        )
        assert petition["exhaustion_required"] is True

    def test_a_closed_window_stops_being_reported_as_open(self) -> None:
        """Non-vacuity: if every clock were always open this would prove nothing."""
        store: SeasonStore = InMemorySeasonStore()
        store.append_entry(Basin.SHASTA, _entry())
        later = season_payload(store, Basin.SHASTA, now=datetime(2027, 1, 1, tzinfo=UTC))
        assert later["open_clocks"] == []
        assert len(later["orders"]) == 1, "the order itself must remain on the record"


class TestAnEmptySeasonIsNeverConfusedWithALostOne:
    def test_the_in_memory_store_says_it_is_not_durable(self) -> None:
        store = InMemorySeasonStore("the test said so")
        assert store.durable is False
        assert "the test said so" in store.describe()
        assert "survives" in store.describe()

    def test_every_payload_carries_the_word_durable(self) -> None:
        payload = season_payload(InMemorySeasonStore(), Basin.SCOTT)
        assert payload["durable"] is False
        assert payload["store"], "a payload with no store description hides the caveat"

    def test_the_endpoint_reports_the_store_rather_than_implying_one(self) -> None:
        body = TestClient(app).get("/api/season/shasta").json()
        assert body["durable"] is False, "the suite forces the in-memory store"
        assert "does not survive" in body["store"] or "survives" in body["store"]
        assert body["recommendation_only"] is True

    def test_an_unknown_basin_is_404_not_an_empty_season(self) -> None:
        assert TestClient(app).get("/api/season/klamath").status_code == 404

    def test_a_store_outage_raises_rather_than_returning_nothing(self) -> None:
        """An errored read reported as an empty season is this project's oldest bug
        shape: a failed check becoming a confident absence."""

        class Broken:
            durable = True

            def describe(self) -> str:
                return "broken"

            def load(self, basin: Basin) -> Any:
                raise SeasonStoreUnavailableError("the database is down")

            def append_entry(self, basin: Basin, entry: Any) -> Any:
                raise SeasonStoreUnavailableError("the database is down")

        with pytest.raises(SeasonStoreUnavailableError):
            season_payload(Broken(), Basin.SHASTA)


class TestTheLedgerStaysAppendOnly:
    def test_the_same_order_cannot_be_filed_twice(self) -> None:
        """Every deadline runs from the adoption timestamp, so replacing a record
        could move or extinguish a party's window to challenge the order."""
        store: SeasonStore = InMemorySeasonStore()
        store.append_entry(Basin.SHASTA, _entry())
        with pytest.raises(LedgerIntegrityError, match="already"):
            store.append_entry(Basin.SHASTA, _entry())

    def test_two_basins_keep_separate_seasons(self) -> None:
        store: SeasonStore = InMemorySeasonStore()
        store.append_entry(Basin.SHASTA, _entry("shasta-order"))
        store.append_entry(Basin.SCOTT, _entry("scott-order"))
        shasta = season_payload(store, Basin.SHASTA)["orders"]
        scott = season_payload(store, Basin.SCOTT)["orders"]
        assert [o["order_id"] for o in shasta] == ["shasta-order"]
        assert [o["order_id"] for o in scott] == ["scott-order"]


class TestTheStoreIsChosenExplicitly:
    def test_it_returns_the_in_memory_store_when_firestore_is_disabled(self) -> None:
        """And says which switch did it, so nobody debugs a phantom outage."""
        store = store_for()
        assert store.durable is False
        assert "CURTAIL_DISABLE_FIRESTORE" in store.describe()


class TestAnOrderIsFiledUnderTheRightRiver:
    @pytest.mark.parametrize(
        ("order_id", "expected"),
        [
            ("DRAFT-SHASTA-2026-06-15-pass", Basin.SHASTA),
            ("DRAFT-SCOTT-2026-05-21-pass", Basin.SCOTT),
            ("draft-shasta-2026-06-15-escalate", Basin.SHASTA),
        ],
    )
    def test_the_basin_is_read_off_the_order_id(self, order_id: str, expected: Basin) -> None:
        assert basin_of_order(order_id) == expected

    def test_an_id_naming_no_basin_returns_none_rather_than_a_guess(self) -> None:
        """Filing an order under the wrong river starts a real statutory clock in a
        season it has nothing to do with, and every later reader treats it as that
        basin's record."""
        assert basin_of_order("WR 2024-0006-DWR") is None
        assert basin_of_order("DRAFT-KLAMATH-2026-06-15-pass") is None


class TestTheDurableRoundTripRecordIsHonest:
    """CI cannot reach Firestore, so it reads what the probe recorded."""

    def test_the_record_exists_and_proves_a_second_client_read_it_back(self) -> None:
        assert RECORD.exists(), (
            "docs/SEASON.md is missing. Run `make season` where credentials exist; it "
            "is the only place the durable round trip can actually happen."
        )
        text = RECORD.read_text()
        assert "second" in text.lower(), (
            "the record does not show a SECOND client reading the season back. Reading "
            "through the same object proves nothing about durability."
        )
        assert "firestore" in text.lower()
        assert "reconsideration_petition" in text, (
            "the record shows no statutory clock, so it demonstrates storage rather "
            "than the thing storage is for"
        )


class TestAConfiguredProjectFailsClosed:
    """The defect a review found: `store_for` promised no silent downgrade, and did one.

    Its own docstring said "never a silent downgrade" three lines above a `except
    SeasonStoreUnavailableError: return InMemorySeasonStore(...)`. An operator who sets
    GOOGLE_CLOUD_PROJECT has asked for a durable legal record, and quietly handing them a
    volatile one decides on their behalf that losing the season beats an error. The
    container would start, serve for hours, and lose every clock it recorded.
    """

    def test_an_unreachable_firestore_raises_rather_than_degrading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CURTAIL_DISABLE_FIRESTORE", raising=False)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "a-project-that-is-configured")

        import curtail_agents.season_store as module

        def _explode(project: str, client: Any | None = None) -> Any:
            raise SeasonStoreUnavailableError("the client could not be built")

        monkeypatch.setattr(module, "FirestoreSeasonStore", _explode)
        with pytest.raises(SeasonStoreUnavailableError):
            module.store_for()

    def test_no_project_is_still_an_explicit_in_memory_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other branch, so failing closed did not simply break every path.

        No project configured is the operator saying they do not want a durable store,
        which is different from asking for one and not getting it.
        """
        monkeypatch.delenv("CURTAIL_DISABLE_FIRESTORE", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        store = store_for()
        assert store.durable is False
        assert "GOOGLE_CLOUD_PROJECT" in store.describe()

    def test_the_endpoint_answers_503_rather_than_an_empty_season(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An outage must never render as a river with no orders on it."""
        import curtail_agents.api as api

        monkeypatch.setattr(api, "_SEASON_STORE", None)

        def _explode() -> Any:
            raise SeasonStoreUnavailableError("the store is unreachable")

        monkeypatch.setattr(api, "store_for", _explode)
        response = TestClient(api.app).get("/api/season/shasta")
        assert response.status_code == 503
        assert "unreachable" in response.json()["detail"]

    def test_the_store_is_not_built_at_import(self) -> None:
        """Built lazily on purpose: failing closed AND building at import would mean a
        transient failure during a cold start permanently attaches the container to
        whatever the first attempt produced."""
        import curtail_agents.api as api

        assert hasattr(api, "season_store"), "the lazy accessor is gone"
        assert not hasattr(api, "SEASON_STORE"), (
            "an import-time store is back, so a cold-start failure would outlive the "
            "request that caused it"
        )
