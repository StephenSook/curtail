"""Parse the Scott River Attachment A into a committed rights record.

**The Board states the group. We do not infer it.** Shasta's attachment gives priority
dates and the ladder has to place each right; Scott's gives a `Curtailment Group` column
holding 1 through 9 directly, which is 23 CCR 875.5(a)(1)(A) materialised by the agency
that administers it. Reading the stated group removes an entire class of inference error,
and it also creates a check nothing else in this project has: our own placement logic can
be scored against the Board's own assignment.

**Owner names are never read.** The table's third column is `Primary Owner`, full names of
private ranchers and districts. The parser matches around it and stores nothing from it,
for the same reason the Shasta record never read one: third-party personal data does not
belong in a public repository, and a public order being the source does not change that.

**Reconciliation is the guard.** Every application number ANYWHERE in the document must
appear in a parsed row. A parser that reads 300 of 384 rows and reports 300 is a
denominator it chose for itself, which is the defect this project audits for. Counts are
compared against the document, not against expectation, and a mismatch refuses.

Run:    uv run python scripts/extract_scott_attachment_a.py
Check:  uv run python scripts/extract_scott_attachment_a.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

#: Addendum 12, 9 June 2026: the most recent Scott addendum in the fetched corpus and
#: the operative statement of which rights sit in which group.
SOURCE = REPO / "data" / "corpus" / "scott_2024__addenda__12.pdf"
RECORD = REPO / "data" / "rights_scott_addendum12.json"

#: The packaged copy, which is the one a DEPLOYED service reads. Written by the same run,
#: because a container has no repository: the Shasta record, the fact sheet and the
#: console page all ship inside the package for that reason, each after a review found
#: the repository-relative path resolving to nothing in a wheel.
PACKAGED = REPO / "core" / "src" / "curtail_core" / "data" / "rights_scott_addendum12.json"

#: The four-column row. The owner column is matched and DISCARDED: `.+?` consumes it so
#: the status can be anchored, and nothing from it reaches the record.
ROW = re.compile(
    r"^\s*(?P<group>\d{1,2})\s+"
    r"(?P<id>[A-Z]{1,2}\d{5,6})\s+"
    r".+?\s{2,}"
    r"(?P<status>Curtailed|Not Curtailed|Suspended|Conditionally Suspended)\s*$"
)

#: Any application number, used to reconcile the parse against the document.
ANY_ID = re.compile(r"\b[A-Z]{1,2}\d{5,6}\b")

#: 23 CCR 875.5(a)(1)(A) defines nine groupings. A row outside that range means the
#: column was misread, and guessing which group was meant would put a right in the wrong
#: place on a ladder where one grouping is the difference between irrigating and shutting
#: off.
VALID_GROUPS = range(1, 10)


def _text() -> str:
    if not SOURCE.exists():
        raise SystemExit(
            f"{SOURCE} is not present. The corpus is fetched rather than vendored: "
            "fetch it before extracting."
        )
    result = subprocess.run(
        ["pdftotext", "-layout", str(SOURCE), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def parse(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = ROW.match(line)
        if match is None:
            continue
        group = int(match.group("group"))
        if group not in VALID_GROUPS:
            raise SystemExit(
                f"row for {match.group('id')} states group {group}, outside the nine "
                "groupings 875.5(a)(1)(A) defines. The column was misread, and placing "
                "it anyway would put a right on the wrong rung."
            )
        rows.append(
            {
                "application_number": match.group("id"),
                "curtailment_group": group,
                "status_as_printed": match.group("status"),
            }
        )
    return rows


def _digest(rows: list[dict[str, Any]]) -> str:
    """A content hash over the parsed rows, in canonical form.

    Sorted keys and a fixed separator so the digest depends on the DATA rather than on
    how json chose to format it that day.
    """
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def build() -> dict[str, Any]:
    text = _text()
    rows = parse(text)

    seen = {r["application_number"] for r in rows}
    in_document = set(ANY_ID.findall(text))
    missed = sorted(in_document - seen)
    if missed:
        raise SystemExit(
            f"{len(missed)} application numbers appear in the document and in no parsed "
            f"row: {missed[:10]}. A parse that reports only what it managed to read is a "
            "denominator it chose. Fix the row pattern rather than the expectation."
        )

    duplicates = [
        n for n, count in Counter(r["application_number"] for r in rows).items() if count > 1
    ]
    if duplicates:
        raise SystemExit(
            f"these application numbers appear on more than one row: {duplicates[:10]}. "
            "One right in two groups is a contradiction the ladder cannot resolve."
        )

    groups = Counter(r["curtailment_group"] for r in rows)
    ordered = sorted(rows, key=lambda r: (r["curtailment_group"], r["application_number"]))
    return {
        "source": {
            "file": SOURCE.name,
            "sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "document": "Order WR 2024-0024-DWR, Addendum 12, Attachment A",
            "issued": "2026-06-09",
            "basin": "scott",
        },
        "provenance": (
            "Curtailment groups are the BOARD'S OWN column, not this project's "
            "inference. 23 CCR 875.5(a)(1)(A) defines nine groupings and the "
            "attachment states which one each right sits in."
        ),
        "privacy": (
            "The attachment's Primary Owner column is deliberately not read. No "
            "third-party personal data enters this repository, and a public source "
            "does not change that."
        ),
        "counts": {
            "rows_parsed": len(rows),
            "application_numbers_in_document": len(in_document),
            "by_group": {str(k): groups[k] for k in sorted(groups)},
            "by_status": dict(Counter(r["status_as_printed"] for r in rows)),
            # **A digest CI can check without the PDF, and the reason it exists.**
            #
            # The corpus is gitignored, so `--check` only runs where somebody has the
            # source, which is a human's machine and never a CI runner. Every other
            # assertion about this record reads numbers the record itself reports, so
            # they are a total derived from its own parts and cannot fail: a hand edit
            # moving one right from group 8 to group 1, the difference between curtailed
            # first and curtailed last, passed the entire suite. Demonstrated before this
            # was added, not imagined.
            #
            # This binds the counts to the rows. Editing a right without re-running the
            # extractor now fails in CI, on a runner that has never seen the PDF.
            "rights_sha256": _digest(ordered),
        },
        "rights": ordered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the record has drifted")
    args = parser.parse_args()

    fresh = build()
    if args.check:
        for target in (RECORD, PACKAGED):
            if not target.exists():
                print(f"MISSING: {target.relative_to(REPO)}", file=sys.stderr)
                return 1
            if json.loads(target.read_text()) != fresh:
                print(
                    f"STALE: {target.relative_to(REPO)} disagrees with a fresh parse of "
                    f"{SOURCE.name}.",
                    file=sys.stderr,
                )
                return 1
        print(f"both copies of {RECORD.name} match a fresh parse of {SOURCE.name}")
        return 0

    payload = json.dumps(fresh, indent=2) + "\n"
    PACKAGED.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(payload)
    PACKAGED.write_text(payload)
    counts = fresh["counts"]
    print(f"wrote {RECORD.relative_to(REPO)}")
    print(
        f"  {counts['rows_parsed']} rights, reconciled against "
        f"{counts['application_numbers_in_document']} ids in the document"
    )
    print(f"  by group: {counts['by_group']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
