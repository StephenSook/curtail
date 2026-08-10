"""Golden tests for statutory clocks and the LCS lifecycle."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from curtail_core.adjudications import Basin
from curtail_core.clocks import (
    DELEGATION_RESOLUTION,
    Clock,
    ClockType,
    RecipientClass,
    ServiceLane,
    ServiceMethod,
    ServiceRecord,
    SignatoryRole,
    clocks_for_order,
    lane_for_action,
    open_clocks,
)
from curtail_core.lcs import (
    BASELINE_YEARS,
    COORDINATING_ENTITIES,
    PROTECTIVE_STATES,
    LcsState,
    LcsTransitionError,
    LocalCooperativeSolution,
    safe_harbor_confers_exemption,
)

ADOPTED = datetime(2026, 6, 16, 17, 0, tzinfo=UTC)


def _by_type(clocks: list[Clock], t: ClockType) -> Clock:
    return next(c for c in clocks if c.clock_type is t)


class TestReconsiderationClocks:
    def test_petition_window_is_30_days(self) -> None:
        c = _by_type(
            clocks_for_order(ADOPTED, signatory=SignatoryRole.DEPUTY_DIRECTOR),
            ClockType.RECONSIDERATION_PETITION,
        )
        assert c.closes_at == ADOPTED + timedelta(days=30)
        assert c.citation == "Water Code 1122"

    def test_board_response_window_is_90_days_and_cites_1122_not_1123(self) -> None:
        """The 90-day deadline is in 1122. Section 1123 governs the manner."""
        c = _by_type(
            clocks_for_order(ADOPTED, signatory=SignatoryRole.DEPUTY_DIRECTOR),
            ClockType.BOARD_RESPONSE,
        )
        assert c.closes_at == ADOPTED + timedelta(days=90)
        assert c.citation == "Water Code 1122"
        assert "not 1123" in c.description

    def test_missing_the_90_days_does_not_divest_jurisdiction(self) -> None:
        c = _by_type(
            clocks_for_order(ADOPTED, signatory=SignatoryRole.DEPUTY_DIRECTOR),
            ClockType.BOARD_RESPONSE,
        )
        assert c.agency_deadline_non_jurisdictional


class TestDelegationException:
    """Water Code 1126(b). The subtle one."""

    def test_deputy_director_orders_require_exhaustion(self) -> None:
        clocks = clocks_for_order(ADOPTED, signatory=SignatoryRole.DEPUTY_DIRECTOR)
        assert _by_type(clocks, ClockType.RECONSIDERATION_PETITION).exhaustion_required
        assert _by_type(clocks, ClockType.JUDICIAL_REVIEW).exhaustion_required

    def test_executive_director_orders_also_require_exhaustion(self) -> None:
        """875(b)(2) assigns some determinations to the Executive Director."""
        clocks = clocks_for_order(ADOPTED, signatory=SignatoryRole.EXECUTIVE_DIRECTOR)
        assert _by_type(clocks, ClockType.JUDICIAL_REVIEW).exhaustion_required

    def test_board_orders_do_not_require_exhaustion(self) -> None:
        """The exception applies to DELEGATED authority. The Board is not delegate."""
        clocks = clocks_for_order(ADOPTED, signatory=SignatoryRole.BOARD)
        assert not _by_type(clocks, ClockType.JUDICIAL_REVIEW).exhaustion_required

    def test_the_delegation_resolution_is_named_in_the_record(self) -> None:
        c = _by_type(
            clocks_for_order(ADOPTED, signatory=SignatoryRole.DEPUTY_DIRECTOR),
            ClockType.JUDICIAL_REVIEW,
        )
        assert DELEGATION_RESOLUTION in c.description


class TestStatutoryFloors:
    def test_certification_below_seven_days_is_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"875\.6"):
            clocks_for_order(ADOPTED, signatory=SignatoryRole.DEPUTY_DIRECTOR, certification_days=6)

    def test_certification_at_exactly_seven_days_is_allowed(self) -> None:
        clocks = clocks_for_order(
            ADOPTED, signatory=SignatoryRole.DEPUTY_DIRECTOR, certification_days=7
        )
        assert _by_type(clocks, ClockType.CERTIFICATION).closes_at == ADOPTED + timedelta(days=7)

    def test_information_response_below_five_days_is_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"875\.8"):
            clocks_for_order(
                ADOPTED, signatory=SignatoryRole.DEPUTY_DIRECTOR, information_response_days=4
            )

    def test_naive_timestamp_is_rejected(self) -> None:
        """A naive timestamp in a legal deadline is a silent off-by-hours error."""
        with pytest.raises(ValueError, match="timezone-aware"):
            clocks_for_order(datetime(2026, 6, 16, 17, 0), signatory=SignatoryRole.BOARD)  # noqa: DTZ001


class TestOpenClocks:
    def test_a_clock_closes_after_its_window(self) -> None:
        clocks = clocks_for_order(ADOPTED, signatory=SignatoryRole.DEPUTY_DIRECTOR)
        assert open_clocks(clocks, ADOPTED + timedelta(days=10))
        still_open = open_clocks(clocks, ADOPTED + timedelta(days=45))
        assert {c.clock_type for c in still_open} == {ClockType.BOARD_RESPONSE}
        assert open_clocks(clocks, ADOPTED + timedelta(days=200)) == []

    def test_days_remaining_never_goes_negative(self) -> None:
        c = _by_type(
            clocks_for_order(ADOPTED, signatory=SignatoryRole.BOARD),
            ClockType.RECONSIDERATION_PETITION,
        )
        assert c.days_remaining(ADOPTED + timedelta(days=1000)) == 0


class TestServiceLanes:
    """Formal service and notification are legally different acts."""

    def test_reinstatement_requires_legal_service(self) -> None:
        assert lane_for_action("reinstatement") is ServiceLane.LEGAL_SERVICE
        assert lane_for_action("initial_order") is ServiceLane.LEGAL_SERVICE

    def test_suspension_travels_the_notification_lane(self) -> None:
        assert lane_for_action("suspension") is ServiceLane.NOTIFICATION
        assert lane_for_action("rescission") is ServiceLane.NOTIFICATION

    def test_a_delivered_notification_is_not_legal_service(self) -> None:
        """The single most dangerous UI confusion available here.

        A green delivered indicator in the notification lane must never read as
        service, or an operator believes a party was served when they were
        merely emailed.
        """
        record = ServiceRecord(
            order_id="WR-2026-0005-DWR",
            recipient_id="R-1",
            recipient_class=RecipientClass.PARTY,
            lane=ServiceLane.NOTIFICATION,
            method=None,
            sent_at=ADOPTED,
            delivered_at=ADOPTED + timedelta(minutes=2),
        )
        assert not record.constitutes_legal_service

    def test_legal_service_needs_method_delivery_and_receipt(self) -> None:
        base = {
            "order_id": "WR-2026-0005-DWR",
            "recipient_id": "R-1",
            "recipient_class": RecipientClass.PARTY,
            "lane": ServiceLane.LEGAL_SERVICE,
            "sent_at": ADOPTED,
        }
        complete = ServiceRecord(
            **base,  # type: ignore[arg-type]
            method=ServiceMethod.CERTIFIED_MAIL,
            delivered_at=ADOPTED + timedelta(days=3),
            receipt_reference="USPS-9400-1",
        )
        assert complete.constitutes_legal_service

        no_receipt = ServiceRecord(
            **base,  # type: ignore[arg-type]
            method=ServiceMethod.CERTIFIED_MAIL,
            delivered_at=ADOPTED + timedelta(days=3),
        )
        assert not no_receipt.constitutes_legal_service

    def test_all_four_statutory_methods_exist(self) -> None:
        """Water Code 1121 as amended by SB 756 permits four, not two."""
        assert len(list(ServiceMethod)) == 4


class TestLcsLifecycle:
    def _lcs(self, state: LcsState = LcsState.PETITIONED) -> LocalCooperativeSolution:
        return LocalCooperativeSolution(
            lcs_id="LCS-2026-01",
            basin=Basin.SCOTT,
            state=state,
            coordinating_entity="Scott River Water Trust",
            covered_right_ids=frozenset({"S-1", "S-2"}),
            baseline_year=2022,
        )

    def test_it_is_a_lifecycle_not_a_boolean(self) -> None:
        assert len(list(LcsState)) == 7

    def test_pending_states_still_protect(self) -> None:
        """The regulation protects rights under an approved OR PENDING solution."""
        for state in (LcsState.PETITIONED, LcsState.NOTICED, LcsState.OBJECTED):
            assert self._lcs(state).protects("S-1")

    def test_rescinded_removes_protection(self) -> None:
        assert not self._lcs(LcsState.RESCINDED).protects("S-1")
        assert LcsState.RESCINDED not in PROTECTIVE_STATES

    def test_uncovered_right_is_never_protected(self) -> None:
        assert not self._lcs(LcsState.APPROVED).protects("SOMEONE-ELSE")

    def test_illegal_transition_is_refused(self) -> None:
        with pytest.raises(LcsTransitionError, match="cannot move"):
            self._lcs(LcsState.PETITIONED).transition(LcsState.MONITORED)

    def test_legal_transition_chain(self) -> None:
        lcs = self._lcs(LcsState.PETITIONED)
        for nxt in (LcsState.NOTICED, LcsState.APPROVED, LcsState.MONITORED):
            lcs = lcs.transition(nxt)
        assert lcs.state is LcsState.MONITORED

    def test_rescinded_is_terminal(self) -> None:
        lcs = self._lcs(LcsState.PETITIONED).transition(LcsState.RESCINDED)
        with pytest.raises(LcsTransitionError, match="terminal state"):
            lcs.transition(LcsState.APPROVED)

    def test_unapproved_coordinating_entity_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not an approved Coordinating Entity"):
            LocalCooperativeSolution(
                lcs_id="X",
                basin=Basin.SCOTT,
                state=LcsState.PETITIONED,
                coordinating_entity="Some Consultancy LLC",
                covered_right_ids=frozenset(),
                baseline_year=2022,
            )

    def test_baseline_year_outside_the_permitted_set_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="baseline year"):
            LocalCooperativeSolution(
                lcs_id="X",
                basin=Basin.SCOTT,
                state=LcsState.PETITIONED,
                coordinating_entity="Scott River Water Trust",
                covered_right_ids=frozenset(),
                baseline_year=2019,
            )

    def test_all_four_coordinating_entities_and_baseline_years(self) -> None:
        assert len(COORDINATING_ENTITIES) == 4
        assert BASELINE_YEARS == {2020, 2021, 2022, 2023}


class TestReductionRequirements:
    def test_scott_is_thirty_percent_with_a_monthly_overlay(self) -> None:
        req = LocalCooperativeSolution(
            lcs_id="A",
            basin=Basin.SCOTT,
            state=LcsState.APPROVED,
            coordinating_entity="Siskiyou Resource Conservation District",
            covered_right_ids=frozenset(),
            baseline_year=2021,
        ).requirement
        assert req.season_percent == 30.0
        assert req.monthly_percent == 30.0
        assert req.monthly_months == (7, 8, 9, 10)
        assert req.applies_on(date(2026, 4, 1))
        assert req.applies_on(date(2026, 10, 31))
        assert not req.applies_on(date(2026, 11, 1))
        assert req.monthly_applies_on(date(2026, 8, 15))
        assert not req.monthly_applies_on(date(2026, 6, 15))

    def test_shasta_is_fifteen_percent_with_no_monthly_overlay(self) -> None:
        req = LocalCooperativeSolution(
            lcs_id="B",
            basin=Basin.SHASTA,
            state=LcsState.APPROVED,
            coordinating_entity="Shasta Valley Resource Conservation District",
            covered_right_ids=frozenset(),
            baseline_year=2023,
        ).requirement
        assert req.season_percent == 15.0
        assert req.monthly_percent is None


class TestSafeHarbourIsNotAnLcs:
    def test_safe_harbor_does_not_exempt_from_curtailment(self) -> None:
        """Legally distinct from an LCS and routinely conflated.

        Safe Harbor is administered through NOAA Fisheries and CDFW. Exemption
        requires a separate approved LCS or another regulatory exception.
        """
        assert safe_harbor_confers_exemption() is False
