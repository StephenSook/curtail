"""Assert the deployed service is not merely up, but still ANSWERING CORRECTLY.

A liveness ping proves a process is listening. It says nothing about whether the
flow schedule still resolves, whether the classifier still agrees with the Board's
record, or whether the service has quietly started answering questions it is
supposed to refuse. A 200 from a process that has lost its logic is worse than a
500, because it looks like health.

Written as a script rather than inline in the workflow, and that is not a style
choice. A heredoc indented inside a YAML block scalar never terminates: the shell
compares the closing token including its leading spaces, `<<-` strips tabs and not
spaces, and the step dies on a syntax error that reads nothing like its cause. A
script is also runnable locally, which is the only way to know the check works
before trusting it to run unattended for a month.

Standard library only, deliberately. This runs on a bare runner during a freeze, and
a dependency install is another thing that can break while nobody is watching.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

TIMEOUT_SECONDS = 30

#: The reading behind Scott Addendum 7: 48.7 cfs against the 50 cfs July minimum at
#: Fort Jones. If the schedule, the Sentinel or the direction helper drift, this
#: answer changes and the check fails, which is the point of pinning a real one.
KNOWN_READING = "/api/classify/scott?cfs=48.7&at=2025-07-20T21:30:00%2B00:00"
EXPECTED = {
    "classification": "reading_near_threshold",
    "direction": "restrict",
    "minimum_cfs": 50.0,
    "provenance": "unsourced",
    "recommendation_only": True,
}

#: Things the service must keep REFUSING. A service that stops refusing is answering
#: from the wrong era's table, or classifying a reading that does not exist, and both
#: look like working software from the outside.
MUST_REFUSE = [
    ("/api/classify/shasta?cfs=41&at=2021-08-15T12:00:00%2B00:00", 422, "unencoded 2021 era"),
    ("/api/classify/scott?cfs=NaN&at=2025-07-20T21:30:00%2B00:00", 422, "NaN reading"),
    ("/api/classify/nowhere?cfs=1", 404, "unknown basin"),
]


def fetch(url: str) -> tuple[int, str]:
    """Return (status, body). An HTTP error is a RESULT here, not an exception.

    A check that raises on 422 could not assert that 422 is what it wanted, which
    is half of what this script exists to verify.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "curtail-uptime"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def check(base: str) -> list[str]:
    """Every failure, not just the first.

    Stopping at the first would hide whether the service is wholly down or has one
    broken endpoint, and those want different responses from a human.
    """
    failures: list[str] = []

    status, body = fetch(f"{base}/api/healthz")
    if status != 200:
        failures.append(f"liveness returned {status}: {body[:120]}")

    status, body = fetch(f"{base}{KNOWN_READING}")
    if status != 200:
        failures.append(f"the known reading returned {status}: {body[:120]}")
    else:
        answer: dict[str, Any] = json.loads(body)
        for field, want in EXPECTED.items():
            if answer.get(field) != want:
                failures.append(
                    f"the engine's answer changed: {field} is {answer.get(field)!r}, "
                    f"expected {want!r}"
                )

    for path, want_status, why in MUST_REFUSE:
        status, _ = fetch(f"{base}{path}")
        if status != want_status:
            failures.append(f"stopped refusing {why}: got {status}, expected {want_status}")

    status, body = fetch(f"{base}/api/facts")
    if status != 200:
        failures.append(f"the fact sheet returned {status}")
    elif "reproduces the direction" not in json.loads(body).get("markdown", ""):
        failures.append("the served fact sheet no longer carries the headline metric")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="the deployed service root")
    args = parser.parse_args()

    failures = check(args.base.rstrip("/"))
    for failure in failures:
        print(f"::error::{failure}")
    if failures:
        print(f"{len(failures)} check(s) failed against {args.base}")
        return 1
    print(f"all checks passed against {args.base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
