"""The fleet graph, and the loop breaker attached to it.

This file exists because a docstring once claimed `RetryConfig` and
`NodeTimeoutError` were in force while both were dead constants. A guard
described in prose and absent from the code is the drift this project audits
for, so the claim is now asserted mechanically: these tests fail if the policy is
detached, if the constants drift apart, or if the graph stops assembling.
"""

from __future__ import annotations

import asyncio

import pytest
from google.adk.events import Event
from google.adk.sessions import Session
from google.adk.workflow import START, Edge, Workflow, node

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


class TestTheSentinelNodeRunsTheRealAgent:
    """A graph of no-op functions carries a retry policy that governs nothing.

    That is the same defect as a guard described in prose: structure present,
    force absent. The Sentinel node calls the real `sentinel.evaluate`, so its
    retry policy protects actual work.
    """

    async def test_it_classifies_a_real_reading(self) -> None:
        from datetime import UTC, datetime

        from curtail_agents.events import EventType, Provenance
        from curtail_agents.fleet import _sentinel
        from curtail_agents.sentinel import Observation
        from curtail_core.basins import Basin

        state = {
            "observation": Observation(
                Basin.SCOTT, 48.7, datetime(2025, 7, 20, 21, 30, tzinfo=UTC), Provenance.USGS_LIVE
            ),
            "correlation_id": "c-1",
        }
        result = await _sentinel(state)
        assert result["event"].event_type is EventType.READING_NEAR_THRESHOLD

    async def test_it_refuses_a_state_with_no_observation(self) -> None:
        """Rather than returning the state unchanged, which would look like a
        successful classification that never happened."""
        from curtail_agents.fleet import _sentinel

        with pytest.raises(ValueError):
            await _sentinel({})


class TestTheCeilingCarriesNoInventedMargin:
    """A first fix for the out-of-band problem padded this constant by 25
    percent. A review called the fraction unsupported, and reading
    `NodeRunner.run` showed the padding was in the wrong window entirely: the
    node timeout wraps `_run_node_loop`, while the completion flush and the
    failure-path enqueue happen after it has already been satisfied. More time
    for the graph body, none for the thing that could hang. The fraction is gone
    and this test is what keeps it gone.
    """

    def test_every_second_in_the_ceiling_is_accounted_for(self) -> None:
        """The excess over attempts and backoff is the counted child appends and
        nothing else. A fraction of the total would land here as an unexplained
        remainder, which is exactly what it was."""
        from curtail_agents.fleet import (
            CHILD_OUT_OF_BAND_APPENDS,
            SESSION_APPEND_BUDGET_SECONDS,
        )

        transient = 3
        attempts = NODE_TIMEOUT_SECONDS * (MAX_ATTEMPTS * transient + 1)
        backoff = (
            sum(
                min(MAX_DELAY_SECONDS, INITIAL_DELAY_SECONDS * BACKOFF_FACTOR**i)
                for i in range(MAX_ATTEMPTS - 1)
            )
            * transient
        )
        unexplained = GRAPH_TIMEOUT_SECONDS - attempts - backoff
        assert unexplained == pytest.approx(
            CHILD_OUT_OF_BAND_APPENDS * SESSION_APPEND_BUDGET_SECONDS
        )

    def test_no_overhead_fraction_survives_anywhere_in_the_module(self) -> None:
        """Named explicitly, because deleting a constant and leaving the number
        inlined somewhere would pass the test above by arithmetic accident."""
        import curtail_agents.fleet as fleet

        assert not hasattr(fleet, "OVERHEAD_MARGIN_FRACTION")


