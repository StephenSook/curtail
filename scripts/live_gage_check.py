"""Live smoke check: read both compliance gages and evaluate them against the regulation.

Deliberately NOT part of the pytest suite. A test suite that reaches the public
internet is a suite that goes red when a third party has a bad afternoon, and a
flaky gate teaches people to ignore gates.

Run it by hand, or from a scheduled job that is allowed to be noisy:

    uv run python scripts/live_gage_check.py

Exits non-zero if either gage cannot be read, so it is usable as a monitor.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime

from curtail_agents.gage_client import GageClient, GageError
from curtail_core.flow_minimums import (
    COMPLIANCE_GAGE,
    Basin,
    is_below_minimum,
    is_near_threshold,
    minimum_flow,
)


async def main() -> int:
    today = datetime.now(UTC).date()
    api_key = os.environ.get("USGS_API_KEY")
    failures = 0

    print(f"Compliance gage check for {today.isoformat()}")
    print(f"{'basin':8} {'gage':16} {'observed':>10} {'minimum':>8}  status")
    print("-" * 68)

    async with GageClient(api_key=api_key) as client:
        for basin in Basin:
            gage = COMPLIANCE_GAGE[basin]
            try:
                reading = await client.latest_discharge(gage)
            except GageError as exc:
                print(f"{basin.value:8} {gage:16} {'ERROR':>10} {'':>8}  {exc}")
                failures += 1
                continue

            minimum = minimum_flow(basin, today)
            below = is_below_minimum(basin, today, reading.cfs)
            near = is_near_threshold(basin, today, reading.cfs)

            if below and near:
                status = "BELOW MINIMUM, within 10 cfs: field verification recommended"
            elif below:
                status = "BELOW MINIMUM"
            elif near:
                status = "at or above minimum, but inside the near-threshold band"
            else:
                status = "above minimum"

            if reading.is_provisional:
                status += " [provisional, subject to revision]"

            print(f"{basin.value:8} {gage:16} {reading.cfs:>10.2f} {minimum:>8.0f}  {status}")

    print()
    print("This is a recommendation input, not a determination. Section 875(b) vests")
    print("the determination in the Deputy Director, and 875(b)(3) preserves the")
    print("discretion to decline, narrow, or suspend.")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
