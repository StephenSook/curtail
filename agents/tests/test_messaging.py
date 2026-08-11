"""Pub/Sub discipline. The prediction in the plan is tested here, not assumed.

The M3.3 plan put this on record before any code was written:

    the DEDUP TABLE, not the exactly-once flag, is what actually makes the chaos
    drill pass. Exactly-once is a delivery guarantee, not a processing one. If
    the drill passes with dedup disabled, my model of the guarantee is wrong and
    I want to know.

`TestExactlyOnceCannotDoWhatDedupDoes` is that test. It holds.
"""

from __future__ import annotations

from datetime import date

import pytest

from curtail_agents.messaging import (
    MAX_DELIVERY_ATTEMPTS,
    MIN_DELIVERY_ATTEMPTS,
    Channel,
    DedupTable,
    OrderType,
    SubscriptionConfig,
    notification_idempotency_key,
    order_idempotency_key,
    ordering_key_for_gage,
    ordering_key_for_right,
)
from curtail_core.basins import Basin


class TestExactlyOnceCannotDoWhatDedupDoes:
    """The distinction the whole module rests on.

    Exactly-once bounds how often the BROKER hands you a message. It says nothing
    about the same logical event being republished by a retried upstream agent
    under a fresh message id, and nothing about a handler that committed a side
    effect and then crashed before acknowledging. Those are the duplicates that
    reach a signed legal document, and only application-level identity stops
    them.
    """

    def test_a_redelivery_of_the_same_logical_event_is_a_no_op(self) -> None:
        table = DedupTable()
        key = order_idempotency_key("A-001", OrderType.IMPOSE, date(2026, 6, 15))
        assert table.claim(key) is True
        assert table.claim(key) is False

    def test_a_republish_under_a_new_message_id_is_still_caught(self) -> None:
        """The case exactly-once is structurally unable to see.

        A new message id is a new message to the broker. It is the SAME order for
        the same ranch on the same day to everyone else, and issuing it twice
        means two signed documents.
        """
        table = DedupTable()
        first = order_idempotency_key("A-001", OrderType.IMPOSE, date(2026, 6, 15))
        republished = order_idempotency_key("A-001", OrderType.IMPOSE, date(2026, 6, 15))
        assert table.claim(first) is True
        assert table.claim(republished) is False

    def test_claim_is_a_single_atomic_operation(self) -> None:
        """Not check-then-act.

        `if not seen: mark()` has a window between the two in which a concurrent
        consumer claims the same key, and both then process it, which is exactly
        the duplicate the table exists to prevent. `claim` returning a bool is
        also the shape a Cloud SQL INSERT ... ON CONFLICT DO NOTHING reports.
        """
        table = DedupTable()
        key = order_idempotency_key("A-001", OrderType.IMPOSE, date(2026, 6, 15))
        results = [table.claim(key) for _ in range(5)]
        assert results == [True, False, False, False, False]
        assert len(table) == 1


class TestIdentityIsMadeOfTheThingsThatDistinguishAnEvent:
    @pytest.mark.parametrize(
        ("label", "a", "b"),
        [
            (
                "different right",
                ("A-001", OrderType.IMPOSE, date(2026, 6, 15)),
                ("B-002", OrderType.IMPOSE, date(2026, 6, 15)),
            ),
            (
                "different order type",
                ("A-001", OrderType.IMPOSE, date(2026, 6, 15)),
                ("A-001", OrderType.SUSPEND, date(2026, 6, 15)),
            ),
            (
                "different effective date",
                ("A-001", OrderType.IMPOSE, date(2026, 6, 15)),
                ("A-001", OrderType.IMPOSE, date(2026, 6, 16)),
            ),
        ],
    )
    def test_each_component_changes_the_identity(
        self, label: str, a: tuple[str, OrderType, date], b: tuple[str, OrderType, date]
    ) -> None:
        """If any component did not, two genuinely different orders would collide
        and the second would be silently suppressed."""
        assert order_idempotency_key(*a) != order_idempotency_key(*b), label

    def test_the_channel_is_part_of_a_notification_identity(self) -> None:
        """An email and an SMS carrying the same notice are two deliveries.

        Collapsing them would let a delivered email suppress the SMS that email
        failing was supposed to escalate to, which is a notice nobody received
        being recorded as one that was sent.
        """
        by_email = notification_idempotency_key("recipient-1", "order-1", Channel.EMAIL)
        by_sms = notification_idempotency_key("recipient-1", "order-1", Channel.SMS)
        assert by_email != by_sms

    def test_the_same_notification_twice_has_one_identity(self) -> None:
        first = notification_idempotency_key("recipient-1", "order-1", Channel.EMAIL)
        again = notification_idempotency_key("recipient-1", "order-1", Channel.EMAIL)
        assert first == again

    def test_an_empty_component_is_refused_rather_than_hashed(self) -> None:
        """Hashing an empty right id produces a perfectly stable key for
        "nothing", which would then deduplicate every unidentified order against
        every other one."""
        with pytest.raises(ValueError):
            order_idempotency_key("", OrderType.IMPOSE, date(2026, 6, 15))
        with pytest.raises(ValueError):
            notification_idempotency_key("", "order-1", Channel.EMAIL)

    def test_an_empty_key_cannot_be_claimed(self) -> None:
        with pytest.raises(ValueError):
            DedupTable().claim("")


