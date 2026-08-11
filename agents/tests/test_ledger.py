"""The Season Ledger, and the one test that makes the claim real.

"Maintains context across weeks of asynchronous operations" is satisfied by an
in-memory dict in every unit test ever written about it, and lost the moment a Cloud
Run instance recycles. So the load-bearing test here builds a SECOND session service
against the same database file and reads the record back through it, which is what a
restart actually looks like.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from curtail_agents.ledger import (
    LEDGER_STATE_KEY,
    LcsLifecycle,
    LedgerEntry,
    LedgerIntegrityError,
    append,
    drift_for,
    entry_from_dict,
    entry_to_dict,
    from_state,
    open_clocks,
    record_order,
    to_state,
    with_final_action,
)
from curtail_core.clocks import ClockType, SignatoryRole

ADOPTED = datetime(2026, 6, 5, 17, 30, tzinfo=UTC)


def _order(order_id: str = "WR 2026-0005-DWR") -> LedgerEntry:
    return record_order(
        ADOPTED,
        order_id=order_id,
        order_type="initial_order",
        signatory=SignatoryRole.DEPUTY_DIRECTOR,
        certification_days=7,
    )


class TestAnAdoptedOrderStartsItsStatutoryClocks:
    def test_the_reconsideration_window_is_thirty_days(self) -> None:
        entry = _order()
        petition = next(
            c for c in entry.clocks if c.clock_type is ClockType.RECONSIDERATION_PETITION
        )
        assert (petition.closes_at - ADOPTED).days == 30

    def test_a_delegated_order_requires_exhaustion(self) -> None:
        """These orders issue under Board Resolution No. 2012-0061, which puts them
        inside the Water Code 1126(b) delegation exception, so reconsideration is a
        prerequisite to judicial review rather than an optional first step."""
        assert _order().exhaustion_required

    def test_the_entry_answers_for_itself_rather_than_recomputing(self) -> None:
        """`exhaustion_required` reads the stored clocks. A record that had to
        re-derive its own legal posture from today's rules would change its answer
        when the rules changed."""
        entry = _order()
        assert entry.exhaustion_required == any(c.exhaustion_required for c in entry.clocks)


class TestTheLedgerIsAppendOnly:
    def test_recording_the_same_order_twice_is_refused(self) -> None:
        """The adoption timestamp is what every deadline is measured from, so a
        silent overwrite could move or extinguish a party's window to challenge."""
        entries = append((), _order())
        with pytest.raises(LedgerIntegrityError):
            append(entries, _order())

    def test_a_superseding_order_goes_in_under_its_own_id(self) -> None:
        entries = append(append((), _order()), _order("WR 2026-0006-DWR"))
        assert [e.order_id for e in entries] == ["WR 2026-0005-DWR", "WR 2026-0006-DWR"]

    def test_a_naive_adoption_timestamp_is_refused(self) -> None:
        """Ambiguous by up to a day, which is a third of the reconsideration window."""
        with pytest.raises(ValueError):
            record_order(
                datetime(2026, 6, 5, 17, 30),  # noqa: DTZ001
                order_id="x",
                order_type="initial_order",
                signatory=SignatoryRole.DEPUTY_DIRECTOR,
            )


class TestItSurvivesTheTripThroughSessionState:
    def test_an_entry_round_trips_without_losing_a_field(self) -> None:
        entry = _order()
        assert entry_from_dict(entry_to_dict(entry)) == entry

    def test_the_whole_ledger_round_trips(self) -> None:
        entries = append(append((), _order()), _order("WR 2026-0006-DWR"))
        assert from_state(to_state(entries)).entries == entries

    def test_an_absent_ledger_reads_as_empty_rather_than_failing(self) -> None:
        """A season that has recorded nothing yet is a valid state."""
        assert from_state(None).entries == ()
        assert from_state(None).is_complete

    def test_a_json_encoded_ledger_is_accepted(self) -> None:
        """Some session services round-trip values as JSON text."""
        import json

        entries = append((), _order())
        assert from_state(json.dumps(to_state(entries))).entries == entries

    def test_a_corrupt_ledger_raises_rather_than_reading_as_empty(self) -> None:
        """The most dangerous wrong answer this system can give is "no open
        deadlines" for an order whose reconsideration window is running."""
        with pytest.raises(LedgerIntegrityError):
            from_state({"not": "a list"})