class TestTheDeadlineBoundsWhatTheNodeTimeoutCannot:
    """The out-of-band waits are bounded by CANCELLATION or by nothing.

    `Runner._consume_events` sets the signal a node waits on only after
    `session_service.append_event` returns and the yielded event has been taken
    by the consumer. If the append hangs, if it raises (the signal is then never
    set at all), or if the consumer stops reading, the node's `await
    processed.wait()` parks with no timeout of its own. No value of
    `Workflow.timeout` reaches it. `asyncio.wait_for` does, because it cancels.
    """

    async def test_it_interrupts_a_wait_that_is_never_signalled(self) -> None:
        """The exact failure shape, not a stand-in sleep."""
        import asyncio

        from curtail_agents.fleet import InvocationDeadlineError, run_with_deadline

        async def parked_on_an_append() -> None:
            never_signalled = asyncio.Event()
            await never_signalled.wait()

        with pytest.raises(InvocationDeadlineError):
            await run_with_deadline(parked_on_an_append(), deadline=0.05)

    async def test_the_parked_work_is_actually_cancelled(self) -> None:
        """A wrapper that returns while the task runs on has bounded the
        caller's patience, not the work. The distinction is the whole point."""
        import asyncio

        from curtail_agents.fleet import InvocationDeadlineError, run_with_deadline

        cancelled = False

        async def parked_on_an_append() -> None:
            nonlocal cancelled
            never_signalled = asyncio.Event()
            try:
                await never_signalled.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise

        with pytest.raises(InvocationDeadlineError):
            await run_with_deadline(parked_on_an_append(), deadline=0.05)
        assert cancelled, "the deadline expired without cancelling the parked await"

    async def test_work_inside_the_deadline_is_not_cut(self) -> None:
        """Non-vacuity. A wrapper that raised unconditionally would pass both
        tests above and be worthless."""
        from curtail_agents.fleet import run_with_deadline

        async def finishes() -> str:
            return "recommendation"

        assert await run_with_deadline(finishes(), deadline=5.0) == "recommendation"

    def test_the_deadline_exceeds_the_graph_ceiling(self) -> None:
        """At or below it, the deadline would pre-empt the retries the policy
        explicitly permits, converting recoverable slowness into a failure."""
        from curtail_agents.fleet import INVOCATION_DEADLINE_SECONDS

        assert INVOCATION_DEADLINE_SECONDS > GRAPH_TIMEOUT_SECONDS

    def test_the_allowance_is_derived_from_the_appends_that_were_counted(self) -> None:
        """Counted from `NodeRunner.run`, not chosen. One append per attempt of
        every child node sits inside the graph window and was missing from its
        derivation; one root append sits outside it entirely."""
        from curtail_agents.fleet import (
            INVOCATION_DEADLINE_SECONDS,
            ROOT_OUT_OF_BAND_APPENDS,
            RUNNER_LIFECYCLE_APPENDS,
            SESSION_APPEND_BUDGET_SECONDS,
        )

        outside_the_graph = ROOT_OUT_OF_BAND_APPENDS + RUNNER_LIFECYCLE_APPENDS
        assert INVOCATION_DEADLINE_SECONDS == pytest.approx(
            GRAPH_TIMEOUT_SECONDS + SESSION_APPEND_BUDGET_SECONDS * outside_the_graph
        )

    def test_the_graph_window_provisions_the_child_appends_it_contains(self) -> None:
        """A child's completion flush runs outside that CHILD's timeout and
        inside the root's, so the root ceiling has to pay for it. Omitting it
        under-provisioned the body by the full append budget of every attempt."""
        from curtail_agents.fleet import (
            CHILD_OUT_OF_BAND_APPENDS,
            SESSION_APPEND_BUDGET_SECONDS,
        )

        transient = 3
        attempts = NODE_TIMEOUT_SECONDS * (MAX_ATTEMPTS * transient + 1)
        backoff = (
            sum(
                min(MAX_DELAY_SECONDS, INITIAL_DELAY_SECONDS * BACKOFF_FACTOR**i)
                for i in range(MAX_ATTEMPTS - 1)
            )
            * transient
        )
        assert CHILD_OUT_OF_BAND_APPENDS == MAX_ATTEMPTS * transient + 1
        assert GRAPH_TIMEOUT_SECONDS == pytest.approx(
            attempts + backoff + CHILD_OUT_OF_BAND_APPENDS * SESSION_APPEND_BUDGET_SECONDS
        )

    async def test_an_inner_timeout_is_not_relabelled_as_our_deadline(self) -> None:
        """`wait_for` reports expiry with the same exception type a session
        client raises on its own timeout. Catching broadly would route a
        recoverable node failure into the unrecoverable-sounding path and report
        an elapsed deadline that never elapsed."""
        from curtail_agents.fleet import InvocationDeadlineError, run_with_deadline

        async def the_work_itself_times_out() -> None:
            raise TimeoutError("the session client gave up")

        with pytest.raises(TimeoutError) as caught:
            await run_with_deadline(the_work_itself_times_out(), deadline=30.0)
        assert not isinstance(caught.value, InvocationDeadlineError)
        assert "session client" in str(caught.value)


