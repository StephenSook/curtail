"""The submission-day gate: everything checkable, checked, in one command.

**Why a gate and not a checklist.** A checklist is prose, and this project has spent a
lot of effort learning that prose about a requirement is not the requirement. Every item
below either runs a real check or reports honestly that it CANNOT, and the exit code is
the verdict.

**It refuses to say READY while anything a human must supply is missing**, because the
failure mode near a deadline is not forgetting to run the tests: it is a green suite
being read as "the submission is done" when the video does not exist.

Ordered so the cheap offline checks fail first and the ones needing credentials or a
network come last, so a broken repository is not diagnosed by a Cloud Run probe.
"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    command: list[str] | None
    #: True when this needs credentials or the network, so it can be skipped
    #: DELIBERATELY and reported as unrun rather than silently passing.
    needs_network: bool = False
    #: Set when a human must supply something no command can check.
    human: str | None = None


STEPS: tuple[Step, ...] = (
    Step("lint, format", ["make", "lint"]),
    Step("types", ["make", "types"]),
    Step("tests and coverage", ["make", "test"]),
    Step("AI tone and em-dash", ["make", "tone"]),
    Step("browser suite", ["make", "test-browser"]),
    Step("fact sheet is current", ["uv", "run", "python", "scripts/generate_facts.py", "--check"]),
    Step(
        "submission sheet is current",
        ["uv", "run", "python", "scripts/generate_submission.py", "--check"],
    ),
    Step("rights record matches its source", ["make", "rights-check"]),
    Step("Scott record matches its source", ["make", "scott-rights-check"]),
    Step(
        "the deployed service still serves what we claim",
        ["make", "deployed-check"],
        needs_network=True,
    ),
    Step("the chaos drill, every layer", ["make", "chaos-recording"], needs_network=True),
    Step("demo video published", None, human="a public YouTube or Vimeo URL, 4 minutes maximum"),
    Step(
        "architecture diagram uploaded", None, human="docs/architecture.png, to Devpost field 28092"
    ),
    Step("organization name answered", None, human="required even though the form reads optional"),
)


def _run(step: Step) -> tuple[bool, str]:
    assert step.command is not None
    result = subprocess.run(step.command, cwd=REPO, capture_output=True, text=True)
    if result.returncode == 0:
        return True, "ok"
    tail = (result.stdout + result.stderr).strip().splitlines()
    return False, (tail[-1][:120] if tail else f"exit {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The submission-day gate.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the steps needing credentials or a network, and SAY they were skipped",
    )
    args = parser.parse_args(argv)

    print("CURTAIL pre-submission gate\n")
    failed: list[str] = []
    skipped: list[str] = []
    outstanding: list[str] = []

    for step in STEPS:
        if step.human is not None:
            outstanding.append(f"{step.name}: {step.human}")
            print(f"[HUMAN]  {step.name}")
            continue
        if step.needs_network and args.offline:
            skipped.append(step.name)
            print(f"[SKIP]   {step.name}  (--offline)")
            continue
        ok, detail = _run(step)
        print(f"[{'PASS' if ok else 'FAIL'}]   {step.name}" + ("" if ok else f"  {detail}"))
        if not ok:
            failed.append(step.name)

    print()
    if failed:
        print(f"NOT READY. {len(failed)} check(s) failed: {', '.join(failed)}")
        return 1
    if skipped:
        print(
            f"{len(skipped)} check(s) were SKIPPED and prove nothing: "
            f"{', '.join(skipped)}. Re-run without --offline before submitting."
        )
    print("Everything checkable passes. Still needed from a human:")
    for item in outstanding:
        print(f"  - {item}")
    print(
        "\nThis is not READY. It is 'nothing automatable is broken'. The items above are "
        "the submission."
    )
    # Non-zero on purpose while a human item is outstanding: a green exit here would be
    # read as "submitted", which is the one misreading that costs the entry.
    return 0 if not outstanding else 2


if __name__ == "__main__":
    raise SystemExit(main())
