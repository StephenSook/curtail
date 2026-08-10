"""Assert that the test suite actually ran and that nothing was skipped.

A green CI check proves a suite RAN. It does not prove any assertion executed.
A conditionally-skipped guard is a false green: the run is honest and the
assertion simply never fired. A prior project shipped a secret-scanning guard
that skipped on every CI run for the life of the repo, under a green check,
with a docstring explaining why the skip was acceptable.

So this reads machine-readable counts from pytest's JUnit XML and fails on:
  - zero tests collected (a green check over an empty suite)
  - any skipped test
  - any failure or error
  - a suite that has silently shrunk below a known floor

Run locally with:  uv run python scripts/check_suite_integrity.py <junit.xml>
"""

from __future__ import annotations

import sys
from pathlib import Path

from defusedxml import ElementTree as ET  # noqa: N817

#: The suite must not silently shrink. Raise this as the suite grows.
#: Never lower it to make a red build pass.
MINIMUM_EXPECTED_TESTS = 196


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("/tmp/junit.xml")

    if not path.exists():
        print(f"::error::{path} does not exist. pytest did not produce a report.")
        return 1

    root = ET.parse(str(path)).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        print("::error::No testsuite element in the report. The suite did not run.")
        return 1

    total = int(suite.get("tests", 0))
    skipped = int(suite.get("skipped", 0))
    failures = int(suite.get("failures", 0))
    errors = int(suite.get("errors", 0))

    print(f"tests={total} skipped={skipped} failures={failures} errors={errors}")

    if total == 0:
        print("::error::Zero tests collected. A green check over an empty suite is a false green.")
        return 1
    if skipped:
        print(f"::error::{skipped} test(s) SKIPPED. A skipped guard is a false green.")
        return 1
    if failures or errors:
        print(f"::error::{failures} failure(s) and {errors} error(s).")
        return 1
    if total < MINIMUM_EXPECTED_TESTS:
        print(
            f"::error::Only {total} tests ran, expected at least "
            f"{MINIMUM_EXPECTED_TESTS}. Did collection break?"
        )
        return 1

    print(f"confirmed: {total} tests ran, zero skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
