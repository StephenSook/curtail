"""Place the corpus on a timeline, and be exact about what kind of date each one is.

The manifest records what each of the 101 documents DOES, impose or suspend, and never
records when. A season timeline needs when, and these documents do not print a date field:
they are memoranda with a Subject line and a body.

**No adoption date is synthesised, because two heuristics were tried and both were wrong.**

*Most frequent date in the text* works on Scott Addendum 12, which names "June 9, 2026"
seventeen times, and fails on Shasta Addendum 6, which lists a run of gage readings by date
so the mode is noise: it returns June 11 where the true date is June 16.

*Latest date in the text* fit BOTH anchors, and looked principled, since a document cannot
reference a date after it was written. Measuring it against the whole corpus killed it:
**73 of 101 documents name a date after their file was created**, mostly by exactly seven
days, because an addendum states when it expires, and one names 2031-01-01, the
regulation's sunset under AB 263. Two anchors is not a sample, and a heuristic that fits
every anchor you have can still be wrong about the population.

So this records facts and makes no inference:

- `file_date`, when the PDF was created. A fact about the FILE, never presented as the
  date an order was adopted.
- `modal_text_date` and `latest_text_date`, carried with their evidence so a reader can
  judge, and plotted by nothing.
- `adoption_date`, only for the documents whose date is established independently by the
  rights loader. Two of 101.

The timeline orders by file date and says so on screen. Measured against the two known
adoption dates, the file lands 0 and 2 days after adoption, and that offset is REPORTED
rather than assumed to hold for the other 99.

**That the Board publishes a hundred curtailment documents carrying no machine-readable
adoption date is not a limitation of this extractor. It is the coordination gap this
project exists to describe.**

Split like the diversion builder and the deployment probe: the corpus is 101 PDFs that are
not in git, so this half needs them and the committed artifact is what CI reads.

    uv run python scripts/build_timeline.py          # extract and write
    uv run python scripts/build_timeline.py --check  # verify, offline, no PDFs needed
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "data" / "corpus"
MANIFEST = REPO / "data" / "corpus_manifest.json"
OUT = REPO / "core" / "src" / "curtail_core" / "data" / "timeline.json"

#: Adoption dates established independently by the rights loader, from these same
#: documents. The only two adoption dates in the artifact, and the only two it may claim.
ANCHORS: dict[str, str] = {
    "scott_2024__addenda__12.pdf": "2026-06-09",
    "shasta_2024__addenda__6.pdf": "2026-06-16",
}

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_IN_TEXT = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})\b")

#: `scott_2024__addenda__12.pdf` -> series, kind, number.
FILENAME = re.compile(r"^(?P<series>[a-z]+_\d{4})__(?P<kind>[a-z]+)__(?P<n>[^.]+)\.pdf$")


class BuildError(RuntimeError):
    """The artifact could not be built or does not hold together."""


def _text(path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise BuildError(f"pdftotext failed on {path.name}: {result.stderr.strip()[:160]}")
    return result.stdout


def _dates_in(text: str) -> list[str]:
    """Every full date the text states, from 2000 onward, in document order.

    Earlier years are excluded because a priority date of 1912 appears constantly in
    these documents and says nothing about when the document issued.
    """
    out: list[str] = []
    for month, day, year in DATE_IN_TEXT.findall(text):
        try:
            # A calendar date with no time and no zone, which is what a document
            # states. Attaching a timezone would invent precision the text does not
            # carry, so the naive parse is deliberate and immediately narrowed to a
            # `date`. Comparing these to anything time-aware is a separate mistake and
            # nothing here does it.
            parsed = datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date()  # noqa: DTZ007
        except ValueError:
            continue
        if parsed.year >= 2000:
            out.append(parsed.isoformat())
    return out


def _metadata_date(path: Path) -> str | None:
    result = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("CreationDate:"):
            raw = line.split(":", 1)[1].strip()
            for fmt in ("%a %b %d %H:%M:%S %Y %Z", "%a %b %d %H:%M:%S %Y"):
                try:
                    # `pdfinfo` prints a local wall-clock stamp whose zone is the
                    # machine that made the file. Narrowed to a `date` immediately for
                    # the same reason: the day is the fact, the hour is not.
                    return datetime.strptime(raw, fmt).date().isoformat()  # noqa: DTZ007
                except ValueError:
                    continue
    return None


def _actions() -> dict[tuple[str, str, str], str]:
    """`(series, kind, n)` to the action the manifest already recorded."""
    manifest = json.loads(MANIFEST.read_text())
    out: dict[tuple[str, str, str], str] = {}
    for series in manifest["series"]:
        for addendum in series.get("addenda", []):
            extracted = addendum.get("extracted") or {}
            out[(series["id"], "addenda", str(addendum["n"]))] = extracted.get("action", "")
        for order in series.get("base_orders", []):
            extracted = order.get("extracted") or {}
            key = (series["id"], "order", str(order.get("order_number", "")))
            out[key] = extracted.get("action", "")
    return out


def build() -> dict[str, Any]:
    for tool in ("pdftotext", "pdfinfo"):
        if shutil.which(tool) is None:
            raise BuildError(f"{tool} is not installed. brew install poppler.")
    if not CORPUS.is_dir():
        raise BuildError(f"{CORPUS} is absent. The corpus is not tracked in git.")

    actions = _actions()
    entries: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []

    for path in sorted(CORPUS.glob("*.pdf")):
        match = FILENAME.match(path.name)
        series = match.group("series") if match else None
        kind = match.group("kind") if match else None
        number = match.group("n") if match else None

        dates = _dates_in(_text(path))
        counted = collections.Counter(dates)
        modal, mentions = counted.most_common(1)[0] if counted else (None, 0)

        entry: dict[str, Any] = {
            "file": path.name,
            "series": series,
            "kind": kind,
            "number": number,
            "action": actions.get((series or "", kind or "", number or ""), ""),
            # A fact about the FILE. Never the adoption date, never labelled as one.
            "file_date": _metadata_date(path),
            # Evidence, plotted by nothing. Both were tried as the adoption date and
            # both were wrong; see the module docstring.
            "modal_text_date": modal,
            "modal_text_mentions": mentions,
            "latest_text_date": max(dates) if dates else None,
            # Only where the rights loader establishes it independently.
            "adoption_date": ANCHORS.get(path.name),
        }
        entries.append(entry)

        if entry["file_date"] is None:
            gaps.append(
                {
                    "file": path.name,
                    "why": "the file carries no creation timestamp, so it cannot be "
                    "placed on the timeline at all",
                }
            )

    entries.sort(key=lambda e: (e["file_date"] or "9999-99-99", e["file"]))

    # Measured on the only two documents where both a file date and a known adoption
    # date exist. Reported so a reader can see how close the plotted date is to the
    # legal one, rather than being told to assume they match.
    offsets = [
        {
            "file": e["file"],
            "adoption_date": e["adoption_date"],
            "file_date": e["file_date"],
            "file_days_after_adoption": (
                date.fromisoformat(e["file_date"]) - date.fromisoformat(e["adoption_date"])
            ).days,
        }
        for e in entries
        if e["adoption_date"] and e["file_date"]
    ]

    return {
        "source": {
            "corpus": "data/corpus, 101 documents, not tracked in git",
            "method": (
                "These documents do not print an adoption date. The timeline is ordered "
                "by the PDF file creation timestamp, which is a fact about the file and "
                "not the date an order was adopted. Two documents carry an adoption date "
                "established independently by the rights loader. Two heuristics for "
                "inferring the rest were tried and both were wrong."
            ),
            "anchors": ANCHORS,
            "measured_offsets": offsets,
        },
        "counts": {
            "documents": len(entries),
            "with_file_date": sum(1 for e in entries if e["file_date"]),
            "with_verified_adoption_date": sum(1 for e in entries if e["adoption_date"]),
            "gaps": len(gaps),
            "by_action": dict(collections.Counter(e["action"] or "unrecorded" for e in entries)),
        },
        "entries": entries,
        "gaps": gaps,
    }


def check() -> list[str]:
    problems: list[str] = []
    if not OUT.exists():
        return [f"{OUT.name} is missing. Run scripts/build_timeline.py."]

    payload = json.loads(OUT.read_text())
    entries = payload["entries"]
    by_file = {e["file"]: e for e in entries}

    for file, expected in ANCHORS.items():
        entry = by_file.get(file)
        if entry is None:
            problems.append(f"the anchor {file} is missing from the artifact")
        elif entry["adoption_date"] != expected:
            problems.append(
                f"{file} records adoption {entry['adoption_date']} and the rights loader "
                f"says {expected}"
            )

    verified = sum(1 for e in entries if e["adoption_date"])
    if verified != len(ANCHORS):
        problems.append(
            f"{verified} entries claim an adoption date and only {len(ANCHORS)} are "
            "independently established. Nothing may claim one without a source."
        )

    counts = payload["counts"]
    if counts["documents"] != len(entries):
        problems.append("the document count does not match the entries")
    with_file_date = sum(1 for e in entries if e["file_date"])
    if counts["with_file_date"] != with_file_date:
        problems.append("the file-date count does not reconcile")
    if len(payload["gaps"]) != len(entries) - with_file_date:
        problems.append("the gaps list does not account for every unplottable document")

    # Every date in this artifact must say what KIND of date it is. A bare `date` field
    # is how an inference gets promoted into a fact by a later edit.
    for entry in entries:
        if "date" in entry:
            problems.append(f"{entry['file']} carries a bare `date` field")
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
            f"{counts['with_file_date']} of {counts['documents']} placeable by file date, "
            f"{counts['with_verified_adoption_date']} with a verified adoption date, "
            f"{counts['gaps']} not placeable"
        )
        return 0

    try:
        payload = build()
    except (BuildError, OSError, ValueError) as exc:
        print(f"could not build: {exc}", file=sys.stderr)
        return 1

    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    counts = payload["counts"]
    print(
        f"{counts['with_file_date']} of {counts['documents']} placed by file date, "
        f"{counts['with_verified_adoption_date']} with a verified adoption date"
    )
    for offset in payload["source"]["measured_offsets"]:
        print(
            f"  {offset['file']}: file created "
            f"{offset['file_days_after_adoption']} day(s) after adoption"
        )
    print(f"  actions: {counts['by_action']}")
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
