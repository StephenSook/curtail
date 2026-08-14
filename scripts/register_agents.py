"""Register the Curtail fleet in the Google Agent Registry.

The Fortified Enterprise Fleet track asks whether an institutional agent network can be
**cataloged for cross-department use**. A registry holding nothing answers no, and the
governance table said so honestly for days: "Reachable, not yet populated."

**Two API-shape facts, established by the M0 spike and re-confirmed against the live
discovery document before this script was written.**

1. **There is no `agents.create`.** `agents` and `mcpServers` expose only `list`, `get`
   and `search`. Registration happens through `services.create`, and the registry derives
   the agent record from the card. Any plan that called for creating agents directly was
   wrong about the API.
2. With `AgentSpec.type = A2A_AGENT_CARD` the Service's own `interfaces` **must be
   empty**; connection details live inside the card content. The API rejects the
   combination explicitly.

**Why the agent list is imported rather than typed here.** The registry is a claim about
what the graph contains. A hand-written list would be a second source of truth that drifts
the moment a node is added or renamed, which is the defect this project keeps finding in
its own artifacts. `NODES` is checked against the constants `fleet.py` actually uses, and
this refuses to run if they disagree.

**Why the endpoint is probed before anything is registered.** The M0 spike's probe was
deleted precisely because it pointed at a placeholder `example.run.app` URL, and a registry
entry describing an endpoint that does not exist is the claim drift this project exists to
prevent. A catalog of unreachable agents is worse than an empty catalog: it is an empty
catalog that lies.

**What the cards deliberately do NOT claim.** These four are nodes of one ADK `Workflow`
graph co-hosted in a single Cloud Run service, speaking HTTP JSON rather than the A2A wire
protocol. Each card is a catalog entry, not an A2A server advertisement, and says so.

**Each agent carries the route that actually reaches it, and the API forced that.** The
first attempt gave all four the traversal endpoint and the registry rejected three with
`Interface URL is already in use by another service`. That constraint is correct: an
address identifies an agent, so four things at one address are one agent. Three nodes do
have a direct route, confirmed by parsing the shipped handlers. **Herald does not**, and
its card states that plainly instead of borrowing a URL that would imply otherwise:
`deliver_order` has no direct HTTP call site and is reached only as the final node of a
full traversal.

Run:    uv run python scripts/register_agents.py
Check:  uv run python scripts/register_agents.py --check
Remove: uv run python scripts/register_agents.py --delete
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "agents" / "src"))
sys.path.insert(0, str(REPO / "core" / "src"))

PROJECT = "curtail-505118"
LOCATION = "us-central1"
BASE = "https://agentregistry.googleapis.com/v1"
PARENT = f"projects/{PROJECT}/locations/{LOCATION}"

#: The deployed service. Registered agents point HERE, and this is probed first.
SERVICE_URL = "https://curtail-console-api-672785135387.us-central1.run.app"

#: The one endpoint that drives a full traversal of the graph.
TRAVERSAL_PATH = "/api/fleet/{basin}"

#: A2A protocol version. Matches what the registry derived for the only other agent
#: present, Google's own Workspace Agent, so the catalog is internally consistent.
PROTOCOL_VERSION = "0.3.0"

#: The version stamped on every card. Bumped when a card's content changes, not when the
#: application deploys: this describes the AGENT's contract, not the build.
CARD_VERSION = "1.0.0"

#: Shared by every card, because it is true of every node and a reader who opens only one
#: card should still learn it.
#:
#: **Each agent gets the route that actually reaches it, and the registry forced this.**
#: The first attempt gave all four the traversal endpoint and the API rejected three with
#: "Interface URL is already in use by another service". That constraint is right: an
#: address identifies an agent, so four things at one address are one agent. The routes
#: below are verified against the deployed OpenAPI document before anything is written,
#: and against the source by `scripts/generate_facts.py`, which reports which domain
#: function each handler reaches.
CO_HOSTING_NOTE = (
    "This agent is a node of one ADK Workflow graph, co-hosted with the rest of the "
    "Curtail fleet in a single Cloud Run service rather than deployed separately. It "
    "speaks HTTP JSON, not the A2A wire protocol; this card is a catalog entry, not an "
    "A2A server advertisement. Nothing it produces self-executes: 23 CCR 875(b) vests "
    "the determination in a named human official, and the fleet drafts for signature "
    "only."
)

#: Keyed by the node name `fleet.py` uses. Checked against the module's own constants
#: below, so a renamed node fails here rather than silently registering a stale catalog.
AGENTS: dict[str, dict[str, Any]] = {
    "gage_sentinel": {
        "service_id": "curtail-gage-sentinel",
        "display": "Curtail Gage Sentinel",
        "route": "/api/classify/{basin}",
        "reach_note": (
            "This route invokes this agent DIRECTLY: it calls the same `evaluate` "
            "function the graph node wraps. A full traversal that also runs the "
            "other three is at " + TRAVERSAL_PATH + "."
        ),
        "description": (
            "Classifies a USGS streamflow reading against the operative drought "
            "emergency minimum flow for a California adjudicated basin. The minimums are "
            "date-period bounded rather than monthly, so Scott changes mid-month on June "
            "24 and Shasta on March 25 and September 16. Emits a flow-below-minimum, "
            "flow-recovered or near-threshold classification, the last when a reading is "
            "within 10 cfs of the minimum, mirroring Fort Jones field practice."
        ),
        "skills": [
            {
                "id": "classify-reading",
                "name": "Classify a gage reading against the operative minimum",
                "description": (
                    "Returns the event type, the observed discharge, the operative "
                    "minimum for that date, and the direction of any indicated action."
                ),
                "tags": ["hydrology", "water-rights", "usgs", "compliance"],
                "examples": [
                    "Classify 45.3 cfs on the Shasta River at Yreka on 16 June 2026.",
                    "Is 117 cfs at Fort Jones below the operative Scott River minimum on "
                    "10 June 2026?",
                ],
            }
        ],
    },
    "allocation_core": {
        "service_id": "curtail-allocation-core",
        "display": "Curtail Allocation Core",
        "route": "/api/recommendation/{basin}",
        "reach_note": (
            "This route invokes this agent DIRECTLY: it calls the same `recommend` "
            "function the graph node wraps, against the committed rights record. A "
            "full traversal is at " + TRAVERSAL_PATH + "."
        ),
        "description": (
            "Deterministic, NOT a language model. Computes which water rights a "
            "curtailment would reach under prior-appropriation priority, from the State "
            "Water Board's own rights table and the 23 CCR 875.5 priority groupings. It "
            "produces a RECOMMENDATION and never a determination: 875(b)(3) expressly "
            "lets the Deputy Director decline to issue, use a smaller grouping, or "
            "suspend. Separates deterministic facts from judgment inputs, which are "
            "surfaced for the official and never auto-resolved, and reports an open "
            "shortfall rather than extrapolating past incomplete data."
        ),
        "skills": [
            {
                "id": "recommend-curtailment-extent",
                "name": "Recommend the priority extent of a curtailment",
                "description": (
                    "Returns a per-right ledger carrying the statutory authority for "
                    "each line, the recommended extent rank, the rights reached, and "
                    "the judgment inputs left to the official."
                ),
                "tags": ["water-rights", "prior-appropriation", "deterministic", "audit"],
                "examples": [
                    "Which Shasta River rights would a curtailment reach at 39 cfs "
                    "against a 50 cfs minimum?",
                ],
            }
        ],
    },
    "order_scribe": {
        "service_id": "curtail-order-scribe",
        "display": "Curtail Order Scribe",
        "route": "/api/queue/draft/{basin}",
        "reach_note": (
            "This route invokes this agent DIRECTLY: it calls `draft_order` behind "
            "both guards and places the result in the human approval queue. A full "
            "traversal is at " + TRAVERSAL_PATH + "."
        ),
        "description": (
            "Drafts a curtailment order, addendum or rescission for a human "
            "watermaster's signature. Every draft is checked against the Allocation "
            "Core's computed ledger before it may reach a PDF generator: a draft "
            "asserting a right, priority date or tier the Core did not compute is "
            "rejected, retried once with the violation fed back, then escalated to the "
            "human queue labelled UNVERIFIED. A deterministic citation scrubber then "
            "strips any legal authority absent from a verified allowlist, because a "
            "system prompt forbidding invention is not a guardrail."
        ),
        "skills": [
            {
                "id": "draft-order-for-signature",
                "name": "Draft a curtailment order for signature",
                "description": (
                    "Returns draft text, the guard verdict, the reason, the attempt "
                    "count, and whether the draft may reach the PDF generator."
                ),
                "tags": ["legal-drafting", "guardrails", "human-in-the-loop"],
                "examples": [
                    "Draft a Shasta River curtailment order from the Core's ledger.",
                ],
            }
        ],
    },
    "herald": {
        "service_id": "curtail-herald",
        "display": "Curtail Herald",
        "route": TRAVERSAL_PATH,
        "reach_note": (
            "**This agent has NO standalone route, and that is stated rather than "
            "disguised.** `deliver_order` has no direct HTTP call site: an audit of "
            "the shipped source finds it reached only as the final node of a full "
            "graph traversal, which is what this URL drives. Distribution is not "
            "offered as a free-standing operation on purpose, because serving a "
            "document that no computed recommendation stands behind is exactly what "
            "the graph edges exist to prevent."
        ),
        "description": (
            "Distributes a signed or drafted artifact through TWO separate state "
            "machines, because formal service and notification are legally different "
            "acts. Water Code 1121 as amended by SB 756 permits four service methods, "
            "and an email is none of them, so a notification-lane delivery can be "
            "entirely successful and is never service. Reports whether a distribution "
            "may be reported as served as its own field rather than leaving it to be "
            "inferred from a records list that looks green. An escalated draft is never "
            "distributed at all."
        ),
        "skills": [
            {
                "id": "serve-or-notify",
                "name": "Serve or notify, on the correct lane",
                "description": (
                    "Returns per-recipient records with the method used, whether each "
                    "constitutes legal service, escalations, and whether the "
                    "distribution as a whole may be reported as served."
                ),
                "tags": ["legal-service", "notification", "water-code-1121", "audit"],
                "examples": [
                    "Serve a curtailment order on the parties and notify operational diverters.",
                ],
            }
        ],
    },
}


def _token() -> str:
    return subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _api(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        # The status line alone does not say which field was rejected, and the M0 spike
        # depended on exactly that detail to learn the API's shape. Never swallow it.
        raise SystemExit(f"{method} {url} failed {exc.code}:\n{detail}") from exc


def _assert_nodes_match_the_graph() -> None:
    """The catalog describes the graph, so it must be built from the graph's own names.

    A hand-written list is a second source of truth, and this project's recurring defect
    is a second source of truth drifting from the first. Renaming a node now fails here
    instead of leaving a stale catalog that still looks populated.
    """
    from curtail_agents.fleet import CORE, HERALD, SCRIBE, SENTINEL

    in_code = {SENTINEL, CORE, SCRIBE, HERALD}
    listed = set(AGENTS)
    if in_code != listed:
        raise SystemExit(
            "the registry list has drifted from the graph.\n"
            f"  fleet.py has: {sorted(in_code)}\n"
            f"  this file has: {sorted(listed)}\n"
            "Update AGENTS so the catalog describes the graph that exists."
        )


def _assert_endpoint_is_live() -> None:
    """Refuse to catalog an endpoint that does not answer.

    The M0 spike's probe was DELETED for pointing at a placeholder URL, because a
    registry entry describing an endpoint that does not exist is precisely the claim
    drift this project exists to prevent. **A catalog of unreachable agents is worse than
    an empty one: it is an empty one that lies.**

    Checked against the OpenAPI document rather than the traversal endpoint itself: this
    must confirm the surface serves the route, not spend a model call and a distribution
    every time somebody re-runs registration.

    **Retried, with a timeout sized for a COLD START, because the first version read a
    transient as an absence.** Cloud Run scales to zero, and the first request after an
    idle period can exceed a short deadline while the container boots. That run reported
    the service unreachable and refused to register; a `curl` seconds later got 200 in
    5.2s, and a warm request in 0.1s. A guard that turns "still starting" into "does not
    exist" is the errored-query-reads-as-a-negative-result defect, and here it blocks
    correct work rather than allowing wrong work, which is the safer direction to fail
    but still wrong.
    """
    spec: dict[str, Any] | None = None
    last = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"{SERVICE_URL}/openapi.json", timeout=90) as response:
                if response.status != 200:
                    last = f"answered {response.status} for its OpenAPI document"
                    continue
                spec = json.load(response)
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < 2:
                print(f"  probe attempt {attempt + 1} failed ({last}), retrying")
                time.sleep(5)

    if spec is None:
        raise SystemExit(
            f"{SERVICE_URL} could not be read after 3 attempts ({last}), so nothing is "
            "registered. A registry entry for an endpoint that does not answer is worse "
            "than no entry."
        )

    served = spec.get("paths") or {}
    missing = sorted({a["route"] for a in AGENTS.values()} - set(served))
    if missing:
        raise SystemExit(
            f"the deployed service does not serve {missing}, so those cards would name "
            "routes that do not exist. Deploy first, then register: a catalog entry "
            "pointing at a 404 is the claim drift this project exists to prevent."
        )


def _card(node: str) -> dict[str, Any]:
    """One A2A agent card, honest about what this agent is and is not."""
    spec = AGENTS[node]
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": spec["display"],
        "description": f"{spec['description']}\n\n{spec['reach_note']}\n\n{CO_HOSTING_NOTE}",
        "url": f"{SERVICE_URL}{spec['route']}",
        "preferredTransport": "HTTP+JSON",
        "version": CARD_VERSION,
        "provider": {"organization": "Curtail", "url": SERVICE_URL},
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": spec["skills"],
    }


def _service_body(node: str) -> dict[str, Any]:
    spec = AGENTS[node]
    return {
        "displayName": spec["display"],
        "description": spec["description"][:2048],
        # `interfaces` stays ABSENT. With A2A_AGENT_CARD the API requires it empty and
        # rejects the combination by name; connection details live in the card.
        "agentSpec": {"type": "A2A_AGENT_CARD", "content": _card(node)},
    }


def _await_operation(operation: dict[str, Any], token: str) -> dict[str, Any]:
    """Poll until done, and treat a DONE operation carrying an error as the failure it is.

    **`done: true` is not success, and the first version of this read it as success.**
    Three of four registrations failed with `INVALID_ARGUMENT` while every operation
    reported done, so the script printed four cheerful "creating" lines and only the
    read-back at the end revealed that one service existed. That is the same false-green
    shape as a watch command whose exit code lies: the long-running-operation protocol
    puts the verdict in `error`, not in `done`.

    The read-back stays regardless. An operation succeeding is a claim about a request;
    the resource existing is the fact.
    """
    if not operation.get("done"):
        name = operation.get("name")
        if not name:
            raise SystemExit(f"the API returned no operation name to poll:\n{operation}")
        for _ in range(60):
            time.sleep(2)
            operation = _api("GET", f"{BASE}/{name}", token)
            if operation.get("done"):
                break
        else:
            raise SystemExit(f"operation {name} did not complete within two minutes")

    if operation.get("error"):
        raise SystemExit(
            "the operation completed WITH AN ERROR, which is a failure:\n"
            f"  {json.dumps(operation['error'], indent=2)}"
        )
    return operation


def _existing(token: str) -> dict[str, dict[str, Any]]:
    listed = _api("GET", f"{BASE}/{PARENT}/services", token)
    return {s["name"].rsplit("/", 1)[-1]: s for s in (listed.get("services") or [])}


def register() -> int:
    _assert_nodes_match_the_graph()
    _assert_endpoint_is_live()
    token = _token()
    present = _existing(token)

    for node, spec in AGENTS.items():
        service_id = spec["service_id"]
        if service_id in present:
            print(f"  {service_id}: already registered, patching the card")
            _await_operation(
                _api(
                    "PATCH",
                    f"{BASE}/{PARENT}/services/{service_id}"
                    "?updateMask=displayName,description,agentSpec",
                    token,
                    _service_body(node),
                ),
                token,
            )
            continue
        print(f"  {service_id}: creating")
        _await_operation(
            _api(
                "POST",
                f"{BASE}/{PARENT}/services?serviceId={service_id}",
                token,
                _service_body(node),
            ),
            token,
        )

    # Read back. Creating is not proof; the resource existing is.
    print("\nregistered services:")
    for service_id, service in sorted(_existing(token).items()):
        print(f"  {service_id} -> {service.get('registryResource', 'NO REGISTRY RESOURCE')}")

    agents = _api("GET", f"{BASE}/{PARENT}/agents", token).get("agents") or []
    mine = [a for a in agents if str(a.get("displayName", "")).startswith("Curtail ")]
    print(f"\ndiscoverable Curtail agents: {len(mine)} of {len(AGENTS)} expected")
    for agent in sorted(mine, key=lambda a: str(a.get("displayName"))):
        skills = ", ".join(s.get("id", "?") for s in (agent.get("skills") or []))
        print(f"  {agent.get('displayName')}  skills=[{skills}]")
    return 0 if len(mine) == len(AGENTS) else 1


def check() -> int:
    """Fail if the registry does not hold exactly the agents the graph defines."""
    _assert_nodes_match_the_graph()
    token = _token()
    agents = _api("GET", f"{BASE}/{PARENT}/agents", token).get("agents") or []
    found = {
        str(a.get("displayName"))
        for a in agents
        if str(a.get("displayName", "")).startswith("Curtail ")
    }
    expected = {spec["display"] for spec in AGENTS.values()}
    if found != expected:
        print("the registry does not match the graph.", file=sys.stderr)
        print(f"  missing:   {sorted(expected - found)}", file=sys.stderr)
        print(f"  unexpected:{sorted(found - expected)}", file=sys.stderr)
        return 1
    print(f"registry holds all {len(expected)} Curtail agents.")
    return 0


def delete() -> int:
    token = _token()
    for service_id in (spec["service_id"] for spec in AGENTS.values()):
        print(f"  deleting {service_id}")
        _await_operation(_api("DELETE", f"{BASE}/{PARENT}/services/{service_id}", token), token)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    parser.add_argument("--delete", action="store_true", help="remove the registrations")
    args = parser.parse_args()
    if args.check:
        return check()
    if args.delete:
        return delete()
    return register()


if __name__ == "__main__":
    raise SystemExit(main())