class TestTheDeadlineIsAppliedOnTheRealRunnerPath:
    """The helper is applied, not merely available.

    A review made the point that a guard nothing calls guards nothing, which is
    the same defect as the retry policy that once wrapped no-op functions. These
    tests run a real ADK `Workflow` through a real `Runner`, so what is proven is
    the production stack: NodeRunner drives the node, the node parks on an event
    that is never signalled, and the deadline cancels it.
    """

    @staticmethod
    def _parked_workflow(started: asyncio.Event) -> tuple[Workflow, dict[str, bool]]:
        """A real Workflow whose only node parks the way a stalled append does."""
        observed = {"cancelled": False}

        async def parks_forever(node_input: object) -> object:
            never_signalled = asyncio.Event()
            started.set()
            try:
                await never_signalled.wait()
            except asyncio.CancelledError:
                observed["cancelled"] = True
                raise
            return node_input

        graph = Workflow(
            name="parked",
            description="one node that never returns, to prove the deadline binds",
            edges=[Edge(from_node=START, to_node=node(parks_forever, name="parks_forever"))],
        )
        return graph, observed

    async def test_a_stalled_invocation_is_cancelled_on_the_runner_path(self) -> None:
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        from curtail_agents.fleet import InvocationDeadlineError, run_invocation

        started = asyncio.Event()
        graph, observed = self._parked_workflow(started)
        runner = InMemoryRunner(node=graph, app_name="curtail_deadline_test")
        await runner.session_service.create_session(
            app_name="curtail_deadline_test", user_id="watermaster", session_id="s1"
        )

        with pytest.raises(InvocationDeadlineError) as caught:
            await run_invocation(
                runner,
                user_id="watermaster",
                session_id="s1",
                new_message=types.Content(role="user", parts=[types.Part(text="go")]),
                deadline=0.5,
            )

        assert started.is_set(), "the node never ran, so nothing was proven"
        assert observed["cancelled"], "the deadline expired without cancelling the node"
        assert "stalled session write" in str(caught.value)

    def test_no_other_code_path_executes_the_graph_without_the_deadline(self) -> None:
        """The finding this closes is a SCOPE fact, so the guard is structural.

        A review said, twice and correctly, that the deadline has no production
        caller. It does not: the Core, Scribe and Herald handlers are unattached,
        so no application entrypoint exists to wrap yet, and fabricating one
        would repeat the mistake this file already documents. What CAN be done
        now is make the omission impossible to introduce quietly. `run_async` is
        how a Runner executes the graph, and the only sanctioned occurrence is
        inside `run_invocation`, which applies the deadline. Any other caller
        fails here on the commit that adds it.

        The file set includes untracked files deliberately: a guard resolving
        only `git ls-files` cannot see the file about to ship, which is a
        false green this project has already been burned by once.
        """
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()

        sanctioned = "agents/src/curtail_agents/fleet.py"
        offenders: list[str] = []
        found_sanctioned = False
        for rel in listed:
            if not rel.endswith(".py"):
                continue
            path = root / rel
            if not path.is_file():
                continue
            if ".run_async(" not in path.read_text(encoding="utf-8", errors="ignore"):
                continue
            if rel == sanctioned:
                found_sanctioned = True
            elif not rel.startswith("agents/tests/"):
                offenders.append(rel)

        assert found_sanctioned, (
            "no sanctioned run_async call found, so this scan would pass over an "
            "unguarded entrypoint without noticing"
        )
        assert not offenders, (
            f"these execute the graph outside run_invocation, so no deadline "
            f"bounds their session appends: {offenders}"
        )

    def test_compaction_is_not_enabled_behind_this_assumption(self) -> None:
        """RUNNER_LIFECYCLE_APPENDS counts one append and says so on the
        condition that event compaction stays off. Compaction appends in a loop
        whose length the App config decides, and no constant honestly bounds an
        unconfigured loop, so the assumption is asserted rather than trusted."""
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        # This file names the setting in order to look for it, so it excludes
        # itself. A scanner that matches its own text reports a defect that is
        # only the search term, which is a false RED and erodes the guard.
        this_file = Path(__file__).resolve().relative_to(root).as_posix()
        needle = "events_compaction" + "_config"
        enabling = [
            rel
            for rel in listed
            if rel.endswith(".py")
            and rel != this_file
            and (root / rel).is_file()
            and needle in (root / rel).read_text(errors="ignore")
        ]
        assert not enabling, (
            f"event compaction is configured in {enabling}, so the invocation "
            f"deadline no longer accounts for every Runner-level append"
        )

    async def test_the_user_message_append_the_budget_counts_actually_happens(self) -> None:
        """RUNNER_LIFECYCLE_APPENDS is a claim about ADK, so observe ADK.

        A mutation proved the arithmetic test blind here: setting the constant to
        zero left every assertion green, because those assertions recompute the
        deadline from the same constant they are checking. A number can only be
        wrong against reality, so this counts the appends a real Runner makes on
        a real invocation and compares the user-message ones to the constant.
        """
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        from curtail_agents.fleet import RUNNER_LIFECYCLE_APPENDS, run_invocation

        async def finishes(node_input: object) -> str:
            return "done"

        graph = Workflow(
            name="counted",
            description="counts the session appends a healthy invocation makes",
            edges=[Edge(from_node=START, to_node=node(finishes, name="finishes"))],
        )
        runner = InMemoryRunner(node=graph, app_name="curtail_append_count")
        await runner.session_service.create_session(
            app_name="curtail_append_count", user_id="watermaster", session_id="s1"
        )

        user_appends = 0
        original = runner.session_service.append_event

        async def counting_append(session: Session, event: Event) -> Event:
            nonlocal user_appends
            if event.author == "user":
                user_appends += 1
            return await original(session, event)

        runner.session_service.append_event = counting_append  # type: ignore[method-assign]

        await run_invocation(
            runner,
            user_id="watermaster",
            session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part(text="go")]),
            deadline=30.0,
        )

        assert user_appends == RUNNER_LIFECYCLE_APPENDS, (
            f"the Runner made {user_appends} user-message session appends before "
            f"the graph, but the invocation budget accounts for "
            f"{RUNNER_LIFECYCLE_APPENDS}"
        )

    async def test_a_healthy_invocation_completes_and_returns_its_events(self) -> None:
        """Non-vacuity on the same path. A wrapper that always raised would pass
        the test above and be worthless."""
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        from curtail_agents.fleet import run_invocation

        async def finishes(node_input: object) -> str:
            await asyncio.sleep(0)
            return "recommendation drafted"

        graph = Workflow(
            name="healthy",
            description="completes immediately",
            edges=[Edge(from_node=START, to_node=node(finishes, name="finishes"))],
        )
        runner = InMemoryRunner(node=graph, app_name="curtail_deadline_ok")
        await runner.session_service.create_session(
            app_name="curtail_deadline_ok", user_id="watermaster", session_id="s1"
        )

        events = await run_invocation(
            runner,
            user_id="watermaster",
            session_id="s1",
            new_message=types.Content(role="user", parts=[types.Part(text="go")]),
            deadline=30.0,
        )
        assert events, "no events came back, so the drain proved nothing"
