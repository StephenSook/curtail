"""Draft a real order with a real model, and print what the guards decided.

The committed test suite drives the Scribe through an injected generator, because what
needs testing is the behaviour when a model returns something WRONG, and a correct answer
from a real model proves nothing about that. This script is the other half: proof that
the wiring is real, runnable on camera for the demo.

Run: GOOGLE_CLOUD_PROJECT=curtail-505118 uv run python scripts/scribe_demo.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path[:0] = [
    str(Path(__file__).resolve().parents[1] / "core" / "src"),
    str(Path(__file__).resolve().parents[1] / "agents" / "src"),
]

from curtail_agents.scribe import draft_order  # noqa: E402
from curtail_core.allocation import recommend  # noqa: E402
from curtail_core.basins import Basin  # noqa: E402
from curtail_core.rights_record import load_rights  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cfs", type=float, default=46.5, help="observed discharge")
    parser.add_argument("--on", default="2026-06-15", help="date being evaluated")
    parser.add_argument("--rights", type=int, default=6, help="how many rights to include")
    args = parser.parse_args()

    loaded = load_rights()
    rights = [r for r in loaded.converted.rights if r.basin is Basin.SHASTA][: args.rights]
    if not rights:
        print("::error::no rights loaded")
        return 1

    result = recommend(
        basin=Basin.SHASTA,
        when=date.fromisoformat(args.on),
        observed_cfs=args.cfs,
        rights=rights,
    )
    print(f"Core      : {result.action.value}")
    print(f"            {args.cfs} cfs against {result.operative_minimum_cfs} cfs minimum")
    print(
        f"            {len(result.rights_reached)} rights reached, extent "
        f"{result.recommended_extent_rank}"
    )

    outcome = draft_order(result)
    print(f"\nScribe    : {outcome.model}")
    print(f"Verdict   : {outcome.verdict.value} after {outcome.attempts} attempt(s)")
    print(f"May sign  : {outcome.may_reach_pdf}")
    print(f"Guard     : {outcome.guard.reason}")
    for violation in outcome.violations:
        print(f"  rejected: {violation}")

    print("\n" + "=" * 72)
    print(outcome.text)
    print("=" * 72)

    # A draft that failed its checks is not a failure of this script: it is the guard
    # doing its job, and the exit code says which happened.
    return 0 if outcome.may_reach_pdf else 2


if __name__ == "__main__":
    sys.exit(main())
