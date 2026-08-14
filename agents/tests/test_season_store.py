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
from typing import Any, ClassVar

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


class TestTheDurabilityContractHoldsOnEveryPath:
    """A contract kept on the success path and dropped on failure is not a contract.

    The README and the store's module docstring both promise every response says whether
    its record is durable and names its store. Three failure branches omitted `durable`
    entirely and a fourth reported it as `False`, which is worse than omitting it: it
    asserts a volatile store WAS used when nothing was written at all.
    """

    #: Every branch of the `season_ledger` block must answer all of these.
    REQUIRED: ClassVar[tuple[str, ...]] = (
        "recorded",
        "durable",
        "store",
        "reason",
        "orders_on_record",
        "clocks_started",
    )

    def test_the_not_approved_path_still_reports_a_store(self) -> None:
        from curtail_agents.api import _season_ledger_block

        block = _season_ledger_block(recorded=False, store=None, reason="not approved")
        assert set(block) == set(self.REQUIRED)
        assert block["durable"] is None, "unknown must not be reported as False"
        assert block["store"] == "not consulted"

    def test_an_unreachable_store_is_unknown_not_volatile(self) -> None:
        """`False` would mean the clocks went to a volatile store. They went nowhere.

        The same conflation that put fourteen undated rights in the first grouping
        curtailed, one layer up: unknown fed in as a definite value.
        """
        from curtail_agents.api import _season_ledger_block

        block = _season_ledger_block(recorded=False, store=None, reason="unreachable")
        assert block["durable"] is not False
        assert block["durable"] is None

    def test_the_success_path_reports_the_real_store(self) -> None:
        from curtail_agents.api import _season_ledger_block

        store = InMemorySeasonStore("a test")
        block = _season_ledger_block(
            recorded=True, store=store, orders_on_record=1, clocks_started=["a"]
        )
        assert set(block) == set(self.REQUIRED)
        assert block["durable"] is False, "an in-memory store is genuinely not durable"
        assert "a test" in block["store"]

    def test_every_branch_answers_the_same_questions(self) -> None:
        """Key-set equality across branches, so no path can quietly answer fewer."""
        from curtail_agents.api import _season_ledger_block

        branches = [
            _season_ledger_block(recorded=False, store=None, reason="not approved"),
            _season_ledger_block(recorded=False, store=None, reason="unreachable"),
            _season_ledger_block(
                recorded=False, store=InMemorySeasonStore(), reason="ledger refused"
            ),
            _season_ledger_block(recorded=True, store=InMemorySeasonStore(), orders_on_record=1),
        ]
        shapes = {frozenset(b) for b in branches}
        assert len(shapes) == 1, f"branches answer different questions: {shapes}"
        assert all(set(b) == set(self.REQUIRED) for b in branches)


class TestNoScriptPicksAProjectForYou:
    """A silent default project is an ambient default, and this account has been bitten.

    `make chaos-recording` substituted `curtail-505118` when GOOGLE_CLOUD_PROJECT was
    unset, which contradicted the target's own contract: a step whose purpose is "set
    the project before recording" cannot set it for you. The season probe did the same
    thing while WRITING a legal record to a real Firestore collection.

    On any machine but the author's, both would aim authenticated live requests at a
    project the operator never chose.
    """

    REPO_ROOT: ClassVar[Path] = Path(__file__).resolve().parents[2]

    def test_no_shell_or_script_substitutes_a_project_when_none_is_set(self) -> None:
        """Catches the PATTERN, not the one instance, in the files that can act."""
        offenders: list[str] = []
        for relative in ("Makefile", "scripts/probe_season_store.py"):
            body = (self.REPO_ROOT / relative).read_text()
            for pattern in (
                "${GOOGLE_CLOUD_PROJECT:-",
                '"GOOGLE_CLOUD_PROJECT") or "',
                "'GOOGLE_CLOUD_PROJECT') or '",
            ):
                if pattern in body:
                    offenders.append(f"{relative}: {pattern}")
        assert not offenders, (
            f"{offenders} substitute a project id when the operator set none. Refuse "
            "instead: a recorded drill must call the project you meant, and a probe "
            "that writes a legal record must write it to the database you meant."
        )

    def test_the_probe_refuses_rather_than_guessing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exercised, not just grepped, because a pattern check alone is a shape test."""
        import runpy
        import sys

        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.setattr(sys, "argv", ["probe_season_store.py"])
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(
                str(self.REPO_ROOT / "scripts" / "probe_season_store.py"),
                run_name="__main__",
            )
        assert exit_info.value.code == 1


class _FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class _FakeDocument:
    def __init__(self, store: dict[str, Any], key: str) -> None:
        self._store = store
        self._key = key
        self.raises: Exception | None = None

    def get(self, transaction: Any = None) -> _FakeSnapshot:
        if self.raises is not None:
            raise self.raises
        return _FakeSnapshot(self._store.get(self._key))


class _FakeCollection:
    def __init__(self, store: dict[str, Any], docs: dict[str, _FakeDocument]) -> None:
        self._store = store
        self._docs = docs

    def document(self, key: str) -> _FakeDocument:
        return self._docs.setdefault(key, _FakeDocument(self._store, key))


class _FakeTransaction:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def set(self, document: _FakeDocument, payload: dict[str, Any]) -> None:
        self._store[document._key] = payload


class _FakeClient:
    """Just enough Firestore to exercise OUR logic, and none of theirs."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.docs: dict[str, _FakeDocument] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self.store, self.docs)

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self.store)


