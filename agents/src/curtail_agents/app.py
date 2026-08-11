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

**What is real here and what is not.** `evaluate_reading` runs the real Sentinel
against a real reading through a real ADK Runner, under the deadline, and returns
the classification. The Core, Scribe and Herald stages the payload then passes
through are placeholders labelled as such in `fleet.py`. This is an honest
partial pipeline rather than a hollow call site added to satisfy a reviewer, and
the distinction is the whole point: the entrypoint claims exactly the work the
fleet actually does today.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    deadline: float = INVOCATION_DEADLINE_SECONDS,
) -> list[Event]:
    """One invocation, shared by both entrypoints.

    Factored out so the two answers a caller can ask for come from the SAME run
    rather than from two code paths that could drift into disagreeing about one
    reading.
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