class TestOpenClocksAcrossTheSeason:
    def test_every_clock_is_open_the_day_after_adoption(self) -> None:
        entries = append((), _order())
        running = open_clocks(entries, ADOPTED + timedelta(days=1))
        assert {c.clock_type for _, c in running} == {c.clock_type for c in entries[0].clocks}

    def test_the_petition_window_has_closed_by_day_thirty_one(self) -> None:
        entries = append((), _order())
        running = open_clocks(entries, ADOPTED + timedelta(days=31))
        assert ClockType.RECONSIDERATION_PETITION not in {c.clock_type for _, c in running}

    def test_each_open_clock_names_its_order(self) -> None:
        """A season carries many orders at once, and a deadline with no order id is
        a deadline nobody can act on."""
        entries = append(append((), _order()), _order("WR 2026-0006-DWR"))
        ids = {order_id for order_id, _ in open_clocks(entries, ADOPTED + timedelta(days=1))}
        assert ids == {"WR 2026-0005-DWR", "WR 2026-0006-DWR"}

    def test_a_naive_moment_is_refused(self) -> None:
        with pytest.raises(ValueError):
            open_clocks(append((), _order()), datetime(2026, 6, 6))  # noqa: DTZ001


class TestDriftIsReportedNotResolved:
    def test_an_untampered_entry_shows_no_drift(self) -> None:
        assert drift_for(_order()) == ()

    def test_a_stored_deadline_that_no_longer_matches_is_surfaced(self) -> None:
        """The scenario this exists for: the statute changes, and a historical
        order keeps the deadline the law actually set for it while the discrepancy
        becomes visible to a human instead of being quietly resolved."""
        import dataclasses

        entry = _order()
        petition = next(
            c for c in entry.clocks if c.clock_type is ClockType.RECONSIDERATION_PETITION
        )
        moved = dataclasses.replace(petition, closes_at=petition.closes_at + timedelta(days=15))
        tampered = dataclasses.replace(
            entry,
            clocks=tuple(
                moved if c.clock_type is ClockType.RECONSIDERATION_PETITION else c
                for c in entry.clocks
            ),
        )
        differences = drift_for(tampered)
        assert differences
        assert "reconsideration_petition" in differences[0]

    def test_a_missing_clock_is_surfaced_too(self) -> None:
        """Drift in the other direction: today's rules compute a clock the record
        does not carry, which is how a deadline goes unwatched."""
        import dataclasses

        entry = _order()
        stripped = dataclasses.replace(
            entry,
            clocks=tuple(c for c in entry.clocks if c.clock_type is not ClockType.BOARD_RESPONSE),
        )
        assert any("board_response" in d for d in drift_for(stripped))

    def test_drift_does_not_alter_the_record(self) -> None:
        """Reporting, not resolving. A checker that fixed what it found would be
        rewriting the legal record from a function nobody thinks of as a writer."""
        entry = _order()
        before = entry_to_dict(entry)
        drift_for(entry)
        assert entry_to_dict(entry) == before


class TestTheLedgerOutlivesTheProcess:
    """The load-bearing test, and the reason sqlalchemy is now a dependency.

    Everything above passes against an in-memory dict, which is exactly the problem:
    "weeks of asynchronous operations" is a claim about what survives a restart, and
    a unit test that never restarts anything cannot tell the two apart. This builds a
    SECOND `DatabaseSessionService` against the same file, which is what a recycled
    Cloud Run instance looks like from the database's point of view.

    `DatabaseSessionService` is the same class production uses against Cloud SQL, so
    what runs here on SQLite is the production code path with a different URL.
    """

    async def test_a_recorded_order_is_readable_by_a_new_service_instance(
        self, tmp_path: Path
    ) -> None:
        from google.adk.sessions import DatabaseSessionService

        db_url = f"sqlite+aiosqlite:///{tmp_path}/season.db"
        entries = append((), _order())

        writer = DatabaseSessionService(db_url=db_url)
        await writer.create_session(
            app_name="curtail",
            user_id="watermaster",
            session_id="season-2026",
            state={LEDGER_STATE_KEY: to_state(entries)},
        )
        del writer  # the process that wrote the record is gone

        reader = DatabaseSessionService(db_url=db_url)
        session = await reader.get_session(
            app_name="curtail", user_id="watermaster", session_id="season-2026"
        )
        assert session is not None
        loaded = from_state(session.state.get(LEDGER_STATE_KEY))
        assert loaded.is_complete, loaded.summary()
        recovered = loaded.entries

        assert recovered == entries
        assert recovered[0].adopted_at == ADOPTED
        assert recovered[0].exhaustion_required

    async def test_the_clocks_still_run_after_the_restart(self, tmp_path: Path) -> None:
        """Recovering the bytes is not the point. The point is that a deadline
        recorded six weeks ago is still counted correctly by a process that was not
        running when it started."""
        from google.adk.sessions import DatabaseSessionService

        db_url = f"sqlite+aiosqlite:///{tmp_path}/season.db"
        writer = DatabaseSessionService(db_url=db_url)
        await writer.create_session(
            app_name="curtail",
            user_id="watermaster",
            session_id="season-2026",
            state={LEDGER_STATE_KEY: to_state(append((), _order()))},
        )
        del writer

        reader = DatabaseSessionService(db_url=db_url)
        session = await reader.get_session(
            app_name="curtail", user_id="watermaster", session_id="season-2026"
        )
        assert session is not None
        loaded = from_state(session.state.get(LEDGER_STATE_KEY))
        assert loaded.is_complete, loaded.summary()
        recovered = loaded.entries

        # Day 20: the petition window is still open. Day 40: it has closed and the
        # Board's 90-day response window is still running.
        day_20 = {c.clock_type for _, c in open_clocks(recovered, ADOPTED + timedelta(days=20))}
        day_40 = {c.clock_type for _, c in open_clocks(recovered, ADOPTED + timedelta(days=40))}
        assert ClockType.RECONSIDERATION_PETITION in day_20
        assert ClockType.RECONSIDERATION_PETITION not in day_40
        assert ClockType.BOARD_RESPONSE in day_40