class TestOrderingKeysAreHighCardinality:
    """A single global key serialises the fleet behind its slowest message.

    That is correctness-preserving and throughput-destroying, and it only shows
    up under load, which is to say during the demo.
    """

    def test_two_rights_get_two_streams(self) -> None:
        assert ordering_key_for_right("A-001") != ordering_key_for_right("B-002")

    def test_two_basins_get_two_streams(self) -> None:
        """Readings for one river must stay ordered relative to each other: a
        suspension followed by a reinstatement processed backwards releases water
        the Board just protected. Different rivers have no such relationship and
        must not be coupled."""
        assert ordering_key_for_gage(Basin.SCOTT) != ordering_key_for_gage(Basin.SHASTA)

    def test_the_same_right_always_gets_the_same_stream(self) -> None:
        assert ordering_key_for_right("A-001") == ordering_key_for_right("A-001")

    def test_surrounding_whitespace_does_not_fork_a_stream(self) -> None:
        """A right id with a stray space would otherwise get its own ordering
        key, silently un-ordering it relative to its own earlier messages."""
        assert ordering_key_for_right(" A-001 ") == ordering_key_for_right("A-001")

    def test_an_empty_right_id_is_refused(self) -> None:
        """It would collapse every right onto one stream, which is the
        single-global-key failure wearing a different name."""
        with pytest.raises(ValueError):
            ordering_key_for_right("   ")

    def test_right_and_gage_keys_cannot_collide(self) -> None:
        """Namespaced, so a right happening to be named "scott" does not share a
        stream with the Scott gage."""
        assert ordering_key_for_right("scott") != ordering_key_for_gage(Basin.SCOTT)


class TestASubscriptionMustCarryItsSafetySettings:
    def test_a_valid_configuration_is_accepted(self) -> None:
        config = SubscriptionConfig(
            name="curtail-orders",
            topic="orders",
            dead_letter_topic="orders-dlq",
        )
        assert config.enable_exactly_once_delivery is True
        assert config.enable_message_ordering is True

    def test_a_subscription_without_a_dead_letter_topic_is_refused(self) -> None:
        """A message failing every attempt would be dropped silently, and a
        curtailment notice nobody received is indistinguishable from one nobody
        sent."""
        with pytest.raises(ValueError):
            SubscriptionConfig(name="x", topic="t", dead_letter_topic="")

    @pytest.mark.parametrize("attempts", [0, 1, 4, 101, 1000])
    def test_an_out_of_range_attempt_count_is_refused_here_not_on_deploy(
        self, attempts: int
    ) -> None:
        """Pub/Sub rejects the subscription outside 5 to 100. Better to fail in a
        unit test than during a deploy."""
        with pytest.raises(ValueError):
            SubscriptionConfig(
                name="x", topic="t", dead_letter_topic="dlq", max_delivery_attempts=attempts
            )

    @pytest.mark.parametrize("attempts", [MIN_DELIVERY_ATTEMPTS, 50, MAX_DELIVERY_ATTEMPTS])
    def test_the_permitted_range_is_accepted(self, attempts: int) -> None:
        config = SubscriptionConfig(
            name="x", topic="t", dead_letter_topic="dlq", max_delivery_attempts=attempts
        )
        assert config.max_delivery_attempts == attempts


class TestTheLibraryActuallySupportsWhatWeConfigure:
    """Read from the installed client, not from documentation.

    A configuration naming a field the library does not have is a claim that
    fails on deploy, and this repository's rule is to audit claims against the
    shipped code rather than against a plan.
    """

    def test_exactly_once_and_ordering_are_real_subscription_fields(self) -> None:
        from google.pubsub_v1.types import Subscription

        fields = set(Subscription.meta.fields)
        assert "enable_exactly_once_delivery" in fields
        assert "enable_message_ordering" in fields

    def test_the_dead_letter_policy_type_exists(self) -> None:
        from google.pubsub_v1.types import DeadLetterPolicy

        fields = set(DeadLetterPolicy.meta.fields)
        assert "dead_letter_topic" in fields
        assert "max_delivery_attempts" in fields
