"""Generate docs/FACTS.md from the code and the data. Never hand-edit the output.

Every number that appears in the video narration, the README, the landing page
and the Devpost description must come from this one file, and this file is
computed rather than written. That is not tidiness, it is the fix for a specific
failure: on a previous project the demo video quoted a build time from a memory
ledger while the README quoted a figure from a fresh code audit, the two
disagreed, and the video had already been published and could not be corrected.
A single generated source cannot diverge from itself.

The rule that follows from that: if a number is not in FACTS.md, it does not go
in an artifact. If it needs to be in an artifact, it gets computed here first.

Run:  uv run python scripts/generate_facts.py
Check without writing:  uv run python scripts/generate_facts.py --check
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "src"))

from curtail_core import penalties  # noqa: E402
from curtail_core.backtest import run as run_backtest  # noqa: E402
from curtail_core.basins import COMPLIANCE_GAGE, Basin  # noqa: E402
from curtail_core.flow_minimums import (  # noqa: E402
    NEAR_THRESHOLD_BAND_CFS,
    SCHEDULES,
)

OUT = REPO / "docs" / "FACTS.md"

#: The SAME content, inside the package, because the API serves it at runtime.
#:
#: A review found `/api/facts` broken in any packaged deployment: the endpoint
#: resolved the path relative to the repository, and a wheel contains only the
#: package, so an installed service would 503 forever. The first response was a
#: diagnostic 503 message, which is documenting a hole rather than closing it.
#:
#: Two copies would normally be two places to drift, and that is handled the way
#: everything else here is: ONE generator writes both and `--check` verifies both,
#: so a stale copy fails CI. Moving the file into the package is also the fix this
#: project already used for the citation allowlist, after a cross-root
#: force-include failed to build because uv builds from an sdist.
PACKAGED = REPO / "agents" / "src" / "curtail_agents" / "data" / "FACTS.md"
MANIFEST = REPO / "data" / "corpus_manifest.json"


def _schedule_rows(basin: Basin) -> list[str]:
    rows = []
    for period in SCHEDULES[basin]:
        start = f"{period.start_month:02d}-{period.start_day:02d}"
        end = f"{period.end_month:02d}-{period.end_day:02d}"
        rows.append(f"| {start} to {end} | {period.cfs:g} |")
    return rows


#: The fleet nodes, in the order the graph runs them.
FLEET_NODES = ("_sentinel", "_core", "_scribe", "_herald")

#: The domain function each node wraps. The nodes are ADK plumbing; these hold the logic.
NODE_LOGIC = {
    "_sentinel": "evaluate",
    "_core": "recommend",
    "_scribe": "draft_order",
    "_herald": "deliver_order",
}


def _calls_within(module: ast.Module) -> dict[str, set[str]]:
    """Every function each function invokes, keyed by function name.

    **Counts a name passed AS AN ARGUMENT to a call, and that is not a loosening.**
    The first version collected only `ast.Call` funcs, and it reported the Scribe and
    the Herald as not running their own domain functions on the commit that wired
    them. Both dispatch through `asyncio.to_thread(draft_order, ...)`, where the
    function is an argument and never syntactically called, so the checker was
    looking for a shape the correct code does not have.

    Restricted to arguments OF a call, so a bare mention in a docstring or an
    unrelated assignment does not count. Handing a function to something whose job is
    to run it is invoking it.
    """
    graph: dict[str, set[str]] = {}
    for fn in ast.walk(module):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        names: set[str] = set()
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call):
                continue
            if isinstance(call.func, ast.Name):
                names.add(call.func.id)
            names.update(a.id for a in call.args if isinstance(a, ast.Name))
        graph[fn.name] = names
    return graph


def _reaches(start: str, target: str, graph: dict[str, set[str]]) -> bool:
    """Whether `start` reaches `target`, following helpers in the same module.

    **Transitive on purpose, and the direct version would have been wrong here.** The
    Scribe node does not call `draft_order` in its own body: it calls `_sanitize` and
    then `_draft`, which makes the model call. A one-level check would have reported
    the Scribe as not carrying its own logic on the very commit that gave it that
    logic, which is a false claim in the safe-looking direction.
    """
    seen: set[str] = set()
    queue = [start]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        callees = graph.get(current, set())
        if target in callees:
            return True
        queue.extend(callees)
    return False


def _node_carries_its_logic() -> dict[str, bool]:
    """Whether each node actually reaches the domain function it is named for.

    A node can act on its input and still not do the job its name claims. The Scribe
    did exactly that for several revisions: it sanitized, which is real work, and
    never drafted, so `_is_passthrough` reported it as acting while the graph could
    not produce an order at all.
    """
    module = ast.parse((REPO / "agents" / "src" / "curtail_agents" / "fleet.py").read_text())
    graph = _calls_within(module)
    return {node: _reaches(node, fn, graph) for node, fn in NODE_LOGIC.items()}


def _console_runs_the_graph() -> bool:
    """Whether the deployed HTTP surface executes the fleet, or only its parts.

    Both names are required. Building a runner and never driving it would leave the
    graph as unexercised as before while making this read true, which is the shape of
    claim this file exists to prevent.
    """
    source = (REPO / "agents" / "src" / "curtail_agents" / "api.py").read_text()
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return {"build_fleet_runner", "run_fleet"} <= called


def _otel_claim() -> str:
    """Whether the shipped source EXPORTS telemetry, in three parts, all required.

    **"Imports opentelemetry" is the wrong question and would have been a false green.**
    ADK creates `invoke_node` and `invoke_workflow` spans by itself, so the library is
    present and spans exist whether or not anything routes them anywhere. An import
    check would have reported telemetry wired while every span was created and dropped,
    which is the structure-present-force-absent shape this project keeps finding.

    So three things are checked, and all three are required:

    1. the Cloud Trace exporter is imported by the shipped source,
    2. a tracer provider is actually INSTALLED (`set_tracer_provider`), because building
       one and never registering it exports nothing,
    3. the entrypoint CALLS the configuration, because a function nobody invokes is the
       same defect one layer out.

    Scoped to `agents/src`. `opentelemetry` also appears in `uv.lock` as a transitive
    dependency of `google-adk`, so a lockfile grep answers a different question.
    """
    shipped = "\n".join(path.read_text() for path in (REPO / "agents" / "src").rglob("*.py"))
    entrypoint = (REPO / "agents" / "src" / "curtail_agents" / "api.py").read_text()
    tree = ast.parse(entrypoint)
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }

    exporter = "opentelemetry.exporter.cloud_trace" in shipped
    installed = "set_tracer_provider" in shipped
    reached = "configure_tracing" in called

    if exporter and installed and reached:
        return (
            "OpenTelemetry export to Cloud Trace is WIRED: the shipped source imports "
            "the Cloud Trace exporter, installs a tracer provider, and the HTTP "
            "entrypoint calls `configure_tracing` at import. ADK opens an "
            "`invoke_node` span per fleet node and an `invoke_workflow` span around "
            "the traversal, so the agent-hop trace is a property of the graph. It "
            "exports only where a project id is present, and the fleet response says "
            "so either way."
        )
    missing = [
        name
        for name, ok in (
            ("the Cloud Trace exporter is not imported", exporter),
            ("no tracer provider is installed", installed),
            ("the entrypoint never calls `configure_tracing`", reached),
        )
        if not ok
    ]
    return (
        "No OpenTelemetry export: " + "; ".join(missing) + ". ADK creates spans "
        "regardless, so without this they are created and dropped."
    )


def _stamp_from(text: str, label: str) -> str:
    """Pull one stamped value out of the probe record, or say it is missing.

    Returns a legible placeholder rather than raising, because a record written before
    stamps existed should degrade to "unrecorded" rather than break generation. The test
    asserts the stamp IS present, so an unrecorded value fails there instead, which is
    the right place: generation should describe what it found, and the guard should
    decide whether that is acceptable.
    """
    for line in text.splitlines():
        if label in line:
            value = line.split(label, 1)[1].strip().strip("`")
            return value or "unrecorded"
    return "unrecorded"


def _scott_rights_claim() -> str:
    """The Scott table, counted from the committed record rather than described.

    Kept separate from the Shasta paragraph above because the two records answer
    different questions. Shasta's states priority dates and the ladder places each right;
    Scott's states the grouping and the ladder reads it. Collapsing them into one
    sentence would hide that difference, and the difference is the interesting part.
    """
    record = REPO / "data" / "rights_scott_addendum12.json"
    if not record.exists():
        return (
            "**The Scott rights table is NOT present**, so the Core cannot run on that "
            "basin. Generate it with `scripts/extract_scott_attachment_a.py`."
        )
    raw = json.loads(record.read_text())
    counts = raw["counts"]
    groups = ", ".join(
        f"group {k}: {v}" for k, v in sorted(counts["by_group"].items(), key=lambda kv: int(kv[0]))
    )
    return (
        "**The Scott rights table.** Read from the Board's own attachment to "
        f"{raw['source']['document']}, issued {raw['source']['issued']}, sha256 "
        f"`{raw['source']['sha256'][:16]}...`. {counts['rows_parsed']} rights, "
        f"reconciled against {counts['application_numbers_in_document']} application "
        "numbers found anywhere in the document. **The grouping is the Board's own "
        f"column, not this project's inference** ({groups}). {_inference_gap(raw)}"
    )


def _inference_gap(raw: dict[str, Any]) -> str:
    """How far attribute-based placement lands from the Board's own column, MEASURED.

    **The first version of this sentence carried the figures as literals**, which is the
    false-constant defect this repository found inside this very generator a day earlier:
    a hardcoded claim in generated output looks derived and drifts silently. So the
    comparison is run here, against the same ladder the Core uses.

    The rights are placed as bare appropriative, which is what a loader that read only
    the application numbers would produce.
    """
    from curtail_core.adjudications import RightClass, WaterRight
    from curtail_core.basins import Basin
    from curtail_core.priority import place

    total = len(raw["rights"])
    agreed = 0
    inferred_ranks: dict[int, int] = {}
    for row in raw["rights"]:
        placement = place(
            WaterRight(
                right_id=row["application_number"],
                basin=Basin.SCOTT,
                right_class=RightClass.APPROPRIATIVE,
            )
        )
        inferred_ranks[placement.rank] = inferred_ranks.get(placement.rank, 0) + 1
        if placement.rank == row["curtailment_group"]:
            agreed += 1

    biggest_rank, biggest_count = max(
        ((int(k), v) for k, v in raw["counts"]["by_group"].items()), key=lambda kv: kv[1]
    )
    where_inference_puts_them = ", ".join(
        f"group {r}: {n}" for r, n in sorted(inferred_ranks.items())
    )
    return (
        f"Placing these rights from attributes alone instead agrees with the Board on "
        f"{agreed} of {total}: inference lands them at {where_inference_puts_them}, "
        f"while the Board puts {biggest_count} in group {biggest_rank}. Group 1 is "
        "curtailed first and group 8 is curtailed nearly last, so the gap is not a "
        "rounding difference, it is the difference between a ranch irrigating and a "
        "ranch shutting off."
    )


def _armor_claim() -> str:
    """Whether Model Armor is CALLED, in three parts, all required.

    Same shape as the telemetry claim and for the same reason. A template existing in
    the cloud proves nothing about the shipped code, and this row read "provisioned,
    NOT called" for weeks while that was true. The question is whether the drafting
    path invokes it.

    1. the client module exists in the shipped source,
    2. the Scribe path CALLS it (a client nobody invokes is the structure-present
       shape this project keeps finding),
    3. the chaos drill exercises it too, so the claim is demonstrable rather than
       merely true.
    """
    shipped = "\n".join(path.read_text() for path in (REPO / "agents" / "src").rglob("*.py"))
    fleet = (REPO / "agents" / "src" / "curtail_agents" / "fleet.py").read_text()
    chaos = (REPO / "agents" / "src" / "curtail_agents" / "chaos.py").read_text()

    exists = "def screen_document(" in shipped
    called_by_scribe = "screen_document(" in fleet
    in_drill = "screen_document(" in chaos

    if exists and called_by_scribe and in_drill:
        return (
            "Model Armor is CALLED as layer 2: the Scribe path screens untrusted order "
            "text before drafting, chunked to stay inside the documented "
            "prompt-injection window, and an unreachable or partial screen reports "
            "UNAVAILABLE rather than clean. `make chaos` screens the same injection "
            "alone and embedded in an order and reports both verdicts."
        )
    missing = [
        name
        for name, ok in (
            ("no Model Armor client ships", exists),
            ("the Scribe path does not call it", called_by_scribe),
            ("the chaos drill does not exercise it", in_drill),
        )
        if not ok
    ]
    return "Model Armor is NOT fully wired: " + "; ".join(missing) + "."


def _registry_claim() -> str:
    """What the Agent Registry holds, read from the recorded probe.

    **This file computes from repository source, and repository source cannot see a
    registry.** The line this replaces was a hardcoded string saying no Curtail agent
    was registered. Four were registered, the sentence became false, and
    `--check` still passed because it compares this file against THIS GENERATOR: both
    were wrong together. **A generated-artifact check cannot catch a false constant
    inside the generator**, which is the sharper form of the rule that a hand-written
    line inside a generated file is the one line nothing checks.

    So the fact comes from `docs/DEPLOYMENT.md`, which a networked probe writes, and the
    provenance is stated rather than implied. A missing or unreadable record is reported
    as unknown, never as zero: an absent record and an empty registry are different
    claims.
    """
    record = REPO / "docs" / "DEPLOYMENT.md"
    if not record.exists():
        return (
            "Agent Registry contents UNKNOWN: `docs/DEPLOYMENT.md` has not been "
            "generated, and this file cannot query a registry."
        )
    text = record.read_text()
    if "The registry could not be read:" in text:
        return (
            "Agent Registry contents UNKNOWN: the last probe could not read the "
            "registry, and an unreadable registry is not an empty one."
        )
    stamp = _stamp_from(text, "Probed at:")
    revision = _stamp_from(text, "Serving revision at probe time:")
    provenance = (
        f"as recorded by `scripts/probe_deployment.py` at {stamp} against revision "
        f"{revision}. **This is a snapshot, not a live reading.** Nothing re-probes on "
        "its own and CI never queries the network, so run `make deployed-check` to "
        "re-probe and fail on drift before quoting this anywhere that cannot be "
        "corrected."
    )
    for line in text.splitlines():
        if line.endswith("Curtail agents are discoverable in the registry."):
            count = line.split()[0]
            # PAST tense on purpose. The previous wording said agents "are registered",
            # which asserts present external state from a stored snapshot, and nothing
            # in CI can refresh it. A claim scoped to a moment stays true; a
            # present-tense claim from a snapshot silently becomes false.
            return f"{count} Curtail agents were registered in Agent Registry, {provenance}"
    if "**No Curtail agent is registered.**" in text:
        return f"No Curtail agent was registered in Agent Registry, {provenance}"
    return "Agent Registry contents UNKNOWN: `docs/DEPLOYMENT.md` records no registry section."


def _is_passthrough(fn: ast.AST) -> bool:
    """Whether a node returns its input unchanged, ignoring its docstring.

    Computed rather than described. The README said three of these were placeholders
    for a while after they stopped being placeholders, and prose is the one thing no
    guard could check. A statement about the code that IS the code cannot drift.
    """
    body = list(fn.body)  # type: ignore[attr-defined]
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return (
        len(body) == 1
        and isinstance(body[0], ast.Return)
        and isinstance(body[0].value, ast.Name)
        and body[0].value.id == "node_input"
    )


def _http_reaches() -> set[str]:
    """Which node's logic the deployed HTTP surface actually invokes.

    Computed, because the hand-written version of this sentence was wrong twice over: it
    said the surface calls "the four node functions", and it calls THREE domain functions
    that the nodes wrap, while `deliver_order` has no call site at all. A count written by
    hand in a generated file is the one line nothing checks.
    """
    tree = ast.parse((REPO / "agents" / "src" / "curtail_agents" / "api.py").read_text())
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return {node for node, fn in NODE_LOGIC.items() if fn in called}


def _fleet_status() -> list[tuple[str, bool]]:
    tree = ast.parse((REPO / "agents" / "src" / "curtail_agents" / "fleet.py").read_text())
    found: dict[str, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in FLEET_NODES:
            found[node.name] = _is_passthrough(node)
    return [(name, found[name]) for name in FLEET_NODES if name in found]


def build() -> str:
    manifest: dict[str, Any] = json.loads(MANIFEST.read_text())
    status = manifest["extraction_status"]
    report = run_backtest()

    # Penalties are computed, never quoted, so the stale-regulation gap is a
    # result rather than a claim.
    eight_days = penalties.exposure(days_in_violation=8)

    lines: list[str] = []
    add = lines.append

    fleet = _fleet_status()
    rights_record: dict[str, Any] = json.loads(
        (REPO / "data" / "rights_shasta_addendum6.json").read_text()
    )

    add("# FACTS")
    add("")
    add("**Generated file. Do not edit by hand.** Regenerate with")
    add("`uv run python scripts/generate_facts.py`. CI fails if this file drifts")
    add("from the code that produces it.")
    add("")
    add("Every number in the demo video narration, the README, the landing page and")
    add("the Devpost description must come from this file and from nowhere else. A")
    add("figure sourced from a memory ledger or a research summary is exactly how a")
    add("published artifact ends up contradicting its own repository.")
    add("")
    # Deliberately NO commit SHA and no timestamp in this file.
    #
    # A generated artifact must be a pure function of its inputs, and git HEAD is
    # not one of its inputs. Embedding the commit made the file unsatisfiable:
    # generating at commit A wrote "commit A", committing produced commit B, and
    # the CI check then regenerated "commit B" and reported permanent staleness.
    # Provenance comes from `git log -- docs/FACTS.md`, which is authoritative
    # and costs nothing.
    add("")
    add("---")
    add("")

    # ---- 0. What is wired -------------------------------------------------------
    #
    # SYSTEM facts, computed, because the domain facts were generated and the system
    # claims were prose, and it was the prose that drifted: the README described three
    # fleet nodes as placeholders for a while after they had stopped being placeholders.
    # A stale disclaimer understates the project to a judge, which is the same defect as
    # overclaiming and just as wrong.
    add("## 0. What is wired")
    add("")
    add("Computed from the source, not described. Each node is inspected for whether it")
    add("returns its input unchanged.")
    add("")
    carries = _node_carries_its_logic()
    runs_graph = _console_runs_the_graph()
    header = (
        "| Fleet node | Acts on its input | Runs the function it is named for "
        "| Reached by the console |"
    )
    add(header)
    add("|---|---|---|---|")
    for name, passthrough in fleet:
        acts = "no, it is a pass-through" if passthrough else "yes"
        via = NODE_LOGIC[name]
        owns = f"yes, `{via}`" if carries[name] else f"**no**, `{via}` is never called"
        http = (
            "yes, the console runs the graph"
            if runs_graph
            else "**no**, the console does not run the graph"
        )
        add(f"| `{name.lstrip('_')}` | {acts} | {owns} | {http} |")
    add("")
    acting = sum(1 for _, p in fleet if not p)
    owning = sum(1 for name, _ in fleet if carries[name])
    add(
        f"{acting} of {len(fleet)} nodes act on their input and {owning} of {len(fleet)} run "
        f"the domain function they are named for."
    )
    add("")
    if runs_graph:
        add(
            "**The console runs the graph.** `POST /api/fleet/{basin}` builds the ADK "
            "runner and drives one real traversal, so every node above is exercised by "
            "clicking rather than only by the test suite. The response names which node "
            "produced each part, read from ADK's own attribution."
        )
    else:
        add(
            "**The console does not construct the ADK runner**, so the graph is exercised "
            "by the test suite rather than by clicking."
        )
    add("")
    add(
        "**The rights table.** Read from the Board's own attachment to "
        f"{rights_record['source']['document']}, issued "
        f"{rights_record['source']['issued']}, sha256 "
        f"`{rights_record['source']['sha256'][:16]}...`."
    )
    add("")
    accounting = rights_record["accounting"]
    add(f"- {accounting['application_numbers_seen']} application numbers seen")
    add(
        f"- {accounting['parsed']} rows parsed, "
        f"{accounting['imprecise']} imprecise, {accounting['ambiguous']} ambiguous, "
        f"{accounting['unparsed']} unparsed"
    )
    placement = rights_record["ladder_placement_counts"]
    placed = sum(placement.values())
    add(
        f"- {placed} placed on the priority ladder ("
        + ", ".join(f"{k}: {v}" for k, v in sorted(placement.items()))
        + f"), {len(rights_record['unplaceable'])} refused placement because the record"
    )
    add("  states no priority precise enough to establish decree membership")
    add("")
    add(_scott_rights_claim())
    add("")
    add("**Not wired in the DEPLOYED service, named so it cannot be implied away.**")
    add("")
    missing = sorted(n.lstrip("_") for n, ok in carries.items() if not ok)
    if missing:
        verb = "does" if len(missing) == 1 else "do"
        add(
            f"- {', '.join(f'`{n}`' for n in missing)} {verb} not call the domain "
            "function it is named for, so the node is plumbing around an act it does "
            "not perform."
        )
    add("- The session service is in memory and built PER REQUEST, so nothing a traversal")
    add("  records survives the response and no season state persists in production. A")
    add("  test injects a real `DatabaseSessionService` and proves the ledger round-trips")
    add("  across a restart, which is a different claim about a different deployment.")
    add("- No Cloud SQL, and the approval queue lives in the serving process.")
    add("- No Pub/Sub broker, and no delivery vendor: the transport is explicitly")
    add("  synthetic and every report says so.")
    add(f"- {_otel_claim()}")
    add(f"- {_armor_claim()}")
    add(f"- {_registry_claim()}")
    add("")

    add("## 1. The backtest")
    add("")
    add(f"> {report.headline}")
    add("")
    add("The denominator is what was actually scored. Refusals and exclusions are")
    add("reported alongside rather than folded in.")
    add("")
    add("| Case | Basin | Date | Reading | Minimum | Board | Engine | Outcome |")
    add("|---|---|---|---|---|---|---|---|")
    for r in report.results:
        minimum = f"{r.minimum_cfs:g}" if r.minimum_cfs is not None else "n/a"
        board = r.board_direction.value if r.board_direction else "n/a"
        engine = r.engine_direction.value if r.engine_direction else "n/a"
        add(
            f"| `{r.case_id}` | {r.basin.value} | {r.decision_date.isoformat()} | "
            f"{r.reading_cfs:g} cfs | {minimum} cfs | {board} | {engine} | "
            f"**{r.outcome.value}** |"
        )
    add("")
    add(f"Excluded before scoring: {len(report.excluded)}.")
    add("")
    for item in report.excluded:
        add(f"- `{item['id']}`: {item['reason']}")
    add("")
    add("**What this does not claim.** The engine determines the priority grouping or")
    add("tier to which curtailment must extend to provide reasonable assurance of")
    add("meeting the drought emergency minimum flow at the compliance gage. It does")
    add("not derive the cutoff dates; those are decree-defined tiers. A divergence is")
    add("not automatically an engine error, because 23 CCR 875(b)(3) permits the")
    add("official to decline to issue, to narrow the grouping, or to suspend.")
    add("")

    add("## 2. The corpus")
    add("")
    add("| Measure | Count |")
    add("|---|---|")
    add(f"| Declared across the Board's index pages | {status['documents_total_declared']} |")
    add(
        f"| Individually enumerated in the manifest | {status['records_individually_enumerated']} |"
    )
    add(f"| PDFs fetched and byte-verified | {status['pdfs_fetched']} |")
    add(f"| Read via text layer | {status['read_via_text_layer']} |")
    # No .get default. Every sibling row uses [], and a silent 0 here would
    # publish a table that does not add up, because the Scorable row still
    # counts these documents.
    add(f"| Read from rendered pages | {status['read_via_vision']} |")
    add(f"| Refused, no text layer | {status['refused_no_text_layer']} |")
    add(f"| **Scorable** | **{status['scorable']}** |")
    add("")
    add(
        'Never state a figure of the form "N of '
        f'{status["documents_total_declared"]}". The denominator of any claim is'
    )
    add("the SCORABLE count, because that is what has actually been read. The gap")
    add("between declared and scorable is reported here rather than folded away.")
    add("")
    add("The 2021 series is now individually enumerated. The Board does publish")
    add("per-addendum links for it, on two index pages the main drought index does")
    add("not surface: `scott_addendums.html` (51 Scott) and `shasta_addendums.html`")
    add("(14 Shasta). An earlier premise that no such links existed was wrong, and")
    add("the Shasta count was corrected from 16 to 14 against that page.")
    add("")
    add("**Measured:** 4 of the 25 documents in the 2024 series carry no text layer")
    add("at all, roughly 16 percent. `pdftotext` returns three or four bytes. That")
    add("is")
    add("what makes a vision model load-bearing on this project rather than")
    add("decorative: it is the only way to read those documents, and one of them is")
    add("the July 2025 fixture the entry is built around.")
    add("")

    add("## 3. The July 2025 sequence")
    add("")
    add("Both documents are scans and were read from their rendered pages.")
    add("")
    add("| | Addendum 7 | Addendum 8 |")
    add("|---|---|---|")
    add("| When | 2025-07-20 21:30 | 2025-07-22 07:30 |")
    add("| Fort Jones reading | **48.7 cfs** | **78.4 cfs** |")
    reinstate_cell = "Reinstate, all surface water and groundwater diverters"
    add(f"| Action | {reinstate_cell} | Suspend all curtailments |")
    add("| Signed | not captured | Erik Ekdahl, Chief Deputy Director |")
    add("")
    add("The river did not rise. The Scott Valley and Shasta Valley Watermaster")
    add("District measured, USGS revised the rating curve upward, and the same water")
    add("read 48.7 cfs on Sunday night and 78.4 cfs on Tuesday morning. Addendum 8,")
    add('verbatim: "Several community members expressed concern regarding the')
    add("accuracy of the measurement, and USGS has revised its flow measurements")
    add("upward based on measurements taken by the Scott Valley and Shasta Valley")
    add('Watermaster District (Watermaster)."')
    add("")
    add("At 48.7 cfs the engine recommends curtailment and raises a near-threshold")
    add(f"flag, because the reading sits within the {NEAR_THRESHOLD_BAND_CFS:g} cfs band")
    add("AROUND the minimum. The band is symmetric in the code: a reading just")
    add("above the line is flagged too, since a decision to release is as worth")
    add("checking as a decision to curtail. An earlier version of this sentence")
    add("described it as one-sided, which the implementation never was.")
    add("")
    add('**Do not use "above 75 cfs"** in any artifact. That figure comes from the')
    add("August 5 2025 Executive Director's Report paraphrasing the event. The")
    add("Addendum itself states 78.4 cfs.")
    add("")

    add("## 4. Penalties, and the gap the regulation still prints")
    add("")
    add("Computed from Water Code 1846(b) as amended by AB 460 (Stats. 2024, Ch. 342),")
    add("effective January 1, 2025.")
    add("")
    add(f"- Statutory exposure, 8 days of violation: **${eight_days.statutory_total_maximum:,}**")
    add("- The same 8 days computed from 23 CCR 875.9(b), which still prints $500 per")
    add(f"  day: **${eight_days.regulation_total:,}**")
    add(f"- Understatement factor: **{eight_days.understatement_multiple:g}x**")
    add("")
    add("The published regulation is stale on its face while the statute has moved. A")
    add("system computing liability from the regulation text would be wrong by that")
    add("factor. The multiple is computed from the two figures, not asserted.")
    add("")

    add("## 5. A second live drafting gap, found in the corpus")
    add("")
    add('Scott Addendum 6, issued **November 13, 2024**, states: "Flows at the USGS')
    add("Fort Jones gage have been at or above the **September flow requirement (60")
    add('cfs)**". Under 23 CCR 875 the Scott September minimum is **33 cfs**; 60 cfs')
    add("is **November's** figure. Scott Addendum 3 states September as 33 cfs in the")
    add("same order series, so two addenda in one series disagree about September,")
    add("and the number matches the month the addendum was issued in rather than the")
    add("month it names.")
    add("")

    add("## 6. Flow minimums, as encoded and as confirmed")
    add("")
    add("Date-period bounded, not month keys. Scott changes mid-month on June 24;")
    add("Shasta on March 25 and September 16.")
    add("")
    for basin in (Basin.SCOTT, Basin.SHASTA):
        add(f"### {basin.value.title()} at `{COMPLIANCE_GAGE[basin]}`")
        add("")
        add("| Period | cfs |")
        add("|---|---|")
        lines.extend(_schedule_rows(basin))
        add("")
    add("Independently confirmed from the documents: Scott September 33 cfs")
    add("(Addendum 3), Scott October 40 cfs (Addendum 5), Scott July 50 and August 30")
    add("cfs (Addendum 7), Shasta October 105 cfs (Addendum 2), Shasta October 105 and")
    add("November 125 cfs (Addendum 3). Every month stated in a 2024-era addendum")
    add("matches the encoded 2025-readopted schedule.")
    add("")

    add("## 7. Open, and stated as open")
    add("")
    add("Every item is rendered, routed by status. Nothing is filtered out.")
    add("")
    add("An adversarial review found the previous version published only items")
    add("whose text began with the literal string OPEN, which silently dropped")
    add("three genuinely unresolved items written without the prefix, plus one")
    add("stale item that should have been retired rather than hidden. In a")
    add("project whose thesis is that undisclosed gaps are the failure mode, a")
    add('section headed "stated as open" that omitted open items because of a')
    add("formatting convention was the worst defect in this file.")
    add("")

    items = json.loads(MANIFEST.read_text())["open_items"]
    known = ("OPEN", "RESOLVED", "CORRECTED", "PROGRESS")
    unclassifiable = [i for i in items if not i.startswith(known)]
    if unclassifiable:
        # Loud, not silent. An item nobody can classify is exactly the item that
        # would otherwise vanish from the published gap list.
        raise ValueError(
            f"{len(unclassifiable)} manifest open_items carry no recognised "
            f"status prefix and would not be routed: {unclassifiable[:3]}"
        )

    still_open = [i for i in items if i.startswith("OPEN")]
    closed = [i for i in items if i.startswith(("RESOLVED", "CORRECTED", "PROGRESS"))]

    add(f"### Still open ({len(still_open)})")
    add("")
    for item in still_open:
        add(f"- {item}")
    add("")
    add(f"### Closed, kept for provenance ({len(closed)})")
    add("")
    for item in closed:
        add(f"- {item}")
    add("")
    add(f"_All {len(items)} items accounted for: {len(still_open)} open, {len(closed)} closed._")
    add("")
    add(
        "_Provenance: run `git log -- docs/FACTS.md` for when this was last "
        "regenerated, and `git log -- data/ core/src/` for the inputs it was "
        "computed from._"
    )
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = parser.parse_args()

    content = build()

    if args.check:
        if not PACKAGED.exists():
            print(
                f"{PACKAGED.relative_to(REPO)} does not exist, so the API would serve "
                "a 503 in every packaged deployment. Run without --check to generate."
            )
            return 1
        if PACKAGED.read_text() != content:
            print(
                f"{PACKAGED.relative_to(REPO)} is STALE. The API serves this copy, so "
                "it would hand a judge a figure the repository no longer supports.\n"
                "Regenerate with:\n  uv run python scripts/generate_facts.py"
            )
            return 1
        if not OUT.exists():
            print("docs/FACTS.md does not exist. Run without --check to generate it.")
            return 1
        if OUT.read_text() != content:
            print(
                "docs/FACTS.md is STALE. It no longer matches the code and data that\n"
                "produce it, which means an artifact quoting it may now be quoting a\n"
                "number the repository does not support. Regenerate with:\n"
                "  uv run python scripts/generate_facts.py"
            )
            return 1
        print("docs/FACTS.md and the packaged copy are current.")
        return 0

    for target in (OUT, PACKAGED):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        print(f"wrote {target.relative_to(REPO)} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
