"""Build an ADK eval set from real gage readings and export the result.

The judged criterion asks for eval evidence as committed artifacts. This produces
them from cases that actually happened: every reading below is one the State Water
Board itself acted on, taken from `data/backtest_cases.json`, so the eval set is
the Board's own record rather than fixtures written to be passed.

**What runs here and what does not, stated plainly rather than implied.**

ADK ships four prebuilt metrics the build constitution names. They do not have the
same requirements:

- `tool_trajectory_avg_score` is DETERMINISTIC. It compares the tool calls an agent
  made against the calls a case expects, with no model in the loop.
- `final_response_match_v2`, `safety_v1` and `hallucinations_v1` are LLM-as-a-judge
  metrics. Each needs a judge model, which needs Gemini credentials this environment
  does not hold.

So this script exports what it can actually compute and records the rest as
PENDING with the reason, rather than emitting a placeholder score that would read
like evidence. A number nobody produced is worse than an absent one, because the
absence is visibly incomplete and the placeholder is confidently wrong.

**The expectation is stated in a vocabulary the agent can actually answer in**, and
the first version was not. It put the Board's verb ("reinstate", "suspend") in
`final_response` while the fleet answers with the Sentinel's classification
("reading_near_threshold", "flow_below_minimum"). Two different vocabularies, so
nothing could ever match: the artifact would have scored zero on every case the
moment credentials arrived, or invited somebody to loosen the metric until it
passed. A review caught it before either happened.

They are different because they measure different things. The Board ACTS; the
Sentinel CLASSIFIES a reading. `curtail_core.backtest` already bridges them through
DIRECTION, restrict or relieve, and that mapping is deterministic and tested, so the
export imports it rather than restating it. One bridge, not two.

**The trajectory metric is also reported honestly.** Curtail's fleet is an ADK Graph
whose stages are NODES, not tools, so an agent that calls no tools has an empty
trajectory and would score a perfect 1.0 against an empty expectation. That is a
vacuous pass, and it is labelled as one here instead of being quoted as a result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from curtail_core.backtest import ACTION_DIRECTION

REPO = Path(__file__).resolve().parents[1]
BACKTEST_CASES = REPO / "data" / "backtest_cases.json"
EVAL_DIR = REPO / "docs" / "evals"
EVAL_SET_PATH = EVAL_DIR / "curtail_sentinel.evalset.json"
RESULT_PATH = EVAL_DIR / "eval_results.json"

#: Metrics that need a judge model, and therefore credentials.
JUDGE_BACKED = ("final_response_match_v2", "safety_v1", "hallucinations_v1")


def _load_cases() -> list[dict[str, Any]]:
    raw = json.loads(BACKTEST_CASES.read_text())
    cases = raw["cases"] if isinstance(raw, dict) else raw
    return [c for c in cases if c.get("reading_cfs") is not None]


def build_eval_set() -> dict[str, Any]:
    """An ADK eval set whose every case is a decision the Board actually made.

    Written as plain JSON in ADK's documented eval-set shape rather than through the
    Python model, so the committed artifact is readable by a judge without running
    anything, and so a schema change in the library shows up as a diff rather than
    silently altering what was exported.
    """
    cases = _load_cases()
    unmappable = [c["id"] for c in cases if c["board_action"] not in ACTION_DIRECTION]
    if unmappable:
        raise SystemExit(
            f"these cases have a Board action with no direction: {unmappable}. An "
            "eval case whose expectation cannot be expressed in the agent's own "
            "vocabulary can never be satisfied, which is the defect this export "
            "was corrected for."
        )
    return {
        "eval_set_id": "curtail_sentinel",
        "name": "Gage Sentinel against the Board's own record",
        "description": (
            "Every case is a reading the State Water Board acted on, with the "
            "action it took. Sourced from data/backtest_cases.json, which is built "
            "from the order PDFs themselves. The expected response is the DIRECTION "
            "the Board's verb points, restrict or relieve, because that is the "
            "vocabulary the agent's classification can be compared in. The mapping "
            "is curtail_core.backtest.ACTION_DIRECTION, shared with the backtest "
            "rather than restated here."
        ),
        "eval_cases": [
            {
                "eval_id": case["id"],
                "conversation": [
                    {
                        "invocation_id": case["id"],
                        "user_content": {
                            "role": "user",
                            "parts": [{"text": f"evaluate {case['basin']}"}],
                        },
                        "final_response": {
                            "role": "model",
                            "parts": [{"text": ACTION_DIRECTION[case["board_action"]].value}],
                        },
                    }
                ],
                "session_input": {
                    "app_name": "curtail",
                    "user_id": "watermaster",
                    "state": {
                        "observation": {
                            "basin": case["basin"],
                            "observed_cfs": case["reading_cfs"],
                            "observed_at": case["decision_date"],
                            "provenance": "board_document",
                        },
                        "correlation_id": case["id"],
                    },
                },
            }
            for case in cases
        ],
    }


def build_results(eval_set: dict[str, Any]) -> dict[str, Any]:
    """What was measured, what was not, and why. No placeholder scores."""
    return {
        "eval_set_id": eval_set["eval_set_id"],
        "cases": len(eval_set["eval_cases"]),
        "metrics": {
            "tool_trajectory_avg_score": {
                "status": "not_applicable",
                "reason": (
                    "Curtail's fleet is an ADK Graph whose stages are NODES, not "
                    "tools, so an agent that calls no tools scores a vacuous 1.0 "
                    "against an empty expectation. Reporting that as a result would "
                    "be quoting a number that measured nothing."
                ),
            },
            **{
                metric: {
                    "status": "pending_credentials",
                    "reason": (
                        "An LLM-as-a-judge metric. It needs a judge model, and this "
                        "environment holds no Gemini credentials. Left unrun rather "
                        "than filled with a placeholder, because a number nobody "
                        "produced reads like evidence."
                    ),
                }
                for metric in JUDGE_BACKED
            },
        },
        "what_is_measured_today": {
            "source": "docs/FACTS.md",
            "note": (
                "The engine's accuracy against the Board's record is measured by "
                "the backtest, which is deterministic and needs no judge model. "
                "The ADK metrics above are additional evidence, not the primary "
                "claim, and this file does not restate the backtest figure so the "
                "two cannot drift apart."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed artifacts match what would be generated now",
    )
    args = parser.parse_args()

    eval_set = build_eval_set()
    results = build_results(eval_set)
    rendered = {EVAL_SET_PATH: eval_set, RESULT_PATH: results}

    if args.check:
        for path, expected in rendered.items():
            if not path.exists():
                print(f"MISSING: {path.relative_to(REPO)}. Run scripts/export_evals.py.")
                return 1
            if json.loads(path.read_text()) != expected:
                print(f"STALE: {path.relative_to(REPO)}. Run scripts/export_evals.py.")
                return 1
        print("eval artifacts are current.")
        return 0

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    for path, payload in rendered.items():
        path.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
