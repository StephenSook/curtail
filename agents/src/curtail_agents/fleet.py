"""The fleet graph, with the loop breaker attached to real nodes.

This closes a caveat that was standing in `routing.py`: its docstring described
`RetryConfig` and `NodeTimeoutError` as layer three while both were dead
constants. A guard described in prose and absent from the code is the drift this
project audits for, and it was committed in the file whose subject is not
trusting assertions. The constants are now attached to nodes in a graph.

**Retry is not applied uniformly, and the asymmetry is the point.**

| Node | Retries | Why |
|---|---|---|
| Gage Sentinel | yes | a USGS poll fails on transport, and transport recovers |
| Allocation Core | NO | deterministic. Same input, same output, forever |
| Order Scribe | yes | a model call fails transiently, and a rejected
  draft can be re-drafted with the violation fed back |
| Herald | yes | delivery is a network act against systems we do not control |

**Retrying a deterministic node is worse than useless.** The Allocation Core is
pure arithmetic over a rights table: if it raised, it will raise again on the
identical input, so a retry burns the budget and delays the human who needs to
see the failure. Worse, a retry policy on a deterministic node quietly implies
its failures are transient, which invites treating a genuine data defect as
noise. Failures there are surfaced immediately, not smoothed over.

**What is wired, stated plainly, because a graph of no-op nodes carries a retry
policy that governs nothing.** All four handlers are attached. The Sentinel calls
`sentinel.evaluate`, the Core calls `allocation.recommend` over the Board's own
rights table, the Scribe sanitizes and then calls `scribe.draft_order` through the
model, and the Herald calls `herald.deliver_order` down whichever lane the verb
requires. What is NOT wired is the delivery vendor: there is no certified mail
integration and no email provider, so a request selects the labelled synthetic
transport and every report it produces says `synthetic`.

Each of those three was a placeholder in turn, and each stated a blocker that had
gone stale before it was removed. The Core's said it needed a rights table that
Attachment A had already provided. The Scribe's said it needed a model that
`scribe.py` had already reached. The Herald's read a transport out of a payload key
nothing could populate, because a payload is built from session state and a
callable cannot be persisted, so `deliver_order` had never once run with a
transport. **A stub whose stated blocker is gone is a claim about the system that
has quietly stopped being true**, and the tell in all three cases was that the
blocker named something already sitting in the repository.

**The graph IS invoked, by `app.evaluate_reading`, and getting there found a
defect worth more than the argument that preceded it.** Four review passes said
the deadline below had no production caller. Three times the answer was that no
entrypoint could exist while the Core has no rights table. That was half true and
the wrong half mattered: the Sentinel is real, so a real invocation classifying a
real gage reading was available the whole time. Building it immediately exposed
that `build_curtailment_graph()` **could not be executed at all**, because ADK
binds node parameters by NAME from session state and the nodes declared a single
`state` parameter that matched no key. Every test asserted the graph's shape;
none asserted that it runs. Trying to run it is the only thing that could have
found that, and it is why "it has no caller" was worth taking seriously rather
than answering a fourth time.

`run_invocation` remains the only sanctioned execution path, and a CI guard fails
on the commit that adds any other `run_async` caller, including one in a file not
yet tracked by git.

**A timeout is not a slower attempt limit.** A model that loops does not fail, it
simply never returns, so an attempt ceiling alone never fires. `timeout` bounds
wall time and raises `NodeTimeoutError`, which is a different failure with a
different meaning, and only the pair covers both shapes.

**And a node timeout does not bound a node.** ADK applies it around
`_run_node_loop`; the completion flush and the failure-path event enqueue run
after that wrapper returns, and both can park indefinitely on a session append.
So the loop breaker is three things, not two: an attempt ceiling, a per-node wall
clock, and `run_with_deadline`, which is the only one of the three that can
interrupt a parked await, because it is the only one that cancels.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.workflow import START, Edge, RetryConfig, Workflow, node
from google.genai import types

from curtail_agents.herald import (
    Recipient,
    TransportResult,
    deliver_order,
    synthetic_transport,
)
from curtail_agents.messaging import Channel
from curtail_agents.model_armor import screen_document
from curtail_agents.routing import (
    BACKOFF_FACTOR,
    INITIAL_DELAY_SECONDS,
    JITTER,
    MAX_ATTEMPTS,
    MAX_DELAY_SECONDS,
    NODE_TIMEOUT_SECONDS,
)
from curtail_agents.sanitize import check_document_size, sanitize_document
from curtail_agents.scribe import draft_order
from curtail_agents.sentinel import Observation, SentinelError, evaluate
from curtail_core.allocation import Recommendation, RecommendedAction, recommend
from curtail_core.backtest import direction_for
from curtail_core.clocks import RecipientClass
from curtail_core.order_parser import OrderAction
from curtail_core.rights import rights_for
from curtail_core.rights_record import RightsRecordUnavailableError

#: Node names, used in the graph, in the policy table and quoted in the README.
#:
#: Named constants rather than string literals at the call sites, because an
#: edge referring to a node that does not exist is a graph that fails at
#: assembly, and a typo in a literal is exactly how that happens.
SENTINEL = "gage_sentinel"
CORE = "allocation_core"
SCRIBE = "order_scribe"
HERALD = "herald"

#: The retry policy for nodes whose failures are genuinely transient.
#:
#: Built from the constants `routing.py` declares, so the escalation threshold
#: the guard applies and the attempts the graph permits cannot drift apart. If
#: they did, the guard would escalate a draft the graph was still retrying.
TRANSIENT_RETRY = RetryConfig(
    max_attempts=MAX_ATTEMPTS,
    initial_delay=INITIAL_DELAY_SECONDS,
    max_delay=MAX_DELAY_SECONDS,
    backoff_factor=BACKOFF_FACTOR,
    jitter=JITTER,
)

#: The Sentinel's retry, scoped to the failures that are actually transient.
#:
#: **The Sentinel has two failure modes and only one of them is worth retrying.**
#: A `GageError` is a USGS poll that failed on transport, and transport recovers.
#: A `SentinelError` is the flow schedule REFUSING, because no minimum is encoded
#: for that basin in that regulatory era, and that answer is identical on every
#: attempt. Retrying it is the exact thing this module argues against for the
#: Allocation Core: it burns the budget, delays the human who has to encode the
#: missing table, and quietly implies the failure was a blip.
#:
#: Found by making the graph runnable and watching a refusal take three attempts
#: and two backoff sleeps to surface. The principle was already written down here;
#: it simply had no failing case to apply to until the graph could run.
#:
#: ADK matches these by exception class NAME with no subclass walking, so a name
#: that is wrong or a subclass that is unlisted means NO retry. That is the
#: fail-safe direction: an unrecognised failure surfaces to a human immediately
#: rather than being smoothed over.
SENTINEL_RETRY = TRANSIENT_RETRY.model_copy(
    update={"exceptions": ["GageError", "TimeoutError", "NodeTimeoutError"]}
)


#: What one out-of-band session append is allowed to take.
#:
#: A DEADLINE, not a prediction. Read `Runner._consume_events`: a non-partial
#: event is enqueued and the producing node then waits on an `asyncio.Event` that
#: the runner loop sets only after `session_service.append_event` returns AND the
#: yielded event has been taken by the consumer. Three separate things can make
#: that wait unbounded, and only the first is the one people expect:
#:
#: 1. `append_event` hangs, for example a stalled Cloud SQL connection.
#: 2. `append_event` RAISES, in which case `processed.set()` is never reached at
#:    all and the waiting node is left parked forever rather than failed.
#: 3. The consumer is slow. The signal is set after `yield output_event`, so a
#:    stalled SSE reader applies backpressure all the way back into node
#:    execution. That one is OURS to avoid, not ADK's, and it is the reason the
#:    console must never let a browser stall the event loop it is reading.
#:
#: Thirty seconds is far past a healthy append and is the point where surfacing a
#: stall beats waiting on it. A curtailment order parked behind a session write
#: is worse than one that failed loudly, because the dead-letter path can
#: retry a failure and cannot retry a hang.
SESSION_APPEND_BUDGET_SECONDS = 30.0

#: Out-of-band appends INSIDE the graph body, counted from `NodeRunner.run`.
#:
#: Exactly one per attempt of every child node: an error event when the attempt
#: raises, or `_flush_output_and_deltas` when it succeeds and the loop ends. Both
#: sit outside that child's own `timeout`, and both sit inside the root
#: workflow's, because the root's timed body IS the graph loop that drives them.
#:
#: A review flagged this and it was right in a sharper form than it stated: these
#: appends were never OUTSIDE the graph window, they were missing from the graph
#: window's DERIVATION, so the ceiling under-provisioned its own body. Retrying
#: nodes can make MAX_ATTEMPTS of them; the deterministic Core makes one, because
#: it does not retry.
CHILD_OUT_OF_BAND_APPENDS = MAX_ATTEMPTS * 3 + 1

#: Out-of-band appends OUTSIDE the graph body, at the root node run.
#:
#: One, not two. The success path flushes and the failure path enqueues an error
#: event, and they are mutually exclusive within a single attempt; the root
#: workflow carries no retry policy, so it runs once. Counted rather than rounded
#: up, because a padded count is the same defect as a padded fraction.
ROOT_OUT_OF_BAND_APPENDS = 1

#: Runner-lifecycle appends, which belong to the invocation but to no node.
#:
#: One: `Runner.run_async` appends the user message to the session BEFORE the
#: root node starts, so it is inside the deadline and outside every node window.
#: A review caught this and it is exactly the kind of omission that produces a
#: false timeout: a healthy traversal spends its whole graph budget, then the
#: deadline fires during work the budget never accounted for.
#:
#: **Event compaction is a second such path and is deliberately NOT counted**,
#: because it appends in a loop whose length depends on the App configuration and
#: no honest constant bounds an unconfigured loop. Curtail does not enable
#: compaction. If it ever does, this count moves, and the assertion in
#: `test_compaction_is_not_enabled_behind_this_assumption` is what will notice.
RUNNER_LIFECYCLE_APPENDS = 1


#: Worst-case wall time for the graph body, computed rather than guessed.
#:
#: A review caught the original: `NODE_TIMEOUT_SECONDS * 4` assumed each node ran
#: ONCE. With three attempts allowed on three transient nodes the attempts alone
#: reach 1200 seconds, so a 480 second ceiling silently truncated the retries it
#: was meant to accommodate, converting recoverable upstream slowness into a
#: workflow failure and preventing delivery.
#:
#: Derived from the same constants the policy uses, so raising MAX_ATTEMPTS
#: cannot leave the ceiling behind.
#:
#: **A 25 percent margin was added here and then removed, and the removal is the
#: instructive part.** It was called unsupported, and reading `NodeRunner.run`
#: showed it was worse: the per-node timeout wraps `_run_node_loop`, so padding
#: this constant hands more time to the part that was never at risk. The right
#: repair was not a better fraction. It was to count the appends and then bound
#: what no constant here can reach, which is `run_with_deadline` below.
def _worst_case_seconds() -> float:
    transient_nodes = 3  # sentinel, scribe, herald
    deterministic_nodes = 1  # core, one attempt only
    attempts = NODE_TIMEOUT_SECONDS * (MAX_ATTEMPTS * transient_nodes + deterministic_nodes)
    backoff = (
        sum(
            min(MAX_DELAY_SECONDS, INITIAL_DELAY_SECONDS * BACKOFF_FACTOR**i)
            for i in range(MAX_ATTEMPTS - 1)
        )
        * transient_nodes
    )
    child_appends = CHILD_OUT_OF_BAND_APPENDS * SESSION_APPEND_BUDGET_SECONDS
    return attempts + backoff + child_appends


GRAPH_TIMEOUT_SECONDS = _worst_case_seconds()

#: The deadline an application entrypoint enforces around a whole invocation.
#:
#: The graph ceiling plus every append the graph ceiling provably does not cover:
#: the root node's own, and the Runner's user-message append. Strictly greater
#: than GRAPH_TIMEOUT_SECONDS, because a deadline at or below it would pre-empt
#: the retries the policy explicitly permits.
INVOCATION_DEADLINE_SECONDS = GRAPH_TIMEOUT_SECONDS + SESSION_APPEND_BUDGET_SECONDS * (
    ROOT_OUT_OF_BAND_APPENDS + RUNNER_LIFECYCLE_APPENDS
)


class InvocationDeadlineError(TimeoutError):
    """A traversal exceeded the wall-clock deadline and was cancelled."""


async def run_with_deadline[T](
    awaitable: Awaitable[T],
    *,
    deadline: float = INVOCATION_DEADLINE_SECONDS,
) -> T:
    """Run one invocation under a deadline that actually binds.

    **This is the mechanism, and the constant above is only its argument.** No
    value of `Workflow.timeout` can bound the waits described in
    SESSION_APPEND_BUDGET_SECONDS, because they happen outside the window that
    setting controls. `asyncio.wait_for` bounds them because it CANCELS, and
    `await processed.wait()` is an ordinary cancellable await point, so the
    cancellation reaches the parked task no matter which of the three causes
    parked it.

    Enforced here, at a call site we own, rather than requested from a library
    that does not offer it. A guard that has to be granted by somebody else is
    not a guard.

    A deadline can cut work that would have completed, and that is the accepted
    trade rather than an oversight: the alternative is an unbounded hang on a
    legal document, and a cut traversal is recoverable through the retry and
    dead-letter path while a hang is not.

    **`asyncio.timeout` rather than `asyncio.wait_for`, and the difference is a
    misclassification bug a review caught.** `wait_for` reports expiry by raising
    `TimeoutError`, which is indistinguishable from a `TimeoutError` raised BY the
    work itself: a session client, an HTTP call, a database driver. Catching
    broadly would relabel a recoverable node-level timeout as a whole-invocation
    deadline and route it into the wrong recovery path while reporting an elapsed
    time that never elapsed. `Timeout.expired()` answers which one happened, so an
    inner timeout propagates untouched.
    """
    try:
        async with asyncio.timeout(deadline) as scope:
            return await awaitable
    except TimeoutError as exc:
        if not scope.expired():
            # Raised by the work, not by us. Reclassifying it here would hide a
            # recoverable failure inside an unrecoverable-sounding one.
            raise
        raise InvocationDeadlineError(
            f"invocation exceeded {deadline} seconds and was cancelled. The graph "
            f"body is bounded at {GRAPH_TIMEOUT_SECONDS} seconds, which includes "
            f"{CHILD_OUT_OF_BAND_APPENDS} child session appends; the remainder is "
            f"{ROOT_OUT_OF_BAND_APPENDS + RUNNER_LIFECYCLE_APPENDS} appends outside "
            f"the graph, budgeted at {SESSION_APPEND_BUDGET_SECONDS} seconds each. "
            f"Suspect a stalled session write or a consumer that stopped reading "
            f"events."
        ) from exc


async def run_invocation(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    new_message: types.Content | None = None,
    state_delta: dict[str, Any] | None = None,
    deadline: float = INVOCATION_DEADLINE_SECONDS,
) -> list[Event]:
    """Drive one ADK Runner invocation to completion, under the deadline.

    **The invocation boundary, and the reason `run_with_deadline` is not merely
    available but applied.** A review pointed out that a helper nothing calls
    protects nothing, which is the same structure-present-force-absent defect
    that put a retry policy on a graph of no-op functions. So the deadline lives
    on the path itself: draining the runner's event stream is what an application
    does with this graph, and it cannot be done here without the bound.

    Draining rather than yielding, deliberately. An async generator would hand
    the caller the ability to stop consuming midway, and cause 3 in
    SESSION_APPEND_BUDGET_SECONDS is precisely a consumer that stops consuming
    applying backpressure into node execution. A caller that wants to stream
    events to a console can do so from the returned list, or take the same
    deadline around its own loop, but it will not get an unbounded one from here.
    """

    async def _drain() -> list[Event]:
        return [
            event
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=new_message,
                state_delta=state_delta,
            )
        ]

    return await run_with_deadline(_drain(), deadline=deadline)


def policy_for(node_name: str) -> NodePolicy:
    """The retry and timeout policy for one node.

    Raises rather than returning a default. An unknown node name means the graph
    and the policy table have drifted apart, and silently handing back "no
    retry, no timeout" would give that node the weakest possible behaviour at
    the moment nobody is looking.
    """
    if node_name not in NODE_POLICY:
        raise KeyError(
            f"no policy for node {node_name!r}. The graph and the policy table "
            f"have drifted; known nodes are {sorted(NODE_POLICY)}."
        )
    return NODE_POLICY[node_name]


#: Per-node policy, as data rather than as decorator calls.
#:
#: Kept separate from the functions so the policy is reviewable in a diff and
#: testable without constructing an agent or touching a model. The judged claim
#: is the policy; the wiring is mechanical.
@dataclass(frozen=True, slots=True)
class NodePolicy:
    """Retry and timeout for one node, plus the reason, as data.

    Typed rather than a dict of `object`, because the values are handed straight
    to ADK's `node()` overloads and an untyped mapping defeats the check that
    would catch a wrong one. The rationale travels WITH the values so a reviewer
    finds the reason beside the setting rather than in a commit message they
    will never read.
    """

    retry_config: RetryConfig | None
    timeout: float
    rationale: str


NODE_POLICY: dict[str, NodePolicy] = {
    SENTINEL: NodePolicy(
        retry_config=SENTINEL_RETRY,
        timeout=NODE_TIMEOUT_SECONDS,
        rationale=(
            "A USGS poll fails on transport, and transport recovers. Retrying is "
            "correct here and the jitter matters: without it, both basin pollers "
            "retry in lockstep against an API that just failed. SCOPED, though: a "
            "SentinelError is the flow schedule refusing because no minimum is "
            "encoded for that era, which is deterministic and must surface at "
            "once rather than after three attempts and two backoff sleeps."
        ),
    ),
    CORE: NodePolicy(
        retry_config=None,
        timeout=NODE_TIMEOUT_SECONDS,
        rationale=(
            "DELIBERATELY NO RETRY. The Allocation Core is deterministic: the "
            "same rights table and the same reading produce the same "
            "recommendation, so a failure will reproduce exactly and a retry "
            "only delays the human who needs to see it. A retry policy here "
            "would also imply its failures are transient, which invites "
            "treating a data defect as noise. It keeps a timeout, because a hang "
            "is a different failure from an exception."
        ),
    ),
    SCRIBE: NodePolicy(
        retry_config=TRANSIENT_RETRY,
        timeout=NODE_TIMEOUT_SECONDS,
        rationale=(
            "The only node calling a model. Retries cover a transient API "
            "failure AND the guard's retry-once path, where a rejected draft is "
            "re-drafted with the violation fed back. The timeout is what catches "
            "a model that loops, which an attempt ceiling never sees because a "
            "looping call does not fail, it simply never returns."
        ),
    ),
    HERALD: NodePolicy(
        retry_config=TRANSIENT_RETRY,
        timeout=NODE_TIMEOUT_SECONDS,
        rationale=(
            "Delivery is a network act against systems we do not control. This "
            "is the Loop pattern the README names, and what survives every "
            "attempt goes to a dead letter with its correlation ID intact, "
            "because a notice nobody received must never be recorded as sent."
        ),
    ),
}


def _build_node(name: str, fn: Callable[..., Any]) -> Any:
    """Construct a node FROM the policy table, never alongside it.

    The single most important line in this module. A review proved the previous
    shape unsafe: the decorators carried their own arguments while NODE_POLICY
    was a parallel data table, and the tests asserted the table. Changing
    `@node(name=SCRIBE, retry_config=TRANSIENT_RETRY)` to `retry_config=None`
    left every test GREEN while the Scribe silently stopped retrying.

    That is a test asserting a claim rather than a reality, in the file whose
    stated purpose is preventing exactly that. Deriving the node from the table
    makes the two unable to disagree, which is stronger than any test comparing
    them.
    """
    policy = policy_for(name)
    return node(
        fn,
        name=name,
        retry_config=policy.retry_config,
        timeout=policy.timeout,
    )


#: The key a caller puts untrusted document text under, and the key the Scribe
#: emits the safe form under. Two different names on purpose: a single key would
#: let a downstream reader believe it had raw text when it had fenced text, or the
#: reverse, and the whole point of this node is that those must never be confused.
#:
#: **The production path supplies this, and the route it takes was the whole
#: question.** A note here previously called it an unresolved design tension: ADK
#: offers two channels into a graph, session state and the user message, and this
#: node's invariant forbids the first. What it did not say is that the second had
#: never been tried. It took one probe. The user message arrives at the first node
#: as `node_input` with its parts intact, so `app.evaluate_reading` puts the
#: document in a marked message part, the Sentinel lifts it into the payload, and
#: session state never holds it.
#:
#: The raw text does land in the session EVENT log, and that is correct rather than
#: a leak: the event log is the immutable record of what actually arrived, which is
#: precisely what auditing a poisoned document requires. State is the dangerous
#: store because nodes bind parameters from it BY NAME, so a node can read state by
#: accident and cannot read the event log by accident.
#:
#: Recorded because "unresolved tension" was the wrong label for "I did not test
#: the other option", and the difference between those two is a minute of work.
RECOMMENDATION = "recommendation"
RECOMMENDATION_UNAVAILABLE = "recommendation_unavailable"
RECIPIENTS = "recipients"
DELIVERY = "delivery"
DELIVERY_UNAVAILABLE = "delivery_unavailable"
DRAFT = "draft"
DRAFT_UNAVAILABLE = "draft_unavailable"
ARTIFACT_ACTION = "artifact_action"
SOURCE_DOCUMENT = "source_document"
PROMPT_BLOCK = "prompt_block"
INJECTION_HITS = "injection_hits"

#: Model Armor's verdict on the untrusted document, layer 2 of the injection defence.
#:
#: Three keys rather than one, because "did it run" and "did it match" are different
#: questions and collapsing them is how an unavailable layer comes to read as a pass.
ARMOR_STATE = "model_armor_state"
ARMOR_REASON = "model_armor_reason"
ARMOR_FILTERS = "model_armor_filters"

#: Names a registered transport. A NAME, never a callable, and that is forced.
#:
#: The previous key held the transport itself, read with `node_input.get(TRANSPORT)`,
#: and nothing could ever have populated it. A payload originates from the first
#: node's return value, which is built from session state, and session state is
#: persisted by a real session service, so a function cannot travel there. The key
#: was unreachable by construction, which is why `deliver_order` never once ran with
#: a transport and every delivery on this path returned None.
#:
#: A name resolves through the registry below, so the only transports reachable
#: from a request are the ones this module deliberately registered.
TRANSPORT_NAME = "transport_name"

#: True when the resolved transport was the labelled stand-in.
SYNTHETIC_TRANSPORT = "synthetic_transport"

#: The only transports a request may select, by name.
#:
#: **Absent means no transport, which makes `deliver_order` refuse.** That is the
#: fail-closed direction and it is deliberate: a delivery recorded but never made is
#: what a reviewing court would read as proof a party was served. An unknown name is
#: therefore not an error to be smoothed over with a default, it is a request for a
#: vendor nobody wired.
#:
#: `synthetic` is the demonstration stand-in and every report it produces carries
#: `synthetic=True`, so no surface can present it as real delivery. Only notification
#: contacts may be synthetic in this project, and they must be visibly labelled.
TRANSPORTS: dict[str, Callable[[], Callable[[Recipient, str], TransportResult]]] = {
    "synthetic": synthetic_transport,
}


class UndeliverableRecommendationError(RuntimeError):
    """A recommendation that implies no distributable artifact reached the Herald.

    Distinct from a delivery failure, and the distinction matters to the reader. A
    failure means an artifact existed and did not arrive. This means there was
    nothing to send, which is an ordinary and correct outcome rather than a fault.
    """


#: What a DRAFT built from each recommendation would DO, as the parser's verb.
#:
#: **Derived, never supplied by the caller, and that closes the last hole in the lane
#: rule.** Herald's contract is that the lane follows from what the artifact IS. If a
#: request could name the verb, a caller could hand an imposing order the verb
#: `suspend` and route a document requiring legal service onto a mailing list, which
#: is precisely the confusion the two state machines exist to prevent.
#:
#: This is NOT the official's determination and must never be read as one. The Core
#: recommends that curtailment be considered; a human decides whether to issue. What
#: this table says is narrower and honest: IF a draft is produced from this
#: recommendation, this is the verb that draft carries, and therefore this is the
#: service obligation the officer is being asked to authorise.
#:
#: The two absent members are absent on purpose. `NO_ACTION` and
#: `FIELD_VERIFICATION_FIRST` imply no order at all, and a near-threshold reading
#: flagged for field verification is a request for a human to go and measure, not a
#: document anybody is served with.
_ARTIFACT_ACTION_FOR: dict[RecommendedAction, OrderAction] = {
    RecommendedAction.CONSIDER_CURTAILMENT: OrderAction.IMPOSE,
    RecommendedAction.CONSIDER_SUSPENSION: OrderAction.SUSPEND,
}


#: Marks the message part carrying untrusted document text.
#:
#: **This is how a document enters the graph without entering session state**, and
#: finding it dissolved a design tension recorded here as unresolved. The claim was
#: that ADK offers only two ways in, session state or the user message, and that
#: state was forbidden by the Scribe's invariant. True, but the second option was
#: never tested. A probe settled it: `run_async(new_message=...)` delivers the
#: `Content` to the first node as `node_input`, parts intact.
#:
#: The raw text does land in session EVENTS, and that is the right place for it. The
#: event log is the immutable record of what actually arrived, which is exactly what
#: an audit of a poisoned document needs. What matters is that it is not in session
#: STATE, because state is what nodes bind parameters from by name, so a node can
#: read state by accident and cannot read the event log by accident.
DOCUMENT_PART_PREFIX = "SOURCE_DOCUMENT:\n"


def _document_from_message(node_input: Any) -> str | None:
    """Pull untrusted document text out of the user message, if it carried any.

    Identified by an explicit prefix rather than by part position, so a reordered
    or re-hydrated message cannot silently turn the instruction part into the
    document part, which would feed the wrong text to the sanitizer and leave the
    real document unscanned.
    """
    parts = getattr(node_input, "parts", None)
    if not parts:
        return None
    carried = [
        part.text.removeprefix(DOCUMENT_PART_PREFIX)
        for part in parts
        if getattr(part, "text", None) and part.text.startswith(DOCUMENT_PART_PREFIX)
    ]
    return "\n".join(carried) if carried else None


async def _sentinel(
    observation: Observation,
    correlation_id: str = "",
    recent: tuple[Observation, ...] = (),
    node_input: Any = None,
) -> dict[str, Any]:
    """Classify a gage reading using the real Sentinel, and carry any document.

    **Parameters are named for the session-state keys they bind to, which is
    ADK's actual contract and not a style choice.** `FunctionNode._bind_parameters`
    resolves each parameter BY NAME out of `ctx.state`, so the previous signature,
    a single `state: dict`, made the node unrunnable: ADK looked for a state key
    literally called `state`, found none, and raised before the body was ever
    entered. Every test asserted the graph's SHAPE and none asserted that it runs,
    so a graph that could not execute at all passed a suite whose stated subject
    is guards that are attached rather than described. It was found by trying to
    run it, which is the only thing that could have found it.

    `node_input` is the one parameter name ADK fills with the message rather than
    from state, which is what makes it the channel for untrusted document text: the
    document rides the message into the payload and never touches session state, so
    the Scribe's invariant holds all the way from the entrypoint.

    The type hints are load-bearing too. Session state has to survive a real
    session service, so the entrypoint puts a plain JSON-safe dict in state and
    ADK coerces it to `Observation` here through the annotation.
    """
    event = evaluate(observation, correlation_id=correlation_id, recent=tuple(recent))
    # The DIRECTION travels with the classification, and that is not cosmetic.
    #
    # A review found the eval set expecting a direction while this node answered
    # only with a classification, so no case it contained could ever match: the
    # artifact would have scored zero the moment a judge model ran, and a zero from
    # a category error is indistinguishable from a zero from a broken agent. The
    # classification is what the Sentinel is FOR; the direction is what it can be
    # compared against, and both belong in the answer.
    carried: dict[str, Any] = {
        "event": event,
        "correlation_id": correlation_id,
        "direction": direction_for(event.observed_cfs, event.minimum_cfs).value,
    }
    document = _document_from_message(node_input)
    if document is not None:
        carried[SOURCE_DOCUMENT] = document
    return carried


async def _core(node_input: dict[str, Any]) -> dict[str, Any]:
    """Compute the recommendation. Deterministic, so it does not retry.

    Wired now. Its placeholder said it needed a rights table the console would supply,
    and that reason went stale the moment Attachment A was parsed: `load_rights` reads
    the Board's own table and the same conversion the local parse used. A stub whose
    stated blocker has been removed is a claim about the system that is no longer true.

    Refuses for a basin with no ingested table rather than running on an empty list. An
    empty rights list is a valid input to `recommend` and produces a well-formed
    recommendation that reaches nobody, which reads exactly like a real answer.
    """
    event = node_input.get("event")
    if event is None:
        raise SentinelError(
            "the Core received no gage event. Refusing rather than recommending from "
            "nothing: a recommendation with no reading behind it is indistinguishable "
            "from one computed on a real river."
        )

    # One question, asked through the dispatcher, so this node does not have to know
    # which of the two records holds which basin. The dispatcher REFUSES on an empty
    # result rather than returning one, and that refusal is caught here and carried
    # forward, because the node's judgement about what to do with a gap is different
    # from the loader's judgement about whether a gap exists.
    try:
        loaded = rights_for(event.basin)
        in_basin, loaded_document = list(loaded.rights), loaded.document
    except RightsRecordUnavailableError as exc:
        in_basin, loaded_document = [], str(exc)
    if not in_basin:
        # A STATED GAP, carried forward, not an abort and not an empty recommendation.
        #
        # Two failures are available here and they pull in opposite directions.
        # Recommending from an empty rights list produces a well-formed answer reaching
        # nobody, which reads exactly like a real one. Aborting the invocation throws
        # away the Sentinel's classification, which is valid and useful on its own: a
        # Scott gage crossing a legally operative minimum is a real event whether or not
        # this system has ingested that basin's rights table yet.
        #
        # So the recommendation is None with a reason attached, and every downstream
        # consumer has to look at the reason rather than at a silence.
        return {
            **node_input,
            RECOMMENDATION: None,
            RECOMMENDATION_UNAVAILABLE: (
                f"no rights table could be loaded for the {event.basin.value} basin, so "
                f"no allocation can be computed. {loaded_document} "
                "The gage classification above stands on its own."
            ),
        }

    recommendation = recommend(
        basin=event.basin,
        when=event.observed_at.date(),
        observed_cfs=event.observed_cfs,
        rights=in_basin,
    )
    return {**node_input, RECOMMENDATION: recommendation}


class UntrustedTextInSessionStateError(RuntimeError):
    """Raw document text was found in shared session state. The node refuses to run.

    **A boundary that only covers one channel is not a boundary**, and a review
    proved this one did not. Omitting a key from a node's return value removes it
    from the payload passed downstream; it does NOT remove it from ADK session
    state, which every later node can read by simply declaring a parameter of that
    name. A probe confirmed it: a raw document placed in state was read back
    verbatim by a downstream node while the Scribe, which binds `node_input`, never
    saw it and reported a clean sanitisation.

    So this fails CLOSED rather than sanitising the copy it can reach and leaving
    the other readable. Untrusted document text in shared state is a caller design
    error, and a guard that quietly does half its job is worse than one that stops.
    """


async def _scribe(
    ctx: Any, node_input: dict[str, Any], produce_order: bool = False
) -> dict[str, Any]:
    """Sanitize any carried document, then draft the order through the model.

    The only node that calls a model, so the only one that can loop rather than
    fail, which is why the timeout matters here most.

    **The drafting half was missing and the docstring said so for several
    revisions: "NOT YET WIRED to a model".** That was true and it made the node a
    sanitizer wearing a drafter's name, so the graph could not produce an order at
    all and `draft_order` was reachable only from a separate HTTP handler that
    bypassed the fleet entirely. A node named for an act it does not perform is the
    same drift this module keeps finding, one layer up.

    Two halves, kept as two functions on purpose. Sanitisation must hold whether or
    not a draft is ever produced, and folding them together would mean every test of
    the injection boundary had to stand up a model.

    **`produce_order` is what the invocation ASKED FOR, and it is not a feature
    flag.** Classifying a reading and producing an order are different jobs: the
    eval set asks the fleet which way a reading points, and drafting a legal document
    to answer that would burn a model call, add a failure mode, and answer a question
    nobody asked. Every entrypoint sets it explicitly rather than inheriting a
    default, so no path can quietly skip the drafting it meant to request. When it is
    false the node says so in `DRAFT_UNAVAILABLE` rather than leaving an absence a
    reader has to interpret.
    """
    return await _draft(_sanitize(ctx, node_input), produce_order=produce_order)


def _sanitize(ctx: Any, node_input: dict[str, Any]) -> dict[str, Any]:
    """Fence any untrusted document text, on both channels, before a model sees it.

    **The sanitizer runs HERE, on the node, not only in the drill.** A review found
    it implemented, tested, demonstrated, and called by nothing on the fleet path,
    which is the structure-present-force-absent shape this module has now hit three
    times. Order text is untrusted input: fetched from a government web server, put
    through OCR, and destined for a prompt. It is sanitized and fenced at the node
    boundary, the raw text is dropped from the payload, and session state is checked
    because dropping it from the payload alone was a boundary with a hole in it.

    Hits travel with the output. A document trying to steer the drafter is evidence
    a watermaster should see, not something to clean up quietly.
    """
    # EVERY reserved key, on BOTH channels.
    #
    # The first version of this guard checked one key in state and two in the
    # payload, and a review found the gap immediately: an attacker-supplied
    # `prompt_block` sitting in session state was accepted, because the node only
    # looked for it in the payload, and any downstream node reading state would then
    # consume a block this sanitizer never touched.
    #
    # That is the same defect as the one it had just fixed, one key over, which is
    # the tell that a per-key check was the wrong shape. Enumerating the keys and
    # the channels together makes adding a key without guarding it require deleting
    # a line rather than forgetting one.
    #
    # `in` on ADK's State, not dict(...): State implements __contains__ but is not
    # dict-convertible, and dict() on it raised KeyError inside the guard itself.
    for reserved in (SOURCE_DOCUMENT, PROMPT_BLOCK, INJECTION_HITS):
        if reserved in ctx.state:
            raise UntrustedTextInSessionStateError(
                f"{reserved!r} is in session state, where every later node can read "
                "it without this node's involvement. Untrusted document text and the "
                "sanitized block both travel in the node payload, never in shared "
                "state, because state is readable by nodes that never crossed this "
                "boundary."
            )

    # A pre-populated output key would let a caller hand the model a block of its own
    # choosing while this node reported success, which is the sanitizer bypassed by
    # the one route it does not inspect.
    for reserved in (PROMPT_BLOCK, INJECTION_HITS):
        if reserved in node_input:
            raise UntrustedTextInSessionStateError(
                f"{reserved!r} arrived pre-populated. Only this node may produce it, "
                "because a caller supplying it directly bypasses sanitisation while "
                "the output still looks sanitized."
            )

    forwarded = dict(node_input)
    if SOURCE_DOCUMENT in node_input:
        raw = node_input[SOURCE_DOCUMENT]
        if not isinstance(raw, str):
            raise TypeError(
                f"{SOURCE_DOCUMENT} must be text, got {type(raw).__name__}. A non-string "
                "here means some caller is passing a parsed object through the untrusted "
                "channel, and the sanitizer would silently do nothing to it."
            )

        check_document_size(raw)
        sanitized = sanitize_document(raw)

        # LAYER 2. Google's own guidance is never to rely on a single defensive
        # measure, and this row read "provisioned, NOT called by the shipped Scribe"
        # for weeks. It is called now.
        #
        # Screened on the RAW text, not the sanitized block, because the question is
        # what the document tried to do. Screening our own neutralised output would
        # mostly confirm that layer 1 works.
        #
        # **The verdict does not block.** Layer 1 has already neutralised and fenced
        # the text, so what reaches the prompt is safe by construction; a match is
        # EVIDENCE a watermaster should see, exactly like layer 1's hits, and refusing
        # to draft would let any injected document veto an order. `screened` is carried
        # separately from `clean`, because an unavailable layer is not a passing one.
        verdict = screen_document(raw)
        del forwarded[SOURCE_DOCUMENT]
        forwarded[PROMPT_BLOCK] = sanitized.fenced()
        forwarded[INJECTION_HITS] = tuple((hit.kind.value, hit.matched) for hit in sanitized.hits)
        forwarded[ARMOR_STATE] = verdict.state.value
        forwarded[ARMOR_REASON] = verdict.reason
        forwarded[ARMOR_FILTERS] = verdict.matched_filters

    return forwarded


async def _draft(forwarded: dict[str, Any], *, produce_order: bool) -> dict[str, Any]:
    """Turn the Core's recommendation into a draft, or say why there is none.

    **Run on a worker thread, and that is a correctness fix rather than a
    performance one.** `draft_order` is synchronous and spends its time inside a
    blocking model call. Awaiting nothing while it runs would pin the event loop
    for the duration, and the thing that stops running first is the deadline: this
    module's whole timeout argument rests on `asyncio.timeout`, which can only fire
    when the loop gets a chance to run. A blocking call inside an async node
    therefore disables the very guard written to bound it, and it would also stall
    every other request on the instance, which is cause 3 in
    SESSION_APPEND_BUDGET_SECONDS arriving from our own code.

    The honest limit, stated rather than hidden: `to_thread` cannot cancel the
    thread it started, so a cancelled invocation returns while the model call
    continues to completion in the background. What it buys is that the deadline
    FIRES and the caller is told; the alternative buys neither.

    A recommendation the Core could not compute yields no draft and says which,
    because a missing draft and a refused draft are different facts to an officer.
    """
    if not produce_order:
        return {
            **forwarded,
            DRAFT: None,
            DRAFT_UNAVAILABLE: (
                "this invocation asked the fleet to classify a reading, not to produce "
                "an order, so no draft was requested and no model was called."
            ),
        }

    recommendation = forwarded.get(RECOMMENDATION)
    if recommendation is None:
        return {
            **forwarded,
            DRAFT: None,
            DRAFT_UNAVAILABLE: str(
                forwarded.get(
                    RECOMMENDATION_UNAVAILABLE,
                    "no recommendation reached the Scribe, so there is nothing to draft "
                    "from. A draft with no computed allocation behind it would be prose "
                    "with the authority of an order and the backing of nothing.",
                )
            ),
        }

    # Refused, not coerced, and the reason is the same one the untrusted-document
    # check gives one function up. `draft_order` reads the recommendation for the
    # rights it reached and the tier it recommends, and anything else in this key is
    # a caller passing something the Core did not compute. Handing that to a model
    # would produce a draft whose facts came from whatever the object happened to
    # stringify as, and the guard downstream compares the draft's claims against a
    # ledger this object does not have.
    if not isinstance(recommendation, Recommendation):
        raise TypeError(
            f"{RECOMMENDATION} must be a Recommendation the Core computed, got "
            f"{type(recommendation).__name__}. Drafting from anything else would put "
            "an order in front of an officer with facts that no allocation produced."
        )

    outcome = await asyncio.to_thread(draft_order, recommendation)
    return {**forwarded, DRAFT: outcome}


async def _herald(
    node_input: dict[str, Any],
    recipients: list[dict[str, str]] | None = None,
    transport_name: str = "",
) -> dict[str, Any]:
    """Route the artifact to its lane and distribute it. The Loop pattern.

    `recipients` and `transport_name` are bound BY NAME from session state, the same
    contract every other node parameter uses, rather than couriered down the payload
    from the Sentinel. Two reasons: a node should declare what it needs, and the
    Sentinel has no business carrying the Herald's configuration through two
    intervening nodes that must not read it.

    The lane is decided by what the artifact IS, never by this node's caller, and the
    report says plainly whether anything was legally served. A notification that
    succeeded is not service, and `may_report_as_served` is the single question any
    surface must ask before showing that word.

    No delivery vendor is wired. Without a resolvable transport the underlying call
    REFUSES rather than recording a delivery that did not happen, and a request selects
    the explicitly named synthetic one whose results are marked as such.

    **The verb is DERIVED from the recommendation, never read from the request.** The
    previous version took `node_input.get("action", "notification")`, which handed a
    caller the power to pick the lane and, worse, defaulted an unlabelled artifact to
    the lane with no legal consequence. A default that quietly selects notification is
    the same shape as a `None` that quietly asserts membership: it answers a legal
    question by accident, in the direction that makes an order unenforceable.
    """
    delivery_state = _nothing_to_deliver(node_input, recipients)
    if delivery_state is not None:
        return {**node_input, DELIVERY: None, DELIVERY_UNAVAILABLE: delivery_state}

    recommendation = node_input[RECOMMENDATION]
    action = _ARTIFACT_ACTION_FOR[recommendation.action]
    draft = node_input[DRAFT]

    factory = TRANSPORTS.get(transport_name)

    report = await asyncio.to_thread(
        deliver_order,
        order_id=str(node_input.get("order_id", "unsigned-draft")),
        action=action,
        recipients=_recipients_from(recipients),
        # The DRAFT is the artifact. A fenced source document is untrusted input the
        # Scribe neutralised, not the order, and sending it would distribute the thing
        # the sanitizer was defending against.
        artifact=draft.text,
        transport=factory() if factory is not None else None,
        synthetic=transport_name == "synthetic",
    )
    return {**node_input, DELIVERY: report, ARTIFACT_ACTION: action.value}


def _nothing_to_deliver(node_input: dict[str, Any], recipients: Any) -> str | None:
    """Why this run distributes nothing, or None when it should distribute.

    Each of these is an ordinary outcome rather than a fault, and each is REPORTED
    rather than left as a silent absence. A console that shows an empty delivery panel
    without saying why reads as a delivery that failed.
    """
    recommendation = node_input.get(RECOMMENDATION)
    if recommendation is None:
        return str(
            node_input.get(
                RECOMMENDATION_UNAVAILABLE,
                "no recommendation was computed, so there is no artifact to distribute.",
            )
        )
    if recommendation.action not in _ARTIFACT_ACTION_FOR:
        return (
            f"the Core recommends {recommendation.action.value}, which implies no order. "
            "Nothing is served or notified, because there is no document: a "
            "near-threshold reading flagged for field verification asks a human to go "
            "and measure, and it is not something anybody is served with."
        )
    draft = node_input.get(DRAFT)
    if draft is None:
        return str(
            node_input.get(
                DRAFT_UNAVAILABLE,
                "no draft was produced, so there is nothing to distribute.",
            )
        )
    # **A DRAFT EXISTING IS NOT A DRAFT BEING FIT TO SEND.** This checked only for
    # absence, and an escalated draft is not absent: `draft_order` deliberately RETURNS
    # the text on escalation, because a watermaster reviewing why a draft was refused
    # needs to see what was written. The Scribe's contract says the label is what keeps
    # it out of the PDF generator. Nothing was enforcing the same for distribution, so a
    # draft that had just failed the hallucination guard twice was handed to Herald and
    # SERVED, which is worse than reaching a PDF: certified mail marks it
    # `constitutes_legal_service`. Observed live before this fix, on a production
    # traversal where the model drafted a Shasta order in Scott's ladder vocabulary,
    # the guard escalated it, and a recipient was still recorded as legally served.
    #
    # Asked through `may_reach_pdf` on purpose. The Scribe states that as the ONE
    # question, asked one way, so a caller cannot consult the looser of two conditions;
    # re-deriving it here from `verdict` alone would miss `escalated`.
    if not draft.may_reach_pdf:
        return (
            "the draft did not pass its guards, so it is not distributed. It reaches "
            "the human queue labelled UNVERIFIED instead: an escalated draft still "
            "carries its text so a reviewer can see what was written, and that is a "
            f"reason to show it to one official, never to serve it. Guard: {draft.guard.reason}"
        )
    if not recipients:
        return (
            "no recipients were supplied, so nothing was distributed. A drafted order "
            "with nobody to serve is a real state: it is produced, reviewed, and held."
        )
    return None


def _recipients_from(raw: Any) -> list[Recipient]:
    """Rebuild recipients from the JSON-safe form session state can actually hold.

    Session state is persisted by a real session service, so a frozen dataclass cannot
    travel there and a request supplies plain dictionaries. Each field is converted
    through its enum, so an unknown channel or recipient class raises here rather than
    reaching the lane rule as an unrecognised string.

    `label` and nothing else. There is deliberately no address field on `Recipient`, so
    no personal contact detail enters session state or the event log.
    """
    if not isinstance(raw, list | tuple):
        raise TypeError(f"{RECIPIENTS} must be a list, got {type(raw).__name__}")
    built: list[Recipient] = []
    for entry in raw:
        if isinstance(entry, Recipient):
            built.append(entry)
            continue
        if not isinstance(entry, dict):
            raise TypeError(f"each recipient must be a mapping, got {type(entry).__name__}")
        built.append(
            Recipient(
                recipient_id=str(entry["recipient_id"]),
                recipient_class=RecipientClass(entry["recipient_class"]),
                channel=Channel(entry["channel"]),
                label=str(entry.get("label", "")),
            )
        )
    return built


sentinel_node = _build_node(SENTINEL, _sentinel)
core_node = _build_node(CORE, _core)
scribe_node = _build_node(SCRIBE, _scribe)
herald_node = _build_node(HERALD, _herald)


def build_curtailment_graph() -> Workflow:
    """The Graph workflow: Sentinel, Core, Scribe, Herald.

    One of the three orchestration patterns named in the README, and an ADK class
    rather than something hand-rolled and then described as a graph.

    The edges are the fleet's separation of concerns made mechanical. The Core is
    reachable only from the Sentinel and the Scribe only from the Core, so there
    is no path by which a drafted order exists without a computed recommendation
    behind it.

    Edges take NODE OBJECTS, not names. An earlier version passed strings and
    both mypy and pydantic rejected it at once: an edge naming a node that does
    not exist is a graph that cannot be assembled, and catching that at
    construction beats catching it on the first message.
    """
    return Workflow(
        name="curtailment",
        description=(
            "Gage Sentinel to Allocation Core to Order Scribe to Herald. Every "
            "drafted order has a computed recommendation behind it, because the "
            "graph provides no other path to the Scribe."
        ),
        timeout=GRAPH_TIMEOUT_SECONDS,
        edges=[
            Edge(from_node=START, to_node=sentinel_node),
            Edge(from_node=sentinel_node, to_node=core_node),
            Edge(from_node=core_node, to_node=scribe_node),
            Edge(from_node=scribe_node, to_node=herald_node),
        ],
    )
