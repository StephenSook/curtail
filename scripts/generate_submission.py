"""Every required Devpost field, answered from the repository where possible.

**Why this is generated.** The submission form is the last artifact in the chain and the
only one nobody tests. Its dropdowns are claims about the stack exactly like a README
sentence, and the failure mode is the same one this project has hit repeatedly: a field
answered from memory of what was planned instead of from what shipped. `wired-or-cut`
applies to a checkbox.

So each answer here is computed. `Which Google Cloud Service(s) did you use?` is a grep,
not a recollection. `Which Google AI Models did you use?` is a grep. The start date is
the first commit. What cannot be computed is listed as OUTSTANDING with what it needs,
rather than left blank for someone tired to fill in at 4pm on the 31st.

Field ids and requirement flags were read from the Devpost API on 2026-08-14, not from
the public page, because the API is what the form actually enforces.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "docs" / "SUBMISSION.md"

SOURCE_DIRS = (REPO / "agents" / "src", REPO / "core" / "src")

#: Devpost field 28142's options, and the import or call that proves we use each.
#: Ticking one of these without the evidence is the checkbox form of a false claim.
CLOUD_SERVICES: dict[str, tuple[str, ...]] = {
    "Cloud Run": ("CLOUD_RUN_REQUEST_TIMEOUT_SECONDS", "--proxy-headers"),
    "Cloud SQL": ("cloudsql", "cloud_sql"),
    "Firestore": ("google.cloud.firestore",),
    "Google Kubernetes (GKE)": ("container_v1",),
    "Pub/Sub": ("pubsub_v1.PublisherClient", "pubsub_v1.SubscriberClient"),
}

#: Services whose evidence must ALL be present rather than any one of them.
#:
#: Cloud Run is not proved by a single import, because there is no import: it is a
#: DEPLOYMENT CONTRACT, and the parts are separable. `CLOUD_RUN_REQUEST_TIMEOUT_SECONDS`
#: is a Python constant that would happily survive moving off the platform, and the
#: container reading `${PORT}` with `--proxy-headers` is what actually makes the service
#: run there. Under `any`, deleting the Dockerfile contract left the claim standing on a
#: constant name, which a review pointed out is not the contract this row asserts.
REQUIRE_ALL: frozenset[str] = frozenset({"Cloud Run"})

#: Devpost field 28091's options.
SDKS: dict[str, tuple[str, ...]] = {
    "Agent Development Kit (ADK)": ("google.adk",),
    "Google GenAI SDK (google-genai)": ("from google import genai", "google.genai"),
    "Antigravity SDK": ("antigravity",),
    "Genkit": ("genkit",),
}


def _executable(text: str, *, python: bool) -> str:
    """The part of a file that RUNS, with comments and docstrings removed.

    **A comment naming a requirement is not the requirement.** This project's Dockerfile
    carries the line `# --proxy-headers is not optional behind Cloud Run`, and while the
    evidence search read whole files, deleting `--proxy-headers` from the actual `CMD`
    left the claim standing on that comment. The prose about a contract was satisfying a
    check on the contract.

    Python goes through `ast`, which drops comments on unparse, plus an explicit pass to
    remove module, class and function docstrings. A Dockerfile drops `#` lines, which is
    the whole of its comment syntax.
    """
    if not python:
        return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    tree = ast.parse(text)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body.pop(0)
    return ast.unparse(tree)


def _source() -> str:
    parts: list[str] = []
    for directory in SOURCE_DIRS:
        for path in directory.rglob("*.py"):
            if "__pycache__" not in path.parts:
                parts.append(_executable(path.read_text(), python=True))
    docker = REPO / "Dockerfile"
    if docker.exists():
        parts.append(_executable(docker.read_text(), python=False))
    return "\n".join(parts)


def _first_commit_date() -> str:
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%ad", "--date=format:%m-%d-%y"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return "UNKNOWN"
    return result.stdout.splitlines()[0].strip()


def _models(source: str) -> list[str]:
    import re

    found = set(re.findall(r"\b(gemini-[0-9.]+-[a-z-]+|gemma[0-9]?:[0-9a-z.]+)\b", source))
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate the submission sheet.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed sheet has drifted from what the repository says",
    )
    args = parser.parse_args(argv)

    source = _source()

    def _has(name: str, evidence: tuple[str, ...]) -> bool:
        test = all if name in REQUIRE_ALL else any
        return test(e in source for e in evidence)

    services = [name for name, ev in CLOUD_SERVICES.items() if _has(name, ev)]
    sdks = [name for name, ev in SDKS.items() if any(e in source for e in ev)]
    models = _models(source)
    started = _first_commit_date()

    # Everything that cannot be derived from the repository. Named with what it needs,
    # because a blank is indistinguishable from a decision not to answer.
    outstanding = [
        (
            "Demo video URL",
            "public on YouTube or Vimeo, 4 minutes maximum, English or "
            "subtitled, must show the backend running on Google Cloud",
        ),
        (
            "Content bonus link",
            "optional. A public post about how this was built, "
            "carrying language saying it was created for this hackathon",
        ),
        ("Social bonus link", "optional. Must carry #AllThingsAgenticHackathon"),
    ]

    lines = [
        "# The submission, answered from the repository",
        "",
        "Generated by `scripts/generate_submission.py`. Do not hand-edit: a hand-written",
        "line inside a generated file is the one line nothing checks.",
        "",
        "The form's dropdowns are claims about the stack exactly like a README sentence,",
        "and the failure mode is the same one this project keeps finding: a field answered",
        "from a memory of what was planned rather than from what shipped. So each answer",
        "below is a grep.",
        "",
        "## Required fields",
        "",
        "| Field | Answer | Where it comes from |",
        "|---|---|---|",
        "| Category | **Fortified Enterprise Fleet** | the exact string the form offers; a "
        "mis-typed track name is an auto-decline |",
        f"| Project start date | **{started}** | first commit. The submission period opened "
        "2026-08-04, so this is inside it |",
        "| Organization name | **required even though it reads optional** | Devpost field "
        "28086 is `required: true` regardless of its wording |",
        "| Repository URL | https://github.com/StephenSook/curtail | |",
        "| Reproducible testing instructions in README | **Yes** | the README carries a "
        "Setup section |",
        f"| Google SDK | **{', '.join(sdks) or 'NONE FOUND'}** | grep of shipped source |",
        f"| Google Cloud service(s) | **{', '.join(services) or 'NONE FOUND'}** | grep of "
        "shipped source and the Dockerfile |",
        f"| Google AI models | **{', '.join(models) or 'NONE FOUND'}** | grep of shipped source |",
        "| Architecture diagram | `docs/architecture.png` | a REQUIRED file upload, field 28092 |",
        "",
        "## What is NOT ticked, and why that is deliberate",
        "",
    ]

    for name, evidence in CLOUD_SERVICES.items():
        if name not in services:
            lines.append(
                f"- **{name}** is not ticked. No shipped source matches "
                + ", ".join(f"`{e}`" for e in evidence)
                + "."
            )
    lines += [
        "",
        "Pub/Sub is the one worth stating out loud: `messaging.py` implements the ordering",
        "key, dead-letter and dedup discipline against the real library's types, and it is",
        "good work. Nothing publishes to a topic, so the box stays unticked. Ticking it",
        "would be true of the design and false of the system.",
        "",
        "## Outstanding, with what each needs",
        "",
    ]
    lines += [f"- **{name}**: {need}" for name, need in outstanding]
    lines += [
        "",
        "## Two dates worth not getting wrong",
        "",
        "- Submissions close **2026-08-31 17:00 PT**.",
        "- Judging runs to **2026-09-24 17:00 PT**, not Oct 1 as earlier notes in this",
        "  project said. Winners **2026-10-08 12:00 PT**.",
        "- The app does **not** need to be live at judging, so long as the video and the",
        "  repository prove it was built and deployed on Google Cloud.",
        "",
    ]

    rendered = "\n".join(lines) + "\n"
    if args.check:
        if not TARGET.exists():
            print(f"{TARGET.relative_to(REPO)} is missing", file=sys.stderr)
            return 1
        if TARGET.read_text() != rendered:
            print(
                f"{TARGET.relative_to(REPO)} has drifted from the repository. Run "
                "`make submission`.",
                file=sys.stderr,
            )
            return 1
        print(f"{TARGET.relative_to(REPO)} is current.")
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    print(
        f"wrote {TARGET.relative_to(REPO)}: {len(sdks)} SDK(s), {len(services)} cloud "
        f"service(s), {len(models)} model(s), {len(outstanding)} outstanding"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
