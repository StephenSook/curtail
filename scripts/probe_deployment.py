"""Probe the DEPLOYED service and record what it actually serves.

**FACTS.md computes its claims from repository source, and repository source cannot
see that production is stale.** That gap is not hypothetical: the commit wiring the
console through the ADK graph made "the console runs the graph" true of the repo and
left it false of the live URL a judge would visit, because nothing in this project
deploys on merge. Every gate was green and the deployed service returned 404 on the
endpoint the README had just started advertising.

So the check is split, the same way the quote-provenance check is split, and for the
same reason: one half needs the network and the other half must run in CI.

- THIS script needs the network. It probes the live service, reads the routes it
  advertises, and writes `docs/DEPLOYMENT.md`.
- The TEST needs no network. It reads that record and fails when the repository
  claims a capability the recorded deployment does not serve.

CI therefore never depends on production being reachable, which matters because a
deliberately powered-down service must not turn the build red. A red monitor is a
claim about the world, not about the monitor.

Run:  uv run python scripts/probe_deployment.py
Check without writing:  uv run python scripts/probe_deployment.py --check
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "DEPLOYMENT.md"

#: The judge-facing service. Named here rather than passed in, because a record that
#: does not say which host it describes is not evidence about anything.
SERVICE_URL = "https://curtail-console-api-672785135387.us-central1.run.app"

#: What the repository may claim only if the deployment serves it. Each entry is a
#: capability sentence paired with the route that settles it.
#:
#: Keyed on the route rather than on prose, because prose is what drifts. The claim
#: column exists so the failure message can name the sentence a reader would have to
#: delete, rather than leaving them to guess which README paragraph is now false.
CLAIMED_ROUTES = {
    "/api/fleet/{basin}": "the console runs the graph",
    "/api/queue/{order_id}/sign": "an officer signs the order",
    "/api/facts": "the service serves its own fact sheet",
}

TIMEOUT_SECONDS = 30

#: The Agent Registry the fleet is cataloged in. Same project and region as the service,
#: which the governance ADR requires: Registry, Gateway and app co-located.
REGISTRY_PROJECT = "curtail-505118"
REGISTRY_LOCATION = "us-central1"

#: Labels other artifacts read the stamp back by. Named constants because a test asserts
#: the stamp survives, and a guard keyed on a phrase that a later edit rewords is a guard
#: that quietly stops guarding.
STAMP_LABEL = "Probed at:"
REVISION_LABEL = "Serving revision at probe time:"


def _now() -> str:
    """Probe time, UTC, to the second.

    Deliberately in the HEADER rather than in the capability or registry sections: those
    are compared byte for byte by `--check`, and a timestamp inside them would make every
    check fail on the clock alone, which is how a gate teaches people to override it.
    """
    return datetime.now(UTC).isoformat(timespec="seconds")


def _serving_revision() -> str:
    """Which Cloud Run revision was serving when this was probed.

    **The commit alone does not identify what was probed.** A repository commit says when
    the record was WRITTEN; the revision says what ANSWERED. They come apart routinely,
    because nothing here deploys on merge, and the whole reason this file exists is that
    the repo and production drift.
    """
    try:
        out = subprocess.run(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                "curtail-console-api",
                "--region",
                REGISTRY_LOCATION,
                "--project",
                REGISTRY_PROJECT,
                "--format=value(status.traffic[0].revisionName)",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        ).stdout.strip()
        return out or "unknown"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


def _head_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _registered_agents() -> tuple[list[dict[str, str]], str | None]:
    """The Curtail agents the live Agent Registry holds, or the reason it cannot be read.

    The registry is external state, exactly like the deployed service, so the same split
    applies: this half needs the network and a token, and the test that reads the record
    needs neither. The governance table is a claim about what is RUNNING, and it said
    "Reachable, not yet populated" for days while that was true. Once it stopped being
    true, only something that queries the registry could notice.
    """
    try:
        token = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return [], f"no gcloud access token ({type(exc).__name__})"

    url = (
        f"https://agentregistry.googleapis.com/v1/projects/{REGISTRY_PROJECT}"
        f"/locations/{REGISTRY_LOCATION}/agents"
    )
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return [], f"the registry answered {response.status}"
            listed = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [], f"the registry could not be read: {exc}"

    found = []
    for agent in listed.get("agents") or []:
        name = str(agent.get("displayName", ""))
        if not name.startswith("Curtail "):
            continue
        urls = [
            i.get("url", "")
            for protocol in agent.get("protocols") or []
            for i in protocol.get("interfaces") or []
        ]
        found.append(
            {
                "displayName": name,
                "url": urls[0] if urls else "",
                "skills": ", ".join(s.get("id", "?") for s in agent.get("skills") or []),
            }
        )
    return sorted(found, key=lambda a: a["displayName"]), None


def _served_routes() -> tuple[list[str], str | None]:
    """The paths the live service advertises, and the reason if it cannot be read.

    **A failed probe is never recorded as an empty deployment.** An unreachable host
    and a host serving nothing produce the same list, and writing that list would turn
    a network error into the published claim that production serves no routes. The
    reason is carried instead, and the test treats a record carrying a reason as
    unknown rather than as absence.
    """
    try:
        with urllib.request.urlopen(
            f"{SERVICE_URL}/openapi.json", timeout=TIMEOUT_SECONDS
        ) as response:
            if response.status != 200:
                return [], f"the service answered {response.status} for its OpenAPI document"
            spec = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [], f"the service could not be read: {exc}"

    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        return [], "the OpenAPI document carried no paths, so nothing was learned"
    return sorted(paths), None


def build() -> str:
    routes, unreachable = _served_routes()
    sha = _head_sha()
    lines: list[str] = []
    add = lines.append

    add("# What the DEPLOYED service serves")
    add("")
    add("Generated by `scripts/probe_deployment.py`. Do not hand-edit: a hand-written")
    add("line inside a generated file is the one line nothing checks.")
    add("")
    add("This file exists because `docs/FACTS.md` computes its claims from repository")
    add("source, and repository source cannot see that production is stale. Nothing here")
    add("deploys on merge, so the repository can be correct and the live URL behind it.")
    add("")
    add(f"- Service: {SERVICE_URL}")
    add(f"- Probed at repository commit: `{sha}`")
    add(f"- {STAMP_LABEL} `{_now()}`")
    add(f"- {REVISION_LABEL} `{_serving_revision()}`")
    add("")
    add("**This is a SNAPSHOT, and everything below is historical.** Nothing re-probes on")
    add("its own, and CI deliberately never queries the network, so this record describes")
    add("the moment above and not necessarily the moment you are reading it. Run")
    add("`make deployed-check` to re-probe and fail on drift; that is the freshness")
    add("mechanism, and it must be run before recording the demo or submitting.")
    add("")
    add("**The fact sheet served at `/api/facts` names the probe BEFORE its own deploy,")
    add("and that is structural rather than a mistake.** `docs/FACTS.md` quotes the stamp")
    add("above, the packaged copy of it ships inside the container, and deploying that")
    add("container necessarily creates a revision newer than the one the stamp names. A")
    add("served sheet can never name its own serving revision. It is labelled as a probe")
    add("record for that reason, so a newer revision serving it contradicts nothing.")
    add("")

    if unreachable is not None:
        add("## The probe did not reach the service")
        add("")
        add(f"Reason: {unreachable}")
        add("")
        add("**This is recorded as unknown, not as absence.** An unreachable host and a")
        add("host serving nothing are indistinguishable from here, and publishing the")
        add("second would turn a network error into a claim. The parity test treats this")
        add("record as carrying no information rather than as evidence of a gap.")
        return "\n".join(lines) + "\n"

    add("## Routes the live service advertises")
    add("")
    for route in routes:
        add(f"- `{route}`")
    add("")
    add(f"{len(routes)} routes.")
    add("")
    add("## Capabilities the repository may claim")
    add("")
    add("| Capability | Route that settles it | Served |")
    add("|---|---|---|")
    for route, claim in sorted(CLAIMED_ROUTES.items(), key=lambda pair: pair[1]):
        served = "yes" if route in routes else "**no**"
        add(f"| {claim} | `{route}` | {served} |")
    add("")
    missing = sorted(claim for route, claim in CLAIMED_ROUTES.items() if route not in routes)
    if missing:
        add("**Claims the deployment does not support:** " + ", ".join(missing) + ".")
        add("")
        add("Either redeploy, or delete the sentence. A claim a judge cannot exercise on")
        add("the live URL scores as absent and reads worse than an honest omission.")
    else:
        add("Every capability the repository claims is reachable on the live service.")

    add("")
    add("## Agents cataloged in the live Agent Registry")
    add("")
    agents, unreadable_registry = _registered_agents()
    if unreadable_registry is not None:
        add(f"The registry could not be read: {unreadable_registry}.")
        add("")
        add("**Recorded as unknown, not as empty.** An unreadable registry and an empty")
        add("one are indistinguishable from here, and publishing the second would turn a")
        add("missing credential into the claim that nothing is registered.")
    elif not agents:
        add("**No Curtail agent is registered.** The governance table must say so.")
    else:
        add("| Agent | Skills | Route that reaches it |")
        add("|---|---|---|")
        for agent in agents:
            add(f"| {agent['displayName']} | `{agent['skills']}` | {agent['url']} |")
        add("")
        add(f"{len(agents)} Curtail agents are discoverable in the registry.")
        add("")
        add("Each carries the route that actually reaches it. The registry enforces")
        add("unique interface URLs, so four agents cannot share one address, and that")
        add("constraint is correct: an address identifies an agent. Herald has no direct")
        add("route because `deliver_order` has no HTTP call site; it is reached only as")
        add("the final node of a full traversal, and its card says exactly that.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed record differs from a fresh probe",
    )
    args = parser.parse_args()

    fresh = build()
    if not args.check:
        OUT.write_text(fresh)
        print(f"wrote {OUT.relative_to(REPO)}")
        return 0

    if not OUT.exists():
        print(f"{OUT.relative_to(REPO)} does not exist. Run without --check.", file=sys.stderr)
        return 1
    # The commit line differs on every commit by construction, so comparing whole
    # files would make this permanently red. What must not drift is the capability
    # table, which is the part a reader relies on.
    if _capability_block(OUT.read_text()) != _capability_block(fresh):
        print(f"{OUT.relative_to(REPO)} disagrees with the live service.", file=sys.stderr)
        return 1
    print(f"{OUT.relative_to(REPO)} matches the live service.")
    return 0


def _capability_block(text: str) -> str:
    marker = "## Capabilities the repository may claim"
    _, _, tail = text.partition(marker)
    return tail.strip()


if __name__ == "__main__":
    raise SystemExit(main())
