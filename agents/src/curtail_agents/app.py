"""The application entrypoint: the only thing that runs the fleet.

**This file exists because a review said the same thing four times and was right
each time.** The invocation deadline in `fleet.py` had no production caller, so
it protected tests and nothing else. The answer given three times was that no
entrypoint could exist yet, since the Core has no rights table and the Scribe has
no model. That answer was half true and the wrong half mattered: the SENTINEL is
real, so a real invocation that classifies a real gage reading was available all
along, and only the downstream stages were unbuildable.

Attempting it immediately found a defect no shape test could: `build_curtailment_
graph()` **could not be executed at all**. ADK binds a node function's parameters
by NAME out of session state, so a node declaring one `state: dict` parameter sent
ADK looking for a state key called `state`, and every invocation died before any
node body ran. A suite whose stated subject is guards that are attached rather
than described contained a graph that could not run, because every test asserted
its shape and none asserted that it executes.

**Three entrypoints, and the difference between them is what the caller ASKED
FOR rather than how much of the fleet is built.** `evaluate_reading` returns the
Sentinel's classification and `evaluate_direction` returns the direction it
points; both run the whole graph and neither requests a draft, because answering
a classification question by drafting a legal document would burn a model call
and add a failure mode to a question nobody asked. `run_fleet` asks for the
order, so the Scribe drafts and the Herald distributes.

An earlier version of this paragraph said the Core, Scribe and Herald "are
placeholders" and that this was "an honest partial pipeline". It was honest when
written and it went stale where nobody looks: all four handlers are attached now.
What remains unwired is the delivery vendor, which is why a caller names the
labelled synthetic transport and every report it produces says so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.genai import types

from curtail_agents.events import GageEvent
from curtail_agents.fleet import (
    DOCUMENT_PART_PREFIX,
    INVOCATION_DEADLINE_SECONDS,
    build_curtailment_graph,
    run_invocation,
)
from curtail_agents.sanitize import check_document_size
from curtail_agents.sentinel import Observation

#: Named once so the session, the runner and the console cannot disagree.
APP_NAME = "curtail"


class NoClassificationError(RuntimeError):
    """The fleet ran but produced no classification.

    Raised rather than returning None. A caller handed None would have to invent
    a meaning for it, and the only honest meanings here are "the Sentinel did not
    run" and "its output was lost in transit", both of which are failures a
    watermaster must see rather than a value to be handled.
    """


def build_fleet_runner(
    session_service: BaseSessionService,
    *,
    app_name: str = APP_NAME,
) -> Runner:
    """Construct the Runner that owns fleet execution.

    The session service is injected rather than chosen here, because it is the
    one dependency whose identity changes the system's guarantees: in-memory for
    tests, Cloud SQL in production, and the whole `SESSION_APPEND_BUDGET_SECONDS`
    analysis is about what happens when the production one stalls.
    """
    return Runner(
        app_name=app_name,
        node=build_curtailment_graph(),
        session_service=session_service,
    )


def _as_state(observation: Observation) -> dict[str, Any]:
    """Render one reading as JSON-safe session state.

    Session state is persisted by a real session service, so it carries plain
    values and never a live dataclass. ADK coerces this back to `Observation` at
    the node boundary through that parameter's type annotation, which is why the
    annotation in `fleet._sentinel` is load-bearing rather than decoration.
    """
    return {
        "basin": observation.basin.value,
        "observed_cfs": observation.observed_cfs,
        "observed_at": observation.observed_at.isoformat(),
        "provenance": observation.provenance.value,
    }


def _message_parts(observation: Observation, source_document: str | None) -> list[types.Part]:
    """The instruction part, and the untrusted document part if there is one.

    Separate parts, and the document one carries an explicit prefix, so the Sentinel
    identifies it by marker rather than by position. A re-hydrated or reordered
    message would otherwise be able to turn the instruction into the document, which
    would feed the sanitizer the wrong text and leave the real document unscanned.
    """
    parts = [types.Part(text=f"evaluate {observation.basin.value}")]
    if source_document is not None:
        # Before the text enters the message, because once it is in the message it
        # is in the session event log and the storage cost is already paid.
        check_document_size(source_document)
        parts.append(types.Part(text=f"{DOCUMENT_PART_PREFIX}{source_document}"))
    return parts


async def _run(
    observation: Observation,
    *,
    runner: Runner,
    correlation_id: str,
    user_id: str,
    session_id: str,
    recent: Sequence[Observation] = (),
    source_document: str | None = None,
    produce_order: bool = False,
    recipients: Sequence[Mapping[str, str]] = (),
    transport_name: str = "",
    deadline: float = INVOCATION_DEADLINE_SECONDS,
) -> list[Event]:
    """One invocation, shared by every entrypoint.

    Factored out so the answers a caller can ask for come from the SAME run
    rather than from code paths that could drift into disagreeing about one
    reading.

    **`produce_order` is stated by every caller, never defaulted at this layer.**
    Classifying a reading and producing a legal document are different jobs, and a
    default here would let an entrypoint request one while believing it asked for
    the other. The three keys below it are the Herald's, bound by name at that node
    rather than couriered through the payload.
    """
    if not correlation_id.strip():
        raise ValueError(
            "a correlation id is required. It is what ties a dead-lettered "
            "message back to the poll that produced it, and an empty one makes a "
            "failure untraceable at exactly the moment tracing matters."
        )
    state_delta: dict[str, Any] = {
        "observation": _as_state(observation),
        "correlation_id": correlation_id,
        "recent": [_as_state(o) for o in recent],
        "produce_order": produce_order,
        "recipients": [dict(r) for r in recipients],
        "transport_name": transport_name,
    }
    return await run_invocation(
        runner,
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=_message_parts(observation, source_document)),
        state_delta=state_delta,
        deadline=deadline,
    )


async def evaluate_direction(
    observation: Observation,
    *,
    runner: Runner,
    correlation_id: str,
    user_id: str,
    session_id: str,
    recent: Sequence[Observation] = (),
    deadline: float = INVOCATION_DEADLINE_SECONDS,
) -> str:
    """The agent's answer in the vocabulary an eval case is written in.

    `evaluate_reading` returns the Sentinel's CLASSIFICATION, which is what the
    Sentinel is for. This returns the DIRECTION that classification points, which is
    what the Board's own record can be compared against, and it comes from the
    fleet's own output rather than being computed by the caller. That distinction is
    the whole point: a test that mapped the classification itself would be proving
    its own arithmetic, not the agent's answer.
    """
    events = await _run(
        observation,
        runner=runner,
        correlation_id=correlation_id,
        user_id=user_id,
        session_id=session_id,
        recent=recent,
        # A classification question. Drafting a legal document to answer it would
        # burn a model call and add a failure mode to a question nobody asked.
        produce_order=False,
        deadline=deadline,
    )
    for event in reversed(events):
        output = event.output
        if isinstance(output, dict):
            direction = output.get("direction")
            if isinstance(direction, str):
                return direction
    raise NoClassificationError(
        f"the fleet produced no direction for {observation.basin.value} at "
        f"{observation.observed_at.isoformat()}."
    )


async def evaluate_reading(
    observation: Observation,
    *,
    runner: Runner,
    correlation_id: str,
    user_id: str,
    session_id: str,
    recent: Sequence[Observation] = (),
    source_document: str | None = None,
    deadline: float = INVOCATION_DEADLINE_SECONDS,
) -> GageEvent:
    """Run the fleet over one gage reading and return the classification.

    Goes through `run_invocation`, so the deadline applies to the real path and
    not merely to a helper. That is the sanctioned way to execute this graph, and
    a test in `test_fleet.py` fails on the commit that adds any other.

    **`source_document` is untrusted order text, and it travels in the MESSAGE, not
    in session state.** That distinction was recorded in `fleet.py` as an unresolved
    design tension: ADK offers two ways into a graph, and the Scribe's invariant
    forbids one of them. The second was never tested, and a probe settled it in a
    minute: the user message arrives at the first node as `node_input`, parts intact.

    So the text rides the message, the Sentinel lifts it into the payload, the
    Scribe sanitizes and fences it, and session state never holds it. The raw text
    does enter the session EVENT log, which is where it belongs: that log is the
    immutable record of what actually arrived, and auditing a poisoned document
    needs exactly that. State is the dangerous place, because state is what nodes
    bind parameters from by name.
    """
    events = await _run(
        observation,
        runner=runner,
        correlation_id=correlation_id,
        user_id=user_id,
        session_id=session_id,
        recent=recent,
        source_document=source_document,
        produce_order=False,
        deadline=deadline,
    )

    for event in reversed(events):
        output = event.output
        if isinstance(output, dict):
            classified = output.get("event")
            if isinstance(classified, GageEvent):
                return classified

    # A node's exception does NOT arrive here. Verified against the library and
    # at runtime: the workflow path re-raises the original error, so a
    # `SentinelError` reaches the caller as itself, with the message naming which
    # era's table is missing. A review asserted the opposite, that ADK flattens
    # node failures into error events, and a probe disproved it. Recorded because
    # the difference decides whether a watermaster is told "no minimum is encoded
    # for the 2021 Shasta table" or merely "no classification".
    #
    # Error events CAN still appear among the events of a run that succeeded,
    # since each retried attempt emits one, so they are surfaced here as
    # diagnostics for the case where a run finishes yet classifies nothing.
    errors = [e.error_message for e in events if e.error_code]
    raise NoClassificationError(
        f"the fleet produced no classification for {observation.basin.value} at "
        f"{observation.observed_at.isoformat()}. Errors seen: {errors or 'none'}."
    )


@dataclass(frozen=True, slots=True)
class NodeOutput:
    """What one node produced, and which node produced it.

    The node's own name travels with its output because that is the claim being
    made. A console showing four panels of data with no attribution asserts a fleet
    ran; this says which member produced which panel, and it is read off ADK's own
    `node_info.path` rather than assumed from the order events arrive in.
    """

    node: str
    payload: dict[str, Any]
    error: str = ""


@dataclass(frozen=True, slots=True)
class FleetRun:
    """One traversal, node by node.

    Held as an ordered tuple rather than a dict keyed by node name, because a
    retried node emits more than once and collapsing those would hide the retry
    that the loop-breaker exists to make visible.
    """

    correlation_id: str
    nodes: tuple[NodeOutput, ...]

    def last_from(self, node: str) -> dict[str, Any] | None:
        """The final payload a node produced, or None if it never produced one."""
        for output in reversed(self.nodes):
            if output.node == node and not output.error:
                return output.payload
        return None

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(o.error for o in self.nodes if o.error)


def _node_name(event: Event) -> str:
    """The node that produced an event, from ADK's own path.

    `node_info.path` reads `curtailment@1/order_scribe@1`, so the node name is the
    last segment with its run counter removed. Taken from the library's attribution
    rather than inferred from arrival order, because a retried node emits twice and
    position would then name the wrong member.
    """
    info = getattr(event, "node_info", None)
    path = getattr(info, "path", "") or ""
    if not path:
        return ""
    return path.rsplit("/", 1)[-1].split("@", 1)[0]


async def run_fleet(
    observation: Observation,
    *,
    runner: Runner,
    correlation_id: str,
    user_id: str,
    session_id: str,
    recipients: Sequence[Mapping[str, str]] = (),
    transport_name: str = "",
    source_document: str | None = None,
    recent: Sequence[Observation] = (),
    deadline: float = INVOCATION_DEADLINE_SECONDS,
) -> FleetRun:
    """Run the whole fleet for an ORDER, and report what each node produced.

    This is the entrypoint that asks for the document, so the Scribe calls the model
    and the Herald distributes down whichever lane the verb requires.

    **Every node's output is returned, including the ones that produced nothing.** A
    surface that only received the final payload could not tell a fleet that ran
    from a single function that computed the same answer, which is exactly the
    claim a judge is being asked to check. Errors are carried per node rather than
    raised, because a Herald that failed after a Core that succeeded is a partial
    result worth showing, and collapsing it to an exception would throw away the
    three nodes that did their work.
    """
    events = await _run(
        observation,
        runner=runner,
        correlation_id=correlation_id,
        user_id=user_id,
        session_id=session_id,
        recent=recent,
        source_document=source_document,
        produce_order=True,
        recipients=recipients,
        transport_name=transport_name,
        deadline=deadline,
    )

    outputs: list[NodeOutput] = []
    for event in events:
        name = _node_name(event)
        if not name:
            continue
        if event.error_code:
            outputs.append(
                NodeOutput(
                    node=name, payload={}, error=event.error_message or str(event.error_code)
                )
            )
        elif isinstance(event.output, dict):
            outputs.append(NodeOutput(node=name, payload=event.output))

    if not outputs:
        raise NoClassificationError(
            f"the fleet produced no node output for {observation.basin.value} at "
            f"{observation.observed_at.isoformat()}. The graph ran and attributed "
            "nothing, which means the traversal did not reach a single node body."
        )
    return FleetRun(correlation_id=correlation_id, nodes=tuple(outputs))
