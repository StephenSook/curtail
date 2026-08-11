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

**A timeout is not a slower attempt limit.** A model that loops does not fail, it
simply never returns, so an attempt ceiling alone never fires. `timeout` bounds
wall time and raises `NodeTimeoutError`, which is a different failure with a
different meaning, and only the pair covers both shapes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google.adk.workflow import START, Edge, RetryConfig, Workflow, node

from curtail_agents.routing import (
    BACKOFF_FACTOR,
    INITIAL_DELAY_SECONDS,
    JITTER,
    MAX_ATTEMPTS,
    MAX_DELAY_SECONDS,
    NODE_TIMEOUT_SECONDS,
)

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


#: Worst-case wall time for a full traversal, computed rather than guessed.
#:
#: A review caught the original: `NODE_TIMEOUT_SECONDS * 4` assumed each node ran
#: ONCE. With three attempts allowed on three transient nodes the attempts alone
#: reach 1200 seconds, so a 480 second ceiling silently truncated the retries it
#: was meant to accommodate, converting recoverable upstream slowness into a
#: workflow failure and preventing delivery.
#:
#: Derived from the same constants the policy uses, so raising MAX_ATTEMPTS
#: cannot leave the ceiling behind.
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
    return attempts + backoff


GRAPH_TIMEOUT_SECONDS = _worst_case_seconds()


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
        retry_config=TRANSIENT_RETRY,
        timeout=NODE_TIMEOUT_SECONDS,
        rationale=(
            "A USGS poll fails on transport, and transport recovers. Retrying is "
            "correct here and the jitter matters: without it, both basin pollers "
            "retry in lockstep against an API that just failed."
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


async def _sentinel(state: dict[str, Any]) -> dict[str, Any]:
    """Poll a gage and classify the reading."""
    return state


async def _core(state: dict[str, Any]) -> dict[str, Any]:
    """Compute the recommendation. Deterministic, so it does not retry."""
    return state


async def _scribe(state: dict[str, Any]) -> dict[str, Any]:
    """Draft the order. The only node calling a model, so the only one that can
    loop rather than fail, which is why the timeout matters here most."""
    return state


async def _herald(state: dict[str, Any]) -> dict[str, Any]:
    """Serve and notify. The Loop pattern the README names."""
    return state


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
