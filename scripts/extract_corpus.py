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

**Three outcomes, never conflated.** An adversarial review found that an earlier
version collapsed them: `pdftotext` missing, `pdftotext` timing out, and a
genuine scan all produced an empty string, and the parser then wrote "this is a
scanned document" for all three. With the binary absent, 90 documents that
demonstrably have text layers were reported as scans, and the script still
exited 0. A diagnosis is not an observation, so the three are now distinct and a
tooling failure is fatal.

Run:  uv run python scripts/extract_corpus.py [--write]

Without `--write` it reports and changes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, fields
from enum import StrEnum
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


class ReadOutcome(StrEnum):
    """What happened to one document. Every record lands in exactly one.

    Totals are computed by counting these, not by adjusting running counters
    afterwards. The previous approach incremented `ocr` inside the loop for
    documents present on disk, then subtracted a vision count derived from the
    manifest alone, and the two sets were not the same set. On a fresh clone,
    where the corpus is gitignored and absent, it printed a NEGATIVE count of
    refused documents.
    """

    NOT_FETCHED = "not_fetched"
    TOOL_FAILED = "tool_failed"
    NO_TEXT_LAYER = "no_text_layer"
    READ_NOT_SCORABLE = "read_not_scorable"
    SCORABLE = "scorable"
    VISION = "vision"


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


class PdfToolError(RuntimeError):
    """`pdftotext` itself failed. NOT the same as a document having no text."""


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


def _require_pdftotext() -> None:
    """Fail before doing anything, rather than mislabelling the whole corpus.

    A missing binary is a precondition failure of this script. Discovering it
    one document at a time, and recording each as a scan, turns an environment
    problem into 90 false claims about government documents.
    """
    if shutil.which("pdftotext") is None:
        raise PdfToolError(
            "pdftotext is not installed or not on PATH. Refusing to run: without "
            "it every document would be indistinguishable from a scan, and this "
            "script would record 'this is a scanned document' for documents that "
            "have perfectly good text layers. Install poppler-utils."
        )


def _read_pdf(path: Path) -> str:
    """Text layer of a PDF.

    Raises rather than returning empty on a tool failure, because empty is a
    meaningful observation about the DOCUMENT and must not be produced by a
    problem with the TOOL. A non-zero exit is also a failure: a partial decode
    that emits page one and then dies is the dangerous case, since the title
    parses while the body does not, so the title/body conflict check cannot fire.
    """
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfToolError(f"pdftotext timed out after 120s on {path.name}") from exc
    except OSError as exc:
        raise PdfToolError(f"pdftotext could not be run on {path.name}: {exc}") from exc

    if proc.returncode != 0:
        raise PdfToolError(
            f"pdftotext exited {proc.returncode} on {path.name}: "
            f"{proc.stderr.strip()[:160]}. A partial decode is not a read."
        )
    return proc.stdout


#: Fields that are not serialised, and why.
#:
#: `action_evidence` is a raw header slice kept for interactive debugging; it is
#: long, noisy, and duplicates information already in the manifest.
_UNSERIALISED = {"action_evidence"}


def _as_json(e: Extraction) -> dict[str, Any]:
    """Serialise an extraction.

    A review found three computed fields silently dropped here, including
    `expires_on`, which the parser's own comment calls legally load-bearing: a
    lapsed suspension reverts rights to curtailment with no further order. Worse,
    the dropped expiry still appeared in the record under `priority_dates`, so an
    expiry was filed as a water-right priority date, which is a different legal
    object the Allocation Core ranks rights by.

    `test_every_extraction_field_is_serialised` now fails if a field is added to
    the dataclass and not handled here.
    """
    return {
        "method": e.method.value,
        "action": e.action.value,
        "qualifier": e.qualifier.value,
        "priority_groups": list(e.priority_groups),
        "affects_all": e.affects_all,
        "priority_dates": [d.isoformat() for d in e.priority_dates],
        "cfs_values": list(e.cfs_values),
        "body_action": e.body_action.value,
        "title_body_conflict": e.title_body_conflict,
        "expires_on": e.expires_on.isoformat() if e.expires_on else None,
        "text_chars": e.text_chars,
        "notes": list(e.notes),
    }


def serialisable_field_names() -> set[str]:
    """Every dataclass field this module is expected to emit."""
    return {f.name for f in fields(Extraction)} - _UNSERIALISED


def _blocked_reason(result: Extraction) -> str:
    """Say why a document was refused, checking the real condition.

    A conflicted document was previously recorded as one whose "action could not
    be classified", which is the opposite of what happened: the action was
    classified twice, incompatibly. That is the most important state this parser
    detects, and describing it as a parsing shortfall would bury it.
    """
    if result.title_body_conflict:
        return (
            "title and body describe different acts; refused rather than "
            "resolved, because choosing between them silently would put a "
            "guessed legal characterisation into the record"
        )
    if result.method is ExtractionMethod.REQUIRES_OCR:
        return "no text layer, requires OCR or transcription"
    return "text layer present but the action could not be classified"


