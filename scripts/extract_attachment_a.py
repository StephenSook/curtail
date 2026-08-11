"""Extract Attachment A into a committed rights record, without owner names.

The corpus PDFs are gitignored: they are fetched, not vendored. So the parse runs
LOCALLY, against the real document, and writes a record that CI and the deployed
service can both read. Same split this repository already uses for quote provenance: a
local script produces the record with a content hash of its source, and CI asserts the
record is complete, internally consistent, and names everything it could not read.

**No owner names are written, and the parser never reads them.** The table's first
column is real private parties, several named in live reconsideration proceedings, and
the repository is public. The record carries application number, source, priority date,
band and page: everything the priority ladder consumes and nothing it does not.

Run: python scripts/extract_attachment_a.py [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "core" / "src")]

from curtail_core.adjudications import WaterRight  # noqa: E402
from curtail_core.attachment_a import parse_attachment, to_water_rights  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "data" / "corpus" / "addendum6-shasta-attach-a.pdf"
RECORD = REPO / "data" / "rights_shasta_addendum6.json"

#: The table runs from page 6 to the end of the document. The body occupies 1 to 5.
FIRST_TABLE_PAGE = 6
LAST_TABLE_PAGE = 15


def page_text(pdf: Path, page: int) -> str:
    """One page of layout text.

    `pdftotext` is required rather than optional. A missing extractor that silently
    produced empty pages would report a table of zero rights as a successful parse,
    which is the failure shape this project keeps meeting.
    """
    if shutil.which("pdftotext") is None:
        raise SystemExit(
            "pdftotext is not installed. Install poppler (brew install poppler). "
            "Refusing to continue: an absent extractor would report an empty table "
            "as a clean read."
        )
    result = subprocess.run(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise SystemExit(f"pdftotext failed on page {page}: {result.stderr[:300]}")
    return result.stdout


def build() -> dict[str, Any]:
    if not SOURCE.exists():
        raise SystemExit(
            f"{SOURCE} is not present. The corpus is fetched, not vendored: run "
            "scripts/fetch_corpus.py first."
        )

    pages = {p: page_text(SOURCE, p) for p in range(FIRST_TABLE_PAGE, LAST_TABLE_PAGE + 1)}
    report = parse_attachment(pages)
    converted = to_water_rights(report)

    return {
        "source": {
            "document": "Order WR 2024-0006-DWR, Addendum 6, Updated Attachment",
            "issued": "2026-06-16",
            "file": SOURCE.name,
            "sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "pages": [FIRST_TABLE_PAGE, LAST_TABLE_PAGE],
        },
        "accounting": {
            "application_numbers_seen": report.application_numbers_seen,
            "parsed": len(report.rights),
            "imprecise": len(report.imprecise),
            "ambiguous": len(report.ambiguous),
            "unparsed": len(report.unparsed),
        },
        "not_read": {
            "imprecise": list(report.imprecise),
            "ambiguous": list(report.ambiguous),
            "unparsed": list(report.unparsed),
            "blank_source": list(report.blank_source),
            "recovered_from_neighbour": list(report.recovered_from_neighbour),
            "unrecoverable": list(report.unrecoverable),
        },
        "open_questions": list(converted.open_questions),
        "unplaceable": list(converted.unplaceable),
        "rights": [
            {
                **{k: v for k, v in asdict(r).items() if k != "band"},
                "band": r.band.value,
                "priority_date": r.priority_date.isoformat() if r.priority_date else None,
            }
            for r in report.rights
        ],
        "ladder_placement_counts": _placement_counts(converted.rights),
    }


def _placement_counts(rights: tuple[WaterRight, ...]) -> dict[str, int]:
    from curtail_core.priority import place

    counts: dict[str, int] = {}
    for right in rights:
        label = place(right).grouping_label
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed record differs from a fresh parse",
    )
    args = parser.parse_args()

    fresh = json.dumps(build(), indent=2, sort_keys=True) + "\n"

    if args.check:
        if not RECORD.exists():
            print(f"::error::{RECORD} is missing")
            return 1
        if RECORD.read_text() != fresh:
            print(f"::error::{RECORD} is stale. Re-run scripts/extract_attachment_a.py")
            return 1
        print(f"{RECORD.name} matches a fresh parse of {SOURCE.name}")
        return 0

    RECORD.write_text(fresh)
    record = json.loads(fresh)
    print(f"wrote {RECORD.relative_to(REPO)}")
    print(
        f"  {record['accounting']['parsed']} rights parsed of "
        f"{record['accounting']['application_numbers_seen']} application numbers seen"
    )
    print(f"  placement: {record['ladder_placement_counts']}")
    for note in record["open_questions"]:
        print(f"  open: {note[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
