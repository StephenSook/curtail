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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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

#: How far back the trace probe looks. Long enough that an ordinary session has run a
#: traversal, short enough that a stale trace is not read as current evidence.
TRACE_WINDOW_HOURS = 6

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


def _recent_trace() -> tuple[list[str], str | None]:
    """Span names from the most recent fleet traversal in Cloud Trace.

    **The fact sheet's telemetry claim is computed from SOURCE, and source cannot see
    whether a span ever landed.** Exactly the repo-versus-production gap this file
    exists for, one layer over: the exporter can be imported, installed and reached,
    and still export into a project that drops it.

    **The pagination quirk here cost a real diagnosis, so it is handled explicitly.**
    Cloud Trace v1 `traces.list` returns an EMPTY FIRST PAGE carrying a
    `nextPageToken`. A single-page query therefore reports zero traces for a project
    that has them, which is what happened: `export()` was returning
    `SpanExportResult.SUCCESS` while the check said nothing had arrived, and the
    verifier was the broken thing rather than the exporter. Follow the tokens.
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

    now = datetime.now(UTC)
    start = (now - timedelta(hours=TRACE_WINDOW_HOURS)).isoformat().replace("+00:00", "Z")
    end = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    base = (
        f"https://cloudtrace.googleapis.com/v1/projects/{REGISTRY_PROJECT}/traces"
        f"?startTime={start}&endTime={end}&view=COMPLETE&pageSize=100"
    )

    traces: list[dict[str, Any]] = []
    page_token = ""
    for _ in range(12):
        request = urllib.request.Request(f"{base}&pageToken={page_token}")
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return [], f"Cloud Trace could not be read: {exc}"
        traces.extend(payload.get("traces") or [])
        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break

    fleet = [
        t
        for t in traces
        if any(s.get("name") == "curtail.fleet_request" for s in (t.get("spans") or []))
    ]
    if not fleet:
        return [], (
            f"no fleet traversal was traced in the last {TRACE_WINDOW_HOURS} hours. "
            "This is not evidence that telemetry is broken: nobody may have run one."
        )
    newest = max(fleet, key=lambda t: min(s.get("startTime", "") for s in t["spans"]))
    return [str(s.get("name")) for s in newest["spans"]], None


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


def _live_capabilities() -> dict[str, object]:
    """What the RUNNING container can actually do, which env vars decide.

    **Written after a deploy silently disabled two of them.** `gcloud run deploy
    --set-env-vars` REPLACES the environment rather than adding to it, so stamping the
    revision wiped GOOGLE_CLOUD_PROJECT, the signing key and the demo passphrase in one
    command. The service stayed up, `/api/healthz` stayed `ok`, every route still
    answered, and the Season Ledger silently fell back to in-process memory. Liveness
    could not see it because nothing was down.

    So the probe asks the service what it can DO, not merely whether it responds.
    """
    out: dict[str, object] = {}
    try:
        with urllib.request.urlopen(f"{SERVICE_URL}/api/version", timeout=TIMEOUT_SECONDS) as r:
            body = json.loads(r.read().decode())
        out["stamped"] = bool(body.get("stamped"))
        out["revision"] = body.get("revision")
    except Exception as exc:
        out["stamped"] = False
        out["revision"] = None
        out["version_error"] = str(exc)[:160]
    try:
        with urllib.request.urlopen(
            f"{SERVICE_URL}/api/season/scott", timeout=TIMEOUT_SECONDS
        ) as r:
            body = json.loads(r.read().decode())
        out["durable"] = bool(body.get("durable"))
        out["store"] = str(body.get("store", ""))[:120]
    except Exception as exc:
        out["durable"] = False
        out["store"] = f"unreachable: {str(exc)[:120]}"
    return out


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
    caps = _live_capabilities()
    served_rev = caps.get("revision")
    add(f"- Service: {SERVICE_URL}")
    add(
        f"- Commit the container reports: `{served_rev}`"
        if served_rev
        else "- Commit the container reports: **NOT STAMPED**, so its vintage is unknown"
    )
    add(f"- Season Ledger durable in production: **{caps['durable']}**, store: {caps['store']}")
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
    # **These two live INSIDE the compared block on purpose.** They were in the header
    # first, where `--check` never looks, so a deployment that lost durability or its
    # stamp still reported "matches the live service". A capability check that cannot
    # see the capability is the shape this record exists to prevent.
    #
    # Booleans, not the revision string: whether the container can name its commit is a
    # stable fact, and WHICH commit changes on every deploy. Comparing the value would
    # redden the gate for the system working normally, which is how a real drift later
    # goes unnoticed.
    add(f"- Season Ledger durable in production: **{caps['durable']}**")
    add(f"- Container reports its own commit: **{'yes' if caps['stamped'] else 'no'}**")
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

    add("")
    add("## The most recent traversal in Cloud Trace")
    add("")
    spans, no_trace = _recent_trace()
    if no_trace is not None:
        add(no_trace)
        add("")
        add("Recorded as unknown rather than as a failure. The fact sheet's telemetry")
        add("claim is computed from source, and source cannot see whether a span landed;")
        add("this section is the other half of that question and it can only answer when")
        add("somebody has actually driven the fleet.")
    else:
        for name in spans:
            add(f"- `{name}`")
        add("")
        add(f"{len(spans)} spans. ADK opens `invoke_node` per fleet member and")
        add("`invoke_workflow` around the traversal; the root `curtail.fleet_request`")
        add("span carries the correlation id, so a trace and a log line join on one key.")
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


#: Where the compared region ends. Everything from here down is VOLATILE by design.
TRACE_SECTION = "## The most recent traversal in Cloud Trace"


def _capability_block(text: str) -> str:
    """The part of the record that must not drift, which is not all of it.

    Capabilities and the registry catalog are stable facts about a deployment, so a
    change there is real drift and `--check` should fail. **The trace section names the
    MOST RECENT traversal, so it changes every time anybody runs one.** Comparing it
    would make the check fail for the system working normally, and a gate that fails on
    correct behaviour is a gate people learn to override, which is how a real drift
    later goes unnoticed.

    So the comparison starts at the capabilities marker and stops at the trace section.
    Freshness for the volatile part is the timestamp in the header, not this check.
    """
    marker = "## Capabilities the repository may claim"
    _, _, tail = text.partition(marker)
    stable, _, _ = tail.partition(TRACE_SECTION)
    return stable.strip()


if __name__ == "__main__":
    raise SystemExit(main())