def _write_atomic(path: Path, content: str) -> None:
    """Write via a sibling temp file and rename.

    `write_text` truncates first, so an interrupt or a full disk leaves a
    truncated `corpus_manifest.json`, which is the git-tracked source of truth
    for the headline denominator. `os.replace` is atomic on the same filesystem.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="persist the flip into the manifest")
    args = parser.parse_args()

    try:
        _require_pdftotext()
    except PdfToolError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    manifest: dict[str, Any] = json.loads(MANIFEST.read_text())
    records = _records(manifest)

    report: dict[str, Any] = {}
    outcomes: dict[str, ReadOutcome] = {}
    tool_failures: list[str] = []

    for rec in records:
        # A vision read is authoritative and this script must not touch it.
        # An earlier version unconditionally set document_read to False for
        # anything the parser could not read, silently reverting all four scans
        # a person had already read. The manifest's own denominator guard caught
        # it. A stated contract the code does not enforce is worth nothing.
        if rec.node.get("read_method") == "vision":
            outcomes[rec.key] = ReadOutcome.VISION
            report[rec.key] = {"status": "vision_read", "document_read": True}
            continue

        if not rec.pdf.exists():
            outcomes[rec.key] = ReadOutcome.NOT_FETCHED
            report[rec.key] = {"status": "not_fetched"}
            continue

        try:
            text = _read_pdf(rec.pdf)
        except PdfToolError as exc:
            outcomes[rec.key] = ReadOutcome.TOOL_FAILED
            report[rec.key] = {"status": "tool_failed", "error": str(exc)}
            tool_failures.append(f"{rec.key}: {exc}")
            continue

        result = extract(text)
        report[rec.key] = _as_json(result)

        if result.scorable:
            outcomes[rec.key] = ReadOutcome.SCORABLE
            if args.write:
                rec.node["document_read"] = True
                rec.node["extracted"] = _as_json(result)
                # Clear the stale refusal. A record that has become readable
                # must not keep the reason it was once refused: a node carrying
                # both document_read: true and extraction_blocked contradicts
                # itself, and the manifest is the source of truth for the
                # headline denominator. Caught by the manifest's own guard when
                # a parser improvement reclassified six documents.
                rec.node.pop("extraction_blocked", None)
        else:
            outcomes[rec.key] = (
                ReadOutcome.NO_TEXT_LAYER
                if result.method is ExtractionMethod.REQUIRES_OCR
                else ReadOutcome.READ_NOT_SCORABLE
            )
            if args.write:
                rec.node["document_read"] = False
                rec.node["extraction_blocked"] = _blocked_reason(result)

    tally = {o: sum(1 for v in outcomes.values() if v is o) for o in ReadOutcome}
    total = len(records)
    scorable = tally[ReadOutcome.SCORABLE] + tally[ReadOutcome.VISION]

    # Invariants. A count that can go negative should raise, not print.
    assert sum(tally.values()) == total, "outcomes do not account for every record"
    assert all(v >= 0 for v in tally.values()), "negative count"
    assert scorable <= total

    print(f"manifest records           {total}")
    print(f"  not fetched              {tally[ReadOutcome.NOT_FETCHED]}")
    print(f"  pdftotext FAILED         {tally[ReadOutcome.TOOL_FAILED]}")
    print(f"  no text layer            {tally[ReadOutcome.NO_TEXT_LAYER]}")
    print(f"  read but not classified  {tally[ReadOutcome.READ_NOT_SCORABLE]}")
    print(f"  read from rendered pages {tally[ReadOutcome.VISION]}")
    print(f"  scorable                 {scorable}")

    if tool_failures:
        print(f"\n{len(tool_failures)} document(s) failed for a TOOLING reason, not a")
        print("document reason. These are NOT scans and must not be recorded as such:")
        for line in tool_failures[:10]:
            print(f"  {line}")

    if args.write:
        _write_atomic(REPORT, json.dumps(report, indent=2, sort_keys=True) + "\n")
        _write_atomic(MANIFEST, json.dumps(manifest, indent=2) + "\n")
        print(f"\nwrote {REPORT.relative_to(REPO)} and updated the manifest")
    else:
        print("\nreport only. Pass --write to persist.")

    unscorable = total - scorable
    if unscorable:
        print(
            f"\n{unscorable} of {total} documents remain unscorable. They stay "
            "document_read: false. A denominator that counted them would claim "
            "the engine was tested against documents nothing has read."
        )

    # A tooling failure is not a document outcome, so it fails the run.
    return 1 if tool_failures else 0


if __name__ == "__main__":
    sys.exit(main())