class TestLcsIsALifecycleNotABoolean:
    def test_an_lcs_state_survives_the_round_trip(self) -> None:
        entry = record_order(
            ADOPTED,
            order_id="WR 2026-0005-DWR",
            order_type="initial_order",
            signatory=SignatoryRole.DEPUTY_DIRECTOR,
            lcs_state=LcsLifecycle.CONDITIONED,
        )
        assert entry_from_dict(entry_to_dict(entry)).lcs_state is LcsLifecycle.CONDITIONED

    def test_a_rescinded_solution_is_expressible(self) -> None:
        """The state a boolean cannot hold. An approved solution later rescinded
        leaves the right curtailable again, and a flag that only says "exempt" or
        "not exempt" loses which of those two histories produced it."""
        assert LcsLifecycle.RESCINDED.value == "rescinded"
        assert {s.value for s in LcsLifecycle} >= {"approved", "rescinded", "objected"}


class TestFinalActionOpensTheWritWindow:
    """The correction a review forced, expressed as behaviour.

    A delegated order carries no judicial-review clock at adoption, because the
    event that starts it has not happened. Recording final action is what opens it.
    """

    def test_a_fresh_delegated_entry_has_no_writ_clock(self) -> None:
        assert ClockType.JUDICIAL_REVIEW not in {c.clock_type for c in _order().clocks}

    def test_recording_final_action_opens_it(self) -> None:
        final_action = ADOPTED + timedelta(days=88)
        acted = with_final_action(_order(), final_action)
        writ = next(c for c in acted.clocks if c.clock_type is ClockType.JUDICIAL_REVIEW)
        assert writ.opens_at == final_action
        assert acted.final_action_at == final_action

    def test_the_window_is_open_long_after_adoption_plus_thirty(self) -> None:
        """The whole point. Under the old model this deadline had expired 58 days
        before the Board even acted."""
        acted = with_final_action(_order(), ADOPTED + timedelta(days=88))
        running = {c.clock_type for _, c in open_clocks((acted,), ADOPTED + timedelta(days=100))}
        assert ClockType.JUDICIAL_REVIEW in running

    def test_moving_a_recorded_final_action_is_refused(self) -> None:
        acted = with_final_action(_order(), ADOPTED + timedelta(days=88))
        with pytest.raises(LedgerIntegrityError):
            with_final_action(acted, ADOPTED + timedelta(days=95))

    def test_final_action_before_adoption_is_refused(self) -> None:
        """The Board cannot finally act on an order before adopting it, so one of
        the two timestamps is wrong and neither should be trusted silently."""
        with pytest.raises(LedgerIntegrityError):
            with_final_action(_order(), ADOPTED - timedelta(days=1))

    def test_a_naive_final_action_is_refused(self) -> None:
        with pytest.raises(ValueError):
            with_final_action(_order(), datetime(2026, 9, 1, 12, 0))  # noqa: DTZ001

    def test_an_acted_entry_still_round_trips_and_shows_no_drift(self) -> None:
        """The writ clock is stored, so drift must recompute it from the recorded
        final action rather than reporting the module's own correct behaviour."""
        acted = with_final_action(_order(), ADOPTED + timedelta(days=88))
        assert entry_from_dict(entry_to_dict(acted)) == acted
        assert drift_for(acted) == ()

    def test_a_board_order_refuses_a_separate_final_action(self) -> None:
        """The duplicate a review caught. A non-delegated order is its own final
        action, so its writ window opened at adoption and recording a later one
        produced TWO windows opening ten days apart: not a question of which is
        right, a record that contradicts itself."""
        board = record_order(
            ADOPTED,
            order_id="WR 2026-BOARD-1",
            order_type="initial_order",
            signatory=SignatoryRole.BOARD,
        )
        with pytest.raises(LedgerIntegrityError) as caught:
            with_final_action(board, ADOPTED + timedelta(days=10))
        # The message, not just the type. The one-clock-per-type invariant would
        # also refuse this, so asserting only the exception let a mutation removing
        # THIS check pass: the test could not tell which guard had fired. The
        # domain-specific wording is the whole reason this check exists on top of
        # the structural one, and a reader who sees "two judicial_review clocks"
        # learns less than one who is told a Board order is its own final action.
        assert "its own" in str(caught.value)
        assert "final action" in str(caught.value)

    def test_an_entry_can_never_carry_two_clocks_of_one_type(self) -> None:
        """Enforced on the TYPE, not at the one call site that got it wrong, so a
        path nobody has written yet cannot reintroduce it."""
        import dataclasses

        entry = _order()
        with pytest.raises(LedgerIntegrityError):
            dataclasses.replace(entry, clocks=(*entry.clocks, entry.clocks[0]))


