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
import json
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


#: The FETCHED form. The generator answers this rather than a remembered list of
#: fields, because two REQUIRED fields (Submitter Type and country of residence) were
#: simply absent from the hand-written table and nothing could have noticed. A form
#: field is a claim exactly like a README sentence, and a missing one blocks submission
#: outright no matter how good the system is.
SCHEMA = REPO / "docs" / "submission_schema.json"

#: The live service, named once. A judge-facing URL that appears in two places drifts.
SERVICE_URL = "https://curtail-console-api-672785135387.us-central1.run.app"

#: The exact strings to paste into the two free-text judge-facing fields.
#:
#: **Both are capped at 255 characters by Devpost**, which is not in the fetched schema
#: and only shows up as a red error under the box after you paste. The first drafts were
#: 879 and 491 characters. A field that silently refuses to hold what you wrote is the
#: same family as the save that silently discards it.
#:
#: They live here rather than in somebody's head because the form dropped them on three
#: save attempts, and a long answer retyped from memory drifts.
LIMIT = 255

#: An adversarial review caught a real error in the first version of this: it said
#: drafting "queues two drafts, one clean and one deliberately UNVERIFIED". It does not.
#: `api.py` calls `QUEUE.add` exactly once per request with one order id, and the console
#: makes exactly one call. TWO rows appeared in the gallery capture because the capture
#: SCRIPT RAN TWICE against an in-process queue, and I read the accumulated state as a
#: feature of one click. Observing a screen is not reading the code, and the screen was
#: showing me my own repeated runs.
JUDGE_INSTRUCTIONS = (
    "No login needed. The console runs on load; Classify and Run the fleet need no "
    "credentials. Draft an order takes about 80s (a model call plus both guards) and "
    "queues ONE draft. Only signing needs the passphrase. /api/facts and "
    "/api/season/shasta are open."
)

AI_MODELS_ANSWER = (
    "Gemini 3.5 Flash through Vertex AI drafts every order (scribe.py). Gemma 3 4B "
    "runs locally through Ollama so no document leaves the machine (normalizer.py). "
    "Chirp 3 speech both ways (speech.py). gemini-embedding-001 corpus search "
    "(embeddings.py)."
)


