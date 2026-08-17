"""How many days the river was already below the line when the Board's order was dated.

**This exists because the project's headline was in the wrong currency.** The backtest
reports how often the engine agrees with the Board, which measures whether the system is
RIGHT. A watermaster does not buy correctness. They buy time. The number that means
something to them is how long the river sat below its minimum before anything was issued,
and that is what this computes, in days, from two public records that nobody had put beside
each other: the USGS daily discharge record and the dates of the Board's own documents.

**What it does NOT claim, and this is the important half.**

23 CCR 875(b) directs the Deputy Director to act with "consideration of hydrologic,
weather, and other conditions." A gap between the crossing and the order is therefore NOT
proof of administrative delay: some of it is deliberate judgment about whether a dip will
persist, and that judgment is exactly what this project says belongs to a human. So the
claim is deliberately narrow and factual:

    the river crossed the line N days before the Board's document is dated

Curtail does not remove the official's judgment. It removes the wait for somebody to
notice. Any wording stronger than that is not supported by this computation and must not
appear in the README, the video or the submission.

**Three honest weaknesses, stated here so they travel with the number.**

1. `file_date` is the PDF's creation timestamp, a fact about the file. Measured against the
   only two independently established adoption dates it lands 0 and 2 days after adoption,
   so it is a close proxy and it is not the adoption date. Reported as what it is.
2. Daily MEAN discharge, not the instantaneous reading the Board watches. A river that dips
   below at 3pm and recovers by midnight does not appear here at all, which makes this
   estimate CONSERVATIVE: it can only understate how long the river was below.
3. Documents with no gage coverage, or whose river was not below its minimum in the days
   before issuance, are excluded and counted rather than dropped.

    uv run python scripts/build_response_lag.py            # fetch and write
    uv run python scripts/build_response_lag.py --check     # verify, offline
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from curtail_core.basins import Basin
from curtail_core.flow_minimums import ScheduleGapError, minimum_flow

REPO = Path(__file__).resolve().parents[1]
TIMELINE = REPO / "core" / "src" / "curtail_core" / "data" / "timeline.json"
OUT = REPO / "core" / "src" / "curtail_core" / "data" / "response_lag.json"

DAILY_VALUES = "https://waterservices.usgs.gov/nwis/dv/"
GAGE = {Basin.SCOTT: "11519500", Basin.SHASTA: "11517500"}

#: The corpus begins in 2021 and the daily record is cheap, so take all of it.
START = "2021-01-01"

#: An order is dated a few days after the conditions it responds to. Searching back this
#: far for the most recent below-minimum day, then walking the unbroken run back from
#: there, is what "the river has been below since" means.
RECENT_WINDOW_DAYS = 14

#: Actions that START or RESUME curtailment. A suspension is the opposite event and its
#: lag would mean something entirely different, so the two are never mixed.
IMPOSING = {"impose", "reinstate"}


class BuildError(RuntimeError):
    """The record could not be built or does not hold together."""


def daily_series(site: str, *, client: httpx.Client) -> dict[str, float]:
    """Daily mean discharge by ISO date, from the USGS daily values service."""
    response = client.get(
        DAILY_VALUES,
        params={
            "format": "json",
            "sites": site,
            "startDT": START,
            # Zone-explicit. The gage record is Pacific and the builder may run anywhere, so
            # an implicit local "today" would silently change what gets fetched.
            "endDT": datetime.now(UTC).date().isoformat(),
            "parameterCd": "00060",
            "siteStatus": "all",
        },
    )
    if response.status_code != 200:
        raise BuildError(f"USGS returned HTTP {response.status_code} for {site}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise BuildError(f"USGS returned a non-JSON body for {site}") from exc

    series = payload.get("value", {}).get("timeSeries", [])
    if not series:
        raise BuildError(f"USGS returned no series for {site}")

    out: dict[str, float] = {}
    for entry in series[0].get("values", [{}])[0].get("value", []):
        raw = entry.get("value")
        # -999999 is the service's no-data sentinel. Reading it as a discharge would put
        # an impossibly low value into the record and manufacture a crossing.
        if raw in (None, "", "-999999"):
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if value < 0:
            continue
        out[str(entry["dateTime"])[:10]] = value
    if not out:
        raise BuildError(f"no usable daily values came back for {site}")
    return out


def crossing_before(
    when: date, series: dict[str, float], basin: Basin
) -> tuple[date, int] | tuple[None, str]:
    """The first day of the unbroken run below the minimum that was current at `when`.

    Walks BACK from the order date to the most recent below-minimum day, then keeps
    walking while days stay below. That is the same shape as the live watch's consecutive
    run, deliberately: a reader who understands one understands the other.

    **Three outcomes, not two, and conflating the last two is a defect this had.** A date
    whose flow schedule is not verified for its era CANNOT BE EVALUATED, and the first
    version caught that refusal and carried on, so every 2021 document came back as "the
    river was not below its minimum" when the truth is that nothing here can say either
    way. An errored query reading as a confident negative is a failure this repository has
    recorded before, and it was pointing at the era boundary the flow-minimums module
    already models correctly.
    """
    anchor: date | None = None
    evaluated = 0
    refused = 0
    for back in range(RECENT_WINDOW_DAYS + 1):
        day = when - timedelta(days=back)
        value = series.get(day.isoformat())
        if value is None:
            continue
        try:
            minimum = float(minimum_flow(basin, day))
        except ScheduleGapError:
            refused += 1
            continue
        evaluated += 1
        if value < minimum:
            anchor = day
            break
    if anchor is None and evaluated == 0:
        return None, (
            f"cannot be scored: the operative flow schedule for this era is not verified "
            f"in this repository, so all {refused} day(s) of gage record before this "
            "document refuse rather than compare. This is a gap in what is known, not a "
            "finding about the river."
        )
    if anchor is None:
        return None, (
            f"the river was not below its minimum on any of the {evaluated} evaluable "
            f"day(s) in the {RECENT_WINDOW_DAYS} before this document, so it is not "
            "responding to a current crossing"
        )

    first = anchor
    while True:
        previous = first - timedelta(days=1)
        value = series.get(previous.isoformat())
        if value is None:
            break
        try:
            minimum = float(minimum_flow(basin, previous))
        except ScheduleGapError:
            break
        if value >= minimum:
            break
        first = previous
    return first, (when - first).days


def build() -> dict[str, Any]:
    timeline = json.loads(TIMELINE.read_text())
    with httpx.Client(timeout=90.0, headers={"User-Agent": "curtail-research"}) as client:
        series: dict[Basin, dict[str, float]] = {}
        for basin, site in GAGE.items():
            series[basin] = daily_series(site, client=client)
            print(f"  {basin.value}: {len(series[basin]):,} daily values")
            time.sleep(2)  # a public agency's service, used gently

    scored: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []

    for entry in timeline["entries"]:
        name = entry["file"]
        if entry.get("action") not in IMPOSING:
            continue
        if not entry.get("file_date"):
            excluded.append({"file": name, "why": "the file carries no creation timestamp"})
            continue
        basin = Basin.SCOTT if str(entry.get("series", "")).startswith("scott") else Basin.SHASTA
        when = date.fromisoformat(entry["file_date"])

        first, result = crossing_before(when, series[basin], basin)
        if first is None:
            excluded.append({"file": name, "why": str(result)})
            continue
        scored.append(
            {
                "file": name,
                "basin": basin.value,
                "action": entry["action"],
                "document_dated": when.isoformat(),
                "river_below_since": first.isoformat(),
                "lag_days": int(result),
            }
        )

    if not scored:
        raise BuildError("nothing could be scored, which is a bug rather than a finding")

    lags = sorted(item["lag_days"] for item in scored)
    return {
        "source": {
            "gage_record": "USGS daily mean discharge, parameter 00060, from " + START,
            "documents": "data/corpus timeline, imposing and reinstating actions only",
            "claim": (
                "the river crossed its operative minimum this many days before the Board's "
                "document is dated"
            ),
            "not_a_claim": (
                "This is NOT a measure of administrative delay. 23 CCR 875(b) directs the "
                "Deputy Director to act with consideration of hydrologic, weather and other "
                "conditions, so part of any gap is deliberate judgment about whether a dip "
                "will persist, and that judgment is what this system says belongs to a "
                "human. Curtail does not remove the official's judgment. It removes the "
                "wait for somebody to notice."
            ),
            "conservative_because": (
                "Daily MEAN discharge, not the instantaneous reading. A river that dips "
                "below in the afternoon and recovers overnight does not appear here, so "
                "this can only understate how long the river was below its minimum."
            ),
            "date_caveat": (
                "The document date is the PDF creation timestamp, a fact about the file. "
                "Against the only two independently established adoption dates it lands 0 "
                "and 2 days after adoption, so it is a close proxy and not the adoption "
                "date itself."
            ),
        },
        "counts": {
            "scored": len(scored),
            "excluded": len(excluded),
            # Split out, because a document nobody can evaluate and a document evaluated
            # and found not to apply are completely different statements about the world.
            "excluded_unevaluable_era": sum(
                1 for item in excluded if "not verified" in item["why"]
            ),
            "median_lag_days": int(statistics.median(lags)),
            "max_lag_days": max(lags),
            "min_lag_days": min(lags),
        },
        "scored": sorted(scored, key=lambda item: item["lag_days"], reverse=True),
        "excluded": excluded,
    }


def check() -> list[str]:
    problems: list[str] = []
    if not OUT.exists():
        return [f"{OUT.name} is missing. Run scripts/build_response_lag.py."]
    payload = json.loads(OUT.read_text())
    scored = payload["scored"]
    counts = payload["counts"]

    if counts["scored"] != len(scored):
        problems.append("the scored count does not match the entries")
    if counts["excluded"] != len(payload["excluded"]):
        problems.append("the excluded count does not match the list")
    for item in scored:
        dated = date.fromisoformat(item["document_dated"])
        since = date.fromisoformat(item["river_below_since"])
        if (dated - since).days != item["lag_days"]:
            problems.append(f"{item['file']} reports a lag its own dates do not support")
            break
        if item["lag_days"] < 0:
            problems.append(f"{item['file']} reports a negative lag")
            break
    lags = [item["lag_days"] for item in scored]
    if lags and counts["median_lag_days"] != int(statistics.median(sorted(lags))):
        problems.append("the median does not match the scored entries")
    if lags and counts["max_lag_days"] != max(lags):
        problems.append("the maximum does not match the scored entries")

    # The caveats are the honest half and they must travel with the number.
    for required in ("not_a_claim", "conservative_because", "date_caveat"):
        if not payload["source"].get(required, "").strip():
            problems.append(f"the record does not carry its {required}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the artifact, offline")
    args = parser.parse_args(argv)

    if args.check:
        problems = check()
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        if problems:
            return 1
        counts = json.loads(OUT.read_text())["counts"]
        print(
            f"{counts['scored']} imposing actions scored, median lag "
            f"{counts['median_lag_days']} days, longest {counts['max_lag_days']}, "
            f"{counts['excluded']} excluded"
        )
        return 0

    try:
        payload = build()
    except (BuildError, OSError, ValueError, httpx.HTTPError) as exc:
        print(f"could not build: {exc}", file=sys.stderr)
        return 1

    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    counts = payload["counts"]
    print(
        f"\n{counts['scored']} imposing actions scored, {counts['excluded']} excluded\n"
        f"  median lag {counts['median_lag_days']} days\n"
        f"  longest    {counts['max_lag_days']} days\n"
        f"  shortest   {counts['min_lag_days']} days"
    )
    for item in payload["scored"][:5]:
        print(
            f"    {item['lag_days']:4d} days  {item['basin']:7} {item['action']:9} "
            f"below since {item['river_below_since']}, dated {item['document_dated']}"
        )
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
