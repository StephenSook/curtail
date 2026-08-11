"""The fleet graph, and the loop breaker attached to it.

This file exists because a docstring once claimed `RetryConfig` and
`NodeTimeoutError` were in force while both were dead constants. A guard
described in prose and absent from the code is the drift this project audits
for, so the claim is now asserted mechanically: these tests fail if the policy is
detached, if the constants drift apart, or if the graph stops assembling.
"""

from __future__ import annotations

import pytest
from google.adk.workflow import Edge

from curtail_agents.fleet import (
    CORE,
    GRAPH_TIMEOUT_SECONDS,
    HERALD,
    NODE_POLICY,
    SCRIBE,
    SENTINEL,
    TRANSIENT_RETRY,
    build_curtailment_graph,
    core_node,
    herald_node,
    policy_for,
    scribe_node,
    sentinel_node,
)
from curtail_agents.routing import (
    BACKOFF_FACTOR,
    INITIAL_DELAY_SECONDS,
    JITTER,
    MAX_ATTEMPTS,
    MAX_DELAY_SECONDS,
    NODE_TIMEOUT_SECONDS,
)


def _edges(graph: object) -> list[Edge]:
    """Narrow `Workflow.edges` to real Edge objects.

    ADK types the field as a union of Edge and several tuple/dict shorthands, so
    a bare `e.from_node` does not type-check. Filtering is honest about that
    rather than casting past it: a graph built from shorthand would simply have
    no edges to assert on, and the assertions would then fail loudly instead of
    silently passing over an empty list.
    """
    found = [e for e in graph.edges if isinstance(e, Edge)]  # type: ignore[attr-defined]
    assert found, "no Edge objects on the graph; the assertions below would be vacuous"
    return found


def _node_names(graph: object) -> set[str]:
    """Every node name the graph actually references, from both edge ends."""
    edges = _edges(graph)
    return {e.to_node.name for e in edges} | {e.from_node.name for e in edges}


class TestTheGraphAssembles:
    def test_it_builds(self) -> None:
        assert build_curtailment_graph().name == "curtailment"

    def test_the_path_to_the_scribe_runs_only_through_the_core(self) -> None:
        """Separation of concerns made mechanical.

        There must be no path by which a drafted order exists without a computed
        recommendation behind it, and the graph is where that is enforced rather
        than remembered.
        """
        graph = build_curtailment_graph()
        into_scribe = {e.from_node.name for e in _edges(graph) if e.to_node.name == SCRIBE}
        assert into_scribe == {CORE}

    def test_nothing_reaches_the_core_except_the_sentinel(self) -> None:
        graph = build_curtailment_graph()
        into_core = {e.from_node.name for e in _edges(graph) if e.to_node.name == CORE}
        assert into_core == {SENTINEL}

    def test_the_graph_carries_its_own_ceiling(self) -> None:
        """A per-node timeout bounds one hop. Without a graph-level bound, a
        chain of individually acceptable hops adds up to an unbounded run."""
        graph = build_curtailment_graph()
        assert graph.timeout is not None
        assert graph.timeout >= NODE_TIMEOUT_SECONDS


class TestTheLoopBreakerIsAttachedNotDescribed:
    @pytest.mark.parametrize("name", [SENTINEL, SCRIBE, HERALD])
    def test_a_transient_node_carries_a_retry_policy(self, name: str) -> None:
        assert policy_for(name).retry_config is TRANSIENT_RETRY

    @pytest.mark.parametrize("name", [SENTINEL, CORE, SCRIBE, HERALD])
    def test_every_node_carries_a_timeout(self, name: str) -> None:
        """A model that loops does not fail, it never returns, so an attempt
        ceiling alone never fires. Only the pair covers both shapes."""
        assert policy_for(name).timeout == NODE_TIMEOUT_SECONDS

    def test_the_retry_policy_is_built_from_the_declared_constants(self) -> None:
        """One source for both, so the threshold the routing guard escalates at
        and the attempts the graph permits cannot drift apart. If they did, the
        guard would escalate a draft the graph was still retrying."""
        assert TRANSIENT_RETRY.max_attempts == MAX_ATTEMPTS
        assert TRANSIENT_RETRY.initial_delay == INITIAL_DELAY_SECONDS
        assert TRANSIENT_RETRY.max_delay == MAX_DELAY_SECONDS
        assert TRANSIENT_RETRY.backoff_factor == BACKOFF_FACTOR
        assert TRANSIENT_RETRY.jitter == JITTER

    def test_jitter_is_present_and_non_zero(self) -> None:
        """Without it, N agents failing on the same downstream outage retry in
        lockstep and re-create the load that caused the failure."""
        assert TRANSIENT_RETRY.jitter is not None
        assert TRANSIENT_RETRY.jitter > 0