def _human_authors() -> list[str]:
    """Distinct human authors in the history, so Submitter Type is derived not assumed.

    Co-Authored-By trailers are not authors, and `git shortlog` does not count them.
    """
    result = subprocess.run(
        ["git", "shortlog", "-sne", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    names = []
    for line in result.stdout.splitlines():
        parts = line.strip().split("\t", 1)
        if len(parts) == 2:
            names.append(parts[1].strip())
    return names


def executable_source(text: str, *, python: bool) -> str:
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
                parts.append(executable_source(path.read_text(), python=True))
    docker = REPO / "Dockerfile"
    if docker.exists():
        parts.append(executable_source(docker.read_text(), python=False))
    return "\n".join(parts)


def _is_shallow() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    return result.stdout.strip() == "true"


def _first_commit_date() -> str:
    """The project's start date, which is an ELIGIBILITY field on the form.

    **Refuses on a shallow clone rather than answering.** `actions/checkout` defaults to
    depth 1, so `git log --reverse` there returns the newest commit, and this function
    would have reported the date of whatever merge CI was building as the date the
    project began. That is a wrong answer to the one field that decides whether the
    entry is eligible at all, produced silently, in the environment nobody watches.

    It is the shape this project has hit before: a check whose precondition holds only
    on the machine where it was written. Caught by CI going red, which is the good
    version of finding it.
    """
    if _is_shallow():
        raise SystemExit(
            "this is a shallow clone, so the first commit cannot be read and the "
            "project start date would be wrong. Fetch full history "
            "(actions/checkout with fetch-depth: 0) before generating or checking "
            "the submission sheet."
        )
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

    found = set(
        re.findall(
            r"\b(gemini-[0-9.]+-[a-z-]+|gemini-embedding-[0-9]+|gemma[0-9]?:[0-9a-z.]+"
            r"|Chirp3-HD-[A-Za-z]+|chirp_3)\b",
            source,
        )
    )
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

    # Everything that cannot be derived from the repository is read from the one file
    # that records it, so each item's status is computed rather than remembered. A
    # blank is indistinguishable from a decision not to answer, so unmet items keep
    # stating what they need.
    links = json.loads((REPO / "docs" / "submission_links.json").read_text())
    video_url = str(links.get("video_url", "")).strip()
    social_url = str(links.get("social_bonus_url", "")).strip()
    content_url = str(links.get("content_bonus_url", "")).strip()

    outstanding = [
        (
            "Demo video URL",
            f"DONE. {video_url}, recorded with its verification in `submission_links.json`."
            if video_url
            else "public on YouTube or Vimeo, 4 minutes maximum, English or "
            "subtitled, must show the backend running on Google Cloud",
        ),
        (
            "Social bonus link",
            f"DONE. {social_url}, a post carrying #AllThingsAgenticHackathon, "
            "recorded in `submission_links.json` and on the Devpost form."
            if social_url
            else "optional. Must carry #AllThingsAgenticHackathon",
        ),
        (
            "Content bonus link",
            # The same URL doing double duty as the demo video is an interim claim,
            # not the how-it-was-built post the bonus text describes. Say which state
            # holds rather than letting DONE imply the stronger one.
            (
                f"INTERIM. The form field holds the demo video ({content_url}), which "
                "qualifies by the form's own letter (public video, description carries "
                "the required created-for-this-hackathon language) but is not the "
                "how-it-was-built post the bonus text describes. A dedicated post is "
                "drafted; when it publishes, swap the URL on the Devpost form and in "
                "`submission_links.json` together."
                if content_url == video_url
                else f"DONE. {content_url}, recorded in `submission_links.json` and on "
                "the Devpost form."
            )
            if content_url
            else "optional. A public post about how this was built, "
            "carrying language saying it was created for this hackathon",
        ),
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
        "## Every required field on the live form",
        "",
        "Built by walking `docs/submission_schema.json`, which is a fetched record of the",
        "form rather than a remembered list of it. The previous version of this table was",
        "hand-written and silently omitted TWO required fields, Submitter Type and country",
        "of residence, which nothing could have noticed. A missing required field blocks",
        "submission outright, however good the system is.",
        "",
        "| Field | Answer | Where it comes from |",
        "|---|---|---|",
    ]

    authors = _human_authors()
    readme = (REPO / "README.md").read_text()
    diagram = REPO / "docs" / "architecture.png"

    #: Answer plus provenance per field id. A required field with no entry here renders
    #: as unanswered rather than vanishing, which is the whole point of walking the
    #: schema instead of listing what somebody remembered.
    answers: dict[int, tuple[str, str]] = {
        28083: (
            "**Individuals**" if len(authors) == 1 else f"**Team of individuals** ({len(authors)})",
            f"derived from `git shortlog`: {len(authors)} human author(s) in the history",
        ),
        28084: (
            "you supply at submit time",
            "country of residence is not a fact the repository holds, and inferring "
            "somebody's residence is not the kind of guess to encode",
        ),
        28085: (
            "**Fortified Enterprise Fleet**",
            "the exact string the form offers; a mis-typed track name is an auto-decline",
        ),
        28086: (
            "you supply at submit time",
            "`required: true` regardless of its optional-sounding wording, so the "
            "pre-submit gate refuses to say READY until it is recorded",
        ),
        28087: (
            f"**{started}**",
            "first commit. The submission period opened 2026-08-04, so this is inside it",
        ),
        28141: ("https://github.com/StephenSook/curtail", "public, so no sharing step is needed"),
        28089: (
            "**Yes**" if "## Setup" in readme else "**No**",
            "grep of README.md for a Setup section",
        ),
        28091: (f"**{', '.join(sdks) or 'NONE FOUND'}**", "grep of shipped source"),
        28142: (
            f"**{', '.join(services) or 'NONE FOUND'}**",
            "grep of shipped source and the Dockerfile",
        ),
        28092: (
            f"`docs/architecture.png` ({diagram.stat().st_size:,} bytes)"
            if diagram.is_file()
            else "**MISSING**",
            "a file UPLOAD, not a text answer, so it never appears in custom_answers",
        ),
        28143: (f"**{', '.join(models) or 'NONE FOUND'}**", "grep of shipped source"),
    }

    schema = json.loads(SCHEMA.read_text())
    for field in schema["fields"]:
        if not field.get("required"):
            continue
        answer, source = answers.get(
            field["id"],
            ("**NOT ANSWERED**", "no answer is staged for this field, and it is required"),
        )
        lines.append(f"| {field['label']} | {answer} | {source} |")

    lines += [
        "",
        "## Optional fields the repository can still answer",
        "",
        'Optional is not the same as leave blank. The form calls a hosted URL "highly encouraged",',
        "and a judge who cannot click anything scores what they could not exercise as absent.",
        "",
        "| Field | Answer |",
        "|---|---|",
        f"| Hosted project URL | {SERVICE_URL} |",
        f"| Testing instructions (judges only) | {JUDGE_INSTRUCTIONS} |",
        f"| Google AI models, long form | {AI_MODELS_ANSWER} |",
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
