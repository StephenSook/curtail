"""Run the order parser across the fetched corpus and record what it read.

This is the script that moves a manifest record from `index_verified` to
`document_read`. That flip is the honesty boundary of the headline metric: the
first tier means the Board's index page listed the document, the second means
its own PDF was opened and supplied the values. Only the second permits scoring.

The flip is performed here, by a program, rather than by hand. A hand-edited
manifest is how the retired 39.3 cfs figure entered four artifacts, a public
README: a number that appears nowhere in Shasta Addendum 6 was written into the
record as though the document had supplied it, and it survived a verification
pass and a passing test suite. A record touched only by this script cannot claim
a value the extractor did not return.

Existing hand-verified fields are merged, never clobbered, so a value confirmed
by a human reading the PDF is not silently replaced by a weaker automatic read.

Run:  uv run python scripts/extract_corpus.py [--write]

Without `--write` it reports and changes nothing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "src"))

from curtail_core.order_parser import (
    Extraction,
    ExtractionMethod,
    extract,
)

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data" / "corpus_manifest.json"
CORPUS_DIR = REPO / "data" / "corpus"
REPORT = REPO / "data" / "extraction_report.json"


@dataclass(frozen=True, slots=True)
class Record:
    """One manifest entry and the file it should correspond to."""

    series_id: str
    group: str
    label: str
    node: dict[str, Any]

    @property
    def key(self) -> str:
        return f"{self.series_id}/{self.group}/{self.label}"

    @property
    def pdf(self) -> Path:
        return CORPUS_DIR / f"{self.key.replace('/', '__')}.pdf"


def _records(manifest: dict[str, Any]) -> list[Record]:
    out: list[Record] = []
    for series in manifest["series"]:
        sid = series["id"]
        for order in series["base_orders"]:
            out.append(Record(sid, "order", order["order_number"], order))
        for group in ("addenda", "addenda_to_2026_0008"):
            for add in series.get(group, []):
                out.append(Record(sid, group, str(add["n"]), add))
    return out


def _read_pdf(path: Path) -> str:
    """Text layer of a PDF, or empty when there is none.

    A failure of `pdftotext` returns empty rather than raising, because the
    parser's own refusal path is the correct handler: an empty string is exactly
    what a scanned document produces, and it must reach `extract` so the result
    is recorded as REQUIRES_OCR rather than as a crash.
    """
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout


def _as_json(e: Extraction) -> dict[str, Any]:
    return {
        "method": e.method.value,
        "action": e.action.value,
        "qualifier": e.qualifier.value,
        "priority_groups": list(e.priority_groups),
        "affects_all": e.affects_all,
        "priority_dates": [d.isoformat() for d in e.priority_dates],
        "cfs_values": list(e.cfs_values),
        "text_chars": e.text_chars,
        "notes": list(e.notes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="persist the flip into the manifest")
    args = parser.parse_args()

    manifest: dict[str, Any] = json.loads(MANIFEST.read_text())
    records = _records(manifest)

    report: dict[str, Any] = {}
    present = read = scored = ocr = 0

    for rec in records:
        if not rec.pdf.exists():
            report[rec.key] = {"status": "not_fetched"}
            continue
        present += 1

        result = extract(_read_pdf(rec.pdf))
        report[rec.key] = _as_json(result)

        if result.method is ExtractionMethod.REQUIRES_OCR:
            ocr += 1
        if result.is_readable:
            read += 1

        # A vision read is authoritative and this script must not touch it.
        #
        # The docstring above promises that hand-verified values are merged and
        # never clobbered, and an earlier version broke that promise: the branch
        # below unconditionally set document_read to False for anything the
        # parser could not read, which silently reverted all four scans that a
        # person had already read from the rendered pages. The manifest's own
        # denominator guard caught it. A stated contract that the code does not
        # enforce is worth nothing, so the check is now in the code.
        if rec.node.get("read_method") == "vision":
            if result.scorable:
                scored += 1
            continue

        if result.scorable:
            scored += 1
            if args.write:
                rec.node["document_read"] = True
                rec.node["extracted"] = _as_json(result)
        elif args.write:
            rec.node["document_read"] = False
            rec.node["extraction_blocked"] = (
                "no text layer, requires OCR"
                if result.method is ExtractionMethod.REQUIRES_OCR
                else "text layer present but action could not be classified"
            )

    # Vision reads are counted here rather than in the loop, since the parser
    # cannot see them at all and they must still appear in the totals.
    vision = sum(
        1 for r in records if r.node.get("read_method") == "vision" and r.node.get("document_read")
    )
    scored += vision
    ocr -= vision

    total = len(records)
    print(f"manifest records           {total}")
    print(f"  PDF present on disk      {present}")
    print(f"  read via text layer      {read}")
    print(f"  scorable (read + action) {scored}")
    print(f"  refused, need OCR        {ocr}")
    print(f"  not fetched              {total - present}")

    if args.write:
        REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"\nwrote {REPORT.relative_to(REPO)} and updated the manifest")
    else:
        print("\nreport only. Pass --write to persist.")

    unread = total - scored
    if unread:
        print(
            f"\n{unread} of {total} documents remain unscorable. They stay "
            "document_read: false. A denominator that counted them would claim "
            "the engine was tested against documents nothing has read."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
