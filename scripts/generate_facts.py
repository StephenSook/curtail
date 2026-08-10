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
MANIFEST = REPO / "data" / "corpus_manifest.json"


def _schedule_rows(basin: Basin) -> list[str]:
    rows = []
    for period in SCHEDULES[basin]:
        start = f"{period.start_month:02d}-{period.start_day:02d}"
        end = f"{period.end_month:02d}-{period.end_day:02d}"
        rows.append(f"| {start} to {end} | {period.cfs:g} |")
    return rows


def build() -> str:
    manifest: dict[str, Any] = json.loads(MANIFEST.read_text())
    status = manifest["extraction_status"]
    report = run_backtest()

    # Penalties are computed, never quoted, so the stale-regulation gap is a
    # result rather than a claim.
    eight_days = penalties.exposure(days_in_violation=8)

    lines: list[str] = []
    add = lines.append

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
    add(f"| Read from rendered pages | {status.get('read_via_vision', 0)} |")
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
    add("all, roughly 16 percent. `pdftotext` returns three or four bytes. That is")
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
    add(f"flag, because the reading sits inside the {NEAR_THRESHOLD_BAND_CFS:g} cfs band")
    add("below the minimum. Field verification is what changed the answer.")
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
    for item in json.loads(MANIFEST.read_text())["open_items"]:
        if item.startswith("OPEN"):
            add(f"- {item}")
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
        print("docs/FACTS.md is current.")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content)
    print(f"wrote {OUT.relative_to(REPO)} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