class TestTheFirestoreStoreItself:
    """The durable implementation's own logic, which the probe cannot cover in CI.

    The probe proves a real round trip on a machine with credentials. It runs nowhere
    else, so every branch here was unexercised: an absent document, a client that
    raises, the transaction body that merges and refuses a duplicate. Those are the
    paths that decide whether a lost season is reported as lost.
    """

    @pytest.fixture
    def store(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        import google.cloud.firestore as firestore

        from curtail_agents.season_store import FirestoreSeasonStore

        # Their decorator expects a real Transaction. We are testing our body, so it is
        # replaced by a passthrough that calls the function with whatever we hand it.
        monkeypatch.setattr(
            firestore, "transactional", lambda fn: lambda txn: fn(txn), raising=False
        )
        return FirestoreSeasonStore("a-project", client=_FakeClient())

    def test_an_absent_document_is_an_empty_season_not_an_error(self, store: Any) -> None:
        """A basin nothing has happened in yet is genuinely empty, and that is the ONE
        case where empty is the right answer."""
        loaded = store.load(Basin.SHASTA)
        assert loaded.entries == ()

    def test_a_client_error_is_reported_rather_than_read_as_empty(self, store: Any) -> None:
        """The distinction the whole module exists for."""
        store._document(Basin.SHASTA).raises = RuntimeError("the database is unreachable")
        with pytest.raises(SeasonStoreUnavailableError, match="could not be read"):
            store.load(Basin.SHASTA)

    def test_an_entry_written_is_an_entry_read_back(self, store: Any) -> None:
        store.append_entry(Basin.SHASTA, _entry("WR-1"))
        assert [e.order_id for e in store.load(Basin.SHASTA).entries] == ["WR-1"]

    def test_a_second_entry_appends_rather_than_replacing(self, store: Any) -> None:
        """Append-only, through the transaction body rather than in memory."""
        store.append_entry(Basin.SHASTA, _entry("WR-1"))
        store.append_entry(Basin.SHASTA, _entry("WR-2"))
        assert sorted(e.order_id for e in store.load(Basin.SHASTA).entries) == ["WR-1", "WR-2"]

    def test_the_transaction_body_refuses_a_duplicate(self, store: Any) -> None:
        """Firestore retries a transaction body on contention, so it must be safe to run
        twice. `ledger.append` refusing an existing id is what makes that true."""
        store.append_entry(Basin.SHASTA, _entry("WR-1"))
        with pytest.raises(LedgerIntegrityError):
            store.append_entry(Basin.SHASTA, _entry("WR-1"))

    def test_a_write_failure_is_reported_with_what_was_lost(self, store: Any) -> None:
        store._document(Basin.SHASTA).raises = RuntimeError("write rejected")
        with pytest.raises(SeasonStoreUnavailableError, match="were NOT recorded"):
            store.append_entry(Basin.SHASTA, _entry("WR-1"))

    def test_it_describes_itself_as_durable_and_names_where(self, store: Any) -> None:
        assert store.durable is True
        assert "a-project" in store.describe()
        assert "seasons" in store.describe()

    def test_both_implementations_refuse_a_duplicate_the_same_way(self, store: Any) -> None:
        """The protocol has two implementations and they must not diverge.

        The durable one wrapped `LedgerIntegrityError` into an outage, so a duplicate
        filing reported "the database could not be written" in production while
        reporting "the ledger refused the entry" in every test. The API distinguishes
        those in its response, so the operator would have been told the wrong thing at
        the only moment it matters.
        """
        memory: SeasonStore = InMemorySeasonStore()
        for implementation in (memory, store):
            implementation.append_entry(Basin.SCOTT, _entry("SAME-ID"))
            with pytest.raises(LedgerIntegrityError):
                implementation.append_entry(Basin.SCOTT, _entry("SAME-ID"))

    def test_corrupt_stored_data_is_an_outage_not_a_refusal(
        self, store: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ValueError from the stored season is infrastructure, not a domain answer.

        The first version of the domain-refusal fix re-raised `ValueError` as well,
        which reads reasonable and is an overcorrection: a season that will not parse,
        or an SDK that objects to an argument, would have escaped as a ValueError and
        been classified by the API as "the ledger refused the entry". A corrupt record
        reported as a routine refusal is the quietest possible way to lose a season.

        Exactly one exception means "this order is already on record".
        """
        import curtail_agents.season_store as module

        def _corrupt(entries: Any, new: Any) -> Any:
            raise ValueError("the stored season could not be parsed")

        monkeypatch.setattr(module, "append", _corrupt)
        with pytest.raises(SeasonStoreUnavailableError, match="could not be written"):
            store.append_entry(Basin.SHASTA, _entry("WR-1"))

    def test_the_duplicate_refusal_still_gets_through(self, store: Any) -> None:
        """The other side of the same narrowing, so it cannot swing back."""
        store.append_entry(Basin.SHASTA, _entry("WR-1"))
        with pytest.raises(LedgerIntegrityError):
            store.append_entry(Basin.SHASTA, _entry("WR-1"))
