"""Herald: the two lanes, and the one question a surface must ask before saying "served".

`_herald` was `return node_input`. Every type it needed already existed and was unused.
What is tested here is not that messages go out, but that the system never claims more
than it accomplished: an email a provider calls delivered is not service under Water Code
1121, and a partial service is not service at all.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from curtail_agents.herald import (
    BACKOFF_BASE_SECONDS,
    DeliveryReport,
    Recipient,
    TransportResult,
    TransportUnavailableError,
    backoff_seconds,
    deliver_order,
    synthetic_transport,
)
from curtail_agents.messaging import CLAIM_LEASE_SECONDS, Channel, DedupTable
from curtail_core.clocks import RecipientClass, ServiceLane, ServiceMethod

STAMP = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
ORDER = "WR-2024-0006-DWR-A6"


def party(rid: str, channel: Channel = Channel.CERTIFIED_MAIL) -> Recipient:
    return Recipient(rid, RecipientClass.PARTY, channel, "labelled synthetic")


def diverter(rid: str, channel: Channel = Channel.EMAIL) -> Recipient:
    return Recipient(rid, RecipientClass.OPERATIONAL_DIVERTER, channel, "labelled synthetic")


def send(
    action: str,
    recipients: list[Recipient],
    *,
    transport: Callable[[Recipient, str], TransportResult] | None = None,
    dedup: DedupTable | None = None,
) -> DeliveryReport:
    return deliver_order(
        order_id=ORDER,
        action=action,
        recipients=recipients,
        artifact="DRAFT ORDER",
        transport=transport or synthetic_transport(),
        dedup=dedup,
        synthetic=True,
        now=STAMP,
    )


class TestTheLaneIsDecidedByTheArtifact:
    """Never by the caller. A caller that could pick the lane could downgrade an order
    into a notification and skip service entirely."""

    @pytest.mark.parametrize(
        "action", ["initial_order", "reinstatement", "new_curtailment", "extension"]
    )
    def test_anything_imposing_curtailment_takes_the_legal_lane(self, action: str) -> None:
        assert send(action, [party("holder-1")]).lane is ServiceLane.LEGAL_SERVICE

    @pytest.mark.parametrize("action", ["suspension", "rescission", "drought_update"])
    def test_anything_else_takes_the_notification_lane(self, action: str) -> None:
        assert send(action, [diverter("d-1")]).lane is ServiceLane.NOTIFICATION

    def test_a_reinstating_addendum_is_treated_as_a_candidate_order(self) -> None:
        """Whether an addendum is itself an "order" triggering fresh 1121 service and a
        fresh 30-day clock is genuinely unadjudicated. The defensive choice is the one
        that survives being wrong."""
        assert send("reinstatement", [party("holder-1")]).lane is ServiceLane.LEGAL_SERVICE


class TestANotificationIsNeverService:
    """The reason this is two machines. A single delivered flag would let an operator
    believe a party was served when they were merely emailed."""

    def test_the_notification_lane_can_never_report_service(self) -> None:
        report = send("drought_update", [diverter("d-1"), diverter("d-2")])
        assert report.records, "nothing was delivered, so this proves nothing"
        assert report.may_report_as_served is False
        assert report.legally_served == ()

    def test_the_lane_check_stops_it_on_its_own(self) -> None:
        """Isolating the layer, because two rules enforce this and a mutation removing
        one of them changed nothing.

        Two rules enforce this and the first attempt at this test could not tell them
        apart. `ServiceRecord.constitutes_legal_service` checks the lane ITSELF, so a
        notification-lane record is rejected one level down and the report-level check
        never runs.

        The only shape that isolates the report-level check is a MISMATCHED report: a
        record that says legal service inside a report that says notification. That is
        the case the check exists for, since a caller assembling a report from the wrong
        lane's records is exactly how an operator would come to see the word "served".
        """
        from curtail_core.clocks import ServiceRecord

        impossible = ServiceRecord(
            order_id=ORDER,
            recipient_id="holder-1",
            recipient_class=RecipientClass.PARTY,
            lane=ServiceLane.LEGAL_SERVICE,
            method=ServiceMethod.CERTIFIED_MAIL,
            sent_at=STAMP,
            delivered_at=STAMP,
            receipt_reference="receipt-1",
        )
        report = DeliveryReport(
            order_id=ORDER,
            lane=ServiceLane.NOTIFICATION,
            records=(impossible,),
            attempts=(("holder-1", 1),),
        )
        assert impossible.constitutes_legal_service is True, (
            "the record must pass its own check, or this tests the wrong layer"
        )
        assert report.may_report_as_served is False, (
            "a notification-lane report claimed service on the strength of a legal-lane record"
        )

    def test_an_email_on_the_legal_lane_is_not_service(self) -> None:
        """A provider reporting success does not make an email one of the four methods
        Water Code 1121 permits."""
        report = send("initial_order", [party("holder-1", Channel.EMAIL)])
        assert report.may_report_as_served is False
        assert any("not a permitted method" in e for e in report.escalations)

    def test_a_delivery_with_no_receipt_is_not_service(self) -> None:
        """The courier arrived and nobody signed. Accepted by the transport, and still
        not service."""
        report = send(
            "initial_order",
            [party("holder-1")],
            transport=synthetic_transport(withhold_receipt_for=["holder-1"]),
        )
        assert report.records[0].delivered_at is None
        assert report.records[0].constitutes_legal_service is False
        assert report.may_report_as_served is False
        assert any("no receipt of delivery" in e for e in report.escalations)

    @pytest.mark.parametrize(
        ("channel", "method"),
        [
            (Channel.CERTIFIED_MAIL, ServiceMethod.CERTIFIED_MAIL),
            (Channel.PERSONAL_DELIVERY, ServiceMethod.PERSONAL_DELIVERY),
            (Channel.RECEIPTED_DELIVERY, ServiceMethod.RECEIPTED_PHYSICAL_DELIVERY),
        ],
    )
    def test_a_receipted_permitted_method_does_effect_service(
        self, channel: Channel, method: ServiceMethod
    ) -> None:
        """Non-vacuity, and it matters: a module that never reports service would pass
        every test above while making the system useless."""
        report = send("initial_order", [party("holder-1", channel)])
        assert report.records[0].method is method
        assert report.records[0].constitutes_legal_service is True
        assert report.may_report_as_served is True
        assert report.escalations == ()


class TestPartialServiceIsNotService:
    def test_one_unserved_party_withholds_the_whole_report(self) -> None:
        """An order shown as served while one party was missed is the failure a
        reviewing court would find first."""
        report = send(
            "initial_order",
            [party("holder-1"), party("holder-2")],
            transport=synthetic_transport(withhold_receipt_for=["holder-2"]),
        )
        assert "holder-1" in report.legally_served
        assert "holder-2" not in report.legally_served
        assert report.may_report_as_served is False

    def test_serving_nobody_is_not_success(self) -> None:
        """An empty set satisfies "every party was served" vacuously, which is the
        shape this project has been bitten by more than once."""
        report = send("initial_order", [])
        assert report.may_report_as_served is False

    def test_a_diverter_alone_does_not_constitute_service(self) -> None:
        """Water Code 1121 serves "the parties". 875(d)(1) reaches operational diverters
        with an onward-notice duty, which is a different thing."""
        report = send("initial_order", [diverter("d-1", Channel.CERTIFIED_MAIL)])
        assert report.may_report_as_served is False


class TestRetryAndEscalation:
    def test_a_transient_failure_is_retried(self) -> None:
        report = send(
            "initial_order",
            [party("holder-1")],
            transport=synthetic_transport(fail_first=2),
        )
        assert dict(report.attempts)["holder-1"] == 3
        assert report.may_report_as_served is True

    def test_exhausting_the_attempts_escalates_rather_than_failing_silently(self) -> None:
        report = send(
            "initial_order",
            [party("holder-1")],
            transport=synthetic_transport(fail_first=99),
        )
        assert report.records == ()
        assert any("was not reached after" in e for e in report.escalations)
        assert report.may_report_as_served is False

    def test_backoff_doubles_from_one_second(self) -> None:
        assert backoff_seconds(1) == BACKOFF_BASE_SECONDS
        assert backoff_seconds(2) == BACKOFF_BASE_SECONDS * 2
        assert backoff_seconds(5) == BACKOFF_BASE_SECONDS * 16
        with pytest.raises(ValueError, match="1 or greater"):
            backoff_seconds(0)


class TestOneDeliveryPerRecipient:
    def test_a_redelivery_is_a_no_op(self) -> None:
        """On the legal lane a second delivery would write a second service record for
        one act, which misstates the record rather than merely wasting a message."""
        table = DedupTable()
        first = send("initial_order", [party("holder-1")], dedup=table)
        second = send("initial_order", [party("holder-1")], dedup=table)
        assert first.records and first.legally_served == ("holder-1",)
        assert second.records == ()
        assert second.skipped_as_duplicate == ("holder-1",)

    def test_an_unserved_party_can_still_be_served_later(self) -> None:
        """The escalation has to be actionable through this system, or it is a dead end.

        Completing on ACCEPTANCE closed that door: a courier who arrived and got no
        signature marked the key done, so serving that party again over the same channel
        was skipped as a duplicate forever. A human received an escalation they could not
        act on.

        Now an unfinished act leaves the claim to lapse with its lease. Inside the lease
        a second actor must not duplicate an in-flight attempt, which is what the lease
        is for; after it, remediation proceeds.
        """
        ticks = [0.0]
        table = DedupTable(_clock=lambda: ticks[0])

        unsigned = send(
            "initial_order",
            [party("holder-1")],
            transport=synthetic_transport(withhold_receipt_for=["holder-1"]),
            dedup=table,
        )
        assert unsigned.legally_served == ()
        assert unsigned.escalations

        blocked = send("initial_order", [party("holder-1")], dedup=table)
        assert blocked.skipped_as_duplicate == ("holder-1",), (
            "an in-flight claim must still exclude a concurrent second attempt"
        )

        ticks[0] = CLAIM_LEASE_SECONDS + 1
        remedied = send("initial_order", [party("holder-1")], dedup=table)
        assert remedied.legally_served == ("holder-1",), (
            "the escalation could not be remediated, so it was a dead end"
        )
        assert remedied.may_report_as_served is True

    def test_a_remediation_is_flagged_as_a_possible_second_delivery(self) -> None:
        """The crash-between-side-effect-and-ack problem, in its legal form.

        A lapsed claim does not say whether the earlier attempt REACHED the recipient. A
        worker can die after the courier delivers and before the record is written, and a
        transport can accept a delivery and then lose the receipt, which is a records
        failure rather than a delivery failure. From inside this function those are
        indistinguishable.

        Retrying is still right: an unserved party makes the order unenforceable against
        them, while a duplicate service is untidy rather than fatal. But a second physical
        delivery must never be SILENT, because the record is what a reviewing court reads
        and it would show one act as two.
        """
        ticks = [0.0]
        table = DedupTable(_clock=lambda: ticks[0])

        send(
            "initial_order",
            [party("holder-1")],
            transport=synthetic_transport(withhold_receipt_for=["holder-1"]),
            dedup=table,
        )
        ticks[0] = CLAIM_LEASE_SECONDS + 1
        remedied = send("initial_order", [party("holder-1")], dedup=table)

        assert remedied.legally_served == ("holder-1",)
        assert remedied.possible_duplicate_service == ("holder-1",), (
            "the party may have been physically served twice and the report did not say so"
        )

        # ON THE RECORD, which is the point. A report is transient: it lives for one
        # distribution run and then nothing carries its warning forward. The record is
        # what persists and what a reviewing court reads, and two service acts with no
        # note attached to either is the misstatement this exists to prevent.
        served_record = next(r for r in remedied.records if r.recipient_id == "holder-1")
        assert served_record.possible_duplicate is True
        assert served_record.constitutes_legal_service is True, (
            "a duplicate delivery is still delivery; it needs an explanation travelling "
            "with it, not invalidation"
        )

    def test_the_report_derives_its_view_from_the_records(self) -> None:
        """Held as a separate list, the report and the records could disagree about the
        same delivery, and only the records persist."""
        ticks = [0.0]
        table = DedupTable(_clock=lambda: ticks[0])
        send(
            "initial_order",
            [party("holder-1")],
            transport=synthetic_transport(withhold_receipt_for=["holder-1"]),
            dedup=table,
        )
        ticks[0] = CLAIM_LEASE_SECONDS + 1
        remedied = send("initial_order", [party("holder-1")], dedup=table)

        assert remedied.possible_duplicate_service == tuple(
            r.recipient_id for r in remedied.records if r.possible_duplicate
        )

    def test_an_unassessed_record_is_neither_flagged_nor_treated_as_clean(self) -> None:
        """A report assembled from stored records can contain one nobody checked, and it
        must be visible as its own state rather than folded into either answer."""
        from curtail_core.clocks import ServiceRecord

        unassessed = ServiceRecord(
            order_id=ORDER,
            recipient_id="holder-legacy",
            recipient_class=RecipientClass.PARTY,
            lane=ServiceLane.LEGAL_SERVICE,
            method=ServiceMethod.CERTIFIED_MAIL,
            sent_at=STAMP,
            delivered_at=STAMP,
            receipt_reference="receipt-legacy",
        )
        assert unassessed.possible_duplicate is None

        report = DeliveryReport(
            order_id=ORDER,
            lane=ServiceLane.LEGAL_SERVICE,
            records=(unassessed,),
            attempts=(("holder-legacy", 1),),
        )
        assert report.possible_duplicate_service == (), "unknown was reported as a duplicate"
        assert report.unassessed_for_duplication == ("holder-legacy",)
        assert report.legally_served == ("holder-legacy",), (
            "not knowing whether it was a duplicate does not undo the service"
        )

    def test_herald_always_assesses_what_it_delivers(self) -> None:
        """So the unknown state only ever arrives from storage, never from a live run."""
        report = send("initial_order", [party("holder-1")], dedup=DedupTable())
        assert all(r.possible_duplicate is not None for r in report.records)
        assert report.unassessed_for_duplication == ()

    def test_a_first_delivery_is_never_flagged(self) -> None:
        """Non-vacuity. A field that flagged everything would be read as noise and then
        ignored exactly when it mattered."""
        report = send("initial_order", [party("holder-1")], dedup=DedupTable())
        assert report.legally_served == ("holder-1",)
        assert report.possible_duplicate_service == ()
        assert all(not r.possible_duplicate for r in report.records)

    def test_the_notification_lane_is_not_flagged(self) -> None:
        """A duplicate email is not a duplicate legal act, and flagging it would dilute
        the signal on the lane where the record matters."""
        ticks = [0.0]
        table = DedupTable(_clock=lambda: ticks[0])

        send(
            "drought_update",
            [diverter("d-1")],
            transport=synthetic_transport(fail_first=99),
            dedup=table,
        )
        ticks[0] = CLAIM_LEASE_SECONDS + 1
        again = send("drought_update", [diverter("d-1")], dedup=table)
        assert again.possible_duplicate_service == ()

    def test_a_completed_service_stays_a_no_op_forever(self) -> None:
        """The other half. Once service IS effected, no lease expiry may let it happen
        twice: that would write a second service record for one act."""
        ticks = [0.0]
        table = DedupTable(_clock=lambda: ticks[0])

        first = send("initial_order", [party("holder-1")], dedup=table)
        assert first.legally_served == ("holder-1",)

        ticks[0] = CLAIM_LEASE_SECONDS * 10
        again = send("initial_order", [party("holder-1")], dedup=table)
        assert again.records == ()
        assert again.skipped_as_duplicate == ("holder-1",)

    def test_a_notification_is_finished_when_it_leaves_the_building(self) -> None:
        """There is no receipt to obtain on this lane, so acceptance completes it."""
        ticks = [0.0]
        table = DedupTable(_clock=lambda: ticks[0])

        send("drought_update", [diverter("d-1")], dedup=table)
        ticks[0] = CLAIM_LEASE_SECONDS * 10
        repeat = send("drought_update", [diverter("d-1")], dedup=table)
        assert repeat.skipped_as_duplicate == ("d-1",)

    def test_a_failed_delivery_is_not_marked_done(self) -> None:
        """It is not completed, so the claim lapses with its lease and a later run can
        retry it. Marking a failure as done loses the event permanently."""
        table = DedupTable()
        failed = send(
            "initial_order",
            [party("holder-1")],
            transport=synthetic_transport(fail_first=99),
            dedup=table,
        )
        assert failed.escalations
        assert table.has_seen(next(iter(table._claims))) is False


class TestItRefusesRatherThanPretending:
    def test_no_transport_means_no_delivery(self) -> None:
        """A recorded delivery that never happened is worse than a visible failure: the
        record is what a reviewing court reads."""
        with pytest.raises(TransportUnavailableError, match="Refusing"):
            deliver_order(
                order_id=ORDER,
                action="initial_order",
                recipients=[party("holder-1")],
                artifact="DRAFT",
            )

    def test_a_synthetic_run_is_marked_as_synthetic(self) -> None:
        """So no surface can present a demonstration as real delivery."""
        assert send("initial_order", [party("holder-1")]).synthetic is True

    def test_a_report_carries_no_contact_detail(self) -> None:
        """Recipients are labels, never addresses. No personal contact data is stored."""
        report = send("initial_order", [party("holder-1")])
        assert "@" not in str(report)


class TestTheTransportContract:
    def test_an_accepted_result_without_a_receipt_is_representable(self) -> None:
        """The type has to be able to say "it arrived and nobody signed", or the case
        that separates delivery from service cannot be modelled at all."""
        result = TransportResult(accepted=True)
        assert result.receipt_reference is None
        assert result.delivered_at is None