class TestTheDeterministicNodeDoesNotRetry:
    """Retrying pure arithmetic is worse than useless.

    The Allocation Core produces the same recommendation from the same rights
    table and reading, so a failure reproduces exactly and a retry only delays
    the human who needs to see it. A retry policy there would also imply its
    failures are transient, which invites treating a data defect as noise.
    """

    def test_the_core_has_no_retry_policy(self) -> None:
        assert policy_for(CORE).retry_config is None

    def test_the_core_still_has_a_timeout(self) -> None:
        """A hang is a different failure from an exception, and determinism does
        not protect against one."""
        assert policy_for(CORE).timeout is not None

    def test_the_asymmetry_is_explained_in_the_policy_itself(self) -> None:
        """A reviewer reading the table must find the reason beside the value,
        not in a commit message they will never see."""
        rationale = policy_for(CORE).rationale
        assert "deterministic" in rationale.lower()


class TestThePolicyTableCannotSilentlyDriftFromTheGraph:
    def test_every_graph_node_has_a_policy(self) -> None:
        graph = build_curtailment_graph()
        in_graph = _node_names(graph)
        real = {n for n in in_graph if not n.startswith("__")}
        assert real <= set(NODE_POLICY), f"nodes with no policy: {real - set(NODE_POLICY)}"

    def test_the_policy_table_names_no_node_the_graph_lacks(self) -> None:
        graph = build_curtailment_graph()
        in_graph = _node_names(graph)
        assert set(NODE_POLICY) <= in_graph

    def test_an_unknown_node_raises_rather_than_defaulting(self) -> None:
        """Returning "no retry, no timeout" for an unrecognised node would hand
        it the weakest possible behaviour at the moment nobody is looking."""
        with pytest.raises(KeyError):
            policy_for("ghost_node")


class TestTheRuntimeNodesCarryThePolicy:
    """Assert the NODE, not the table describing it.

    A review proved the earlier tests unsafe: they checked `policy_for(name)`
    while the decorators carried their own separate arguments, so changing
    `@node(name=SCRIBE, retry_config=TRANSIENT_RETRY)` to `retry_config=None`
    left every test GREEN while the Scribe silently stopped retrying. A test
    asserting a claim rather than a reality, in the file whose stated purpose is
    preventing exactly that.

    Nodes are now DERIVED from the policy table, so the two cannot disagree by
    construction, and these tests read the constructed objects to prove it.
    """

    def test_each_transient_node_object_actually_carries_the_retry_policy(self) -> None:
        for built in (sentinel_node, scribe_node, herald_node):
            assert built.retry_config is TRANSIENT_RETRY, built.name

    def test_the_core_node_object_actually_has_no_retry_policy(self) -> None:
        """Not "the table says None". The node itself."""
        assert core_node.retry_config is None

    def test_every_node_object_carries_its_timeout(self) -> None:
        for built in (sentinel_node, core_node, scribe_node, herald_node):
            assert built.timeout == NODE_TIMEOUT_SECONDS, built.name

    def test_every_node_object_agrees_with_its_policy_entry(self) -> None:
        """The coupling, asserted end to end."""
        for built in (sentinel_node, core_node, scribe_node, herald_node):
            policy = policy_for(built.name)
            assert built.retry_config is policy.retry_config, built.name
            assert built.timeout == policy.timeout, built.name

    def test_the_graph_uses_those_same_node_objects(self) -> None:
        """A graph built from different instances would leave the assertions
        above true and the running system unpoliced."""
        graph = build_curtailment_graph()
        by_name = {e.to_node.name: e.to_node for e in _edges(graph)}
        assert by_name[SCRIBE].retry_config is TRANSIENT_RETRY
        assert by_name[CORE].retry_config is None


class TestTheGraphCeilingCoversTheRetriesItPermits:
    """A ceiling shorter than the retries it allows converts recoverable
    slowness into a workflow failure.

    The original was `NODE_TIMEOUT_SECONDS * 4`, which assumed each node ran
    once. With three attempts on three transient nodes the attempts alone reach
    1200 seconds, so a 480 second ceiling truncated its own retry policy.
    """

    def test_the_ceiling_covers_every_permitted_attempt(self) -> None:
        graph = build_curtailment_graph()
        transient = 3
        attempts = NODE_TIMEOUT_SECONDS * (MAX_ATTEMPTS * transient + 1)
        assert graph.timeout is not None
        assert graph.timeout >= attempts

    def test_the_ceiling_also_covers_the_backoff_delays(self) -> None:
        graph = build_curtailment_graph()
        transient = 3
        attempts = NODE_TIMEOUT_SECONDS * (MAX_ATTEMPTS * transient + 1)
        backoff = (
            sum(
                min(MAX_DELAY_SECONDS, INITIAL_DELAY_SECONDS * BACKOFF_FACTOR**i)
                for i in range(MAX_ATTEMPTS - 1)
            )
            * transient
        )
        assert graph.timeout is not None
        assert graph.timeout >= attempts + backoff

    def test_the_ceiling_is_computed_not_a_literal(self) -> None:
        """Raising MAX_ATTEMPTS must not leave the ceiling behind."""
        assert GRAPH_TIMEOUT_SECONDS == build_curtailment_graph().timeout
        assert GRAPH_TIMEOUT_SECONDS > NODE_TIMEOUT_SECONDS * 4