class TestOneBadRecordDoesNotHideTheRestOfTheSeason:
    """A review's second half, and it is real independently of the first.

    Enforcing the duplicate-clock invariant in the constructor meant an eager read
    would abort on the first record that violated it, taking every sound order in
    the same season down with it. A watermaster would see zero deadlines instead of
    the nineteen that were fine, which is the same harm as reading the ledger empty.

    On legacy data specifically there is none: this ledger has never been wired to
    an adoption path or deployed, so nothing stored can predate the invariant. The
    quarantine is the recovery path that generalises to any cause anyway.
    """

    @staticmethod
    def _stored_with_one_damaged() -> list[dict[str, object]]:
        good = to_state(append(append((), _order("WR-GOOD-1")), _order("WR-GOOD-2")))
        damaged = entry_to_dict(_order("WR-LEGACY-DUP"))
        # The exact shape the old `with_final_action` could write: two writ clocks.
        writ = dict(damaged["clocks"][0])
        writ["clock_type"] = "judicial_review"
        damaged["clocks"] = [*damaged["clocks"], writ, writ]
        return [*good, damaged]

    def test_the_sound_orders_still_load(self) -> None:
        loaded = from_state(self._stored_with_one_damaged())
        assert [e.order_id for e in loaded.entries] == ["WR-GOOD-1", "WR-GOOD-2"]

    def test_the_damaged_one_is_quarantined_rather_than_dropped(self) -> None:
        """Silently skipping it would leave its deadlines unwatched with nothing to
        show a human, which is worse than refusing to load at all."""
        loaded = from_state(self._stored_with_one_damaged())
        assert not loaded.is_complete
        assert [q.order_id for q in loaded.quarantined] == ["WR-LEGACY-DUP"]
        assert "judicial_review" in loaded.quarantined[0].reason

    def test_the_summary_says_the_deadlines_are_not_being_tracked(self) -> None:
        """The one line an operator reads. If it did not say so plainly, a partial
        load would pass for a complete one."""
        summary = from_state(self._stored_with_one_damaged()).summary()
        assert "QUARANTINED" in summary
        assert "NOT being tracked" in summary
        assert "WR-LEGACY-DUP" in summary

    def test_a_non_object_record_is_quarantined_too(self) -> None:
        loaded = from_state([entry_to_dict(_order()), "not an object"])
        assert len(loaded.entries) == 1
        assert not loaded.is_complete

    def test_a_clean_ledger_reports_complete(self) -> None:
        """Non-vacuity. A loader that always reported damage would pass every test
        above and be useless."""
        loaded = from_state(to_state(append((), _order())))
        assert loaded.is_complete
        assert "all readable" in loaded.summary()

    def test_a_corrupt_container_still_raises(self) -> None:
        """Per-entry tolerance is not per-store tolerance. A ledger that is not a
        list at all is a store problem, and reading it as an empty season would
        report no open deadlines for every order in it."""
        with pytest.raises(LedgerIntegrityError):
            from_state({"not": "a list"})
