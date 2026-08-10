"""CI gate: no fabricated authority reaches a shipped artifact.

Hard rule 13 says no legal citation enters any artifact without a verified
primary source. A rule enforced by vigilance does not survive the night before
a deadline, so this makes it mechanical (HARD RULE 74).

The self-reference problem is handled explicitly. `docs/citations.json` and this
test both necessarily contain the banned strings, so both are excluded from the
sweep. An exclusion is exactly how a guard silently becomes inert, so
`TestTheGuardIsNotVacuous` plants a banned string in a real tracked file and
asserts the scanner catches it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CITATIONS = REPO_ROOT / "docs" / "citations.json"

#: Files that must contain the banned strings in order to do their job.
SELF_REFERENTIAL = {
    "docs/citations.json",
    "core/tests/test_citation_guard.py",
}

#: Extensions worth scanning. Everything a judge, an official, or a reviewer reads.
SCANNED_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".txt", ".html", ".ts", ".tsx"}


def _tracked_files() -> list[Path]:
    """Every git-tracked file, which is exactly the set that ships."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line]


def _scannable() -> list[Path]:
    return [
        p
        for p in _tracked_files()
        if p.suffix in SCANNED_SUFFIXES
        and str(p.relative_to(REPO_ROOT)) not in SELF_REFERENTIAL
        and p.is_file()
    ]


def _blacklist() -> list[dict[str, str]]:
    data = json.loads(CITATIONS.read_text())
    entries: list[dict[str, str]] = data["blacklist"]
    return entries


def _display(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise.

    The non-vacuity tests below scan probe files in a temp directory, which is
    outside the repo, so an unguarded `relative_to` raises and the guard errors
    instead of reporting. A scanner that crashes on its own mutation test is
    indistinguishable from one that found nothing.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def scan(paths: list[Path]) -> list[tuple[str, str, str]]:
    """Return (file, pattern, finding) for every blacklisted string found."""
    hits: list[tuple[str, str, str]] = []
    for entry in _blacklist():
        pattern = entry["pattern"]
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern in text:
                hits.append((_display(path), pattern, entry["finding"]))
    return hits


class TestFixtureIntegrity:
    """The fixture itself must be real, or the guard protects nothing."""

    def test_the_blacklist_file_exists(self) -> None:
        assert CITATIONS.exists(), f"{CITATIONS} is missing; the guard has no fixture"

    def test_it_holds_entries(self) -> None:
        assert len(_blacklist()) >= 8

    def test_every_entry_carries_a_pattern_kind_and_finding(self) -> None:
        """An entry without a reason is a rule nobody can review."""
        for entry in _blacklist():
            assert entry.get("pattern"), entry
            assert entry.get("kind"), entry
            assert entry.get("finding"), entry

    def test_the_two_wholly_fabricated_cases_are_listed(self) -> None:
        patterns = {e["pattern"] for e in _blacklist()}
        assert "Schattinger v. Schattinger" in patterns
        assert "Fall River Irrigation Co. v. Swendsen" in patterns

    def test_rich_is_listed_by_reporter_citation(self) -> None:
        """Listed as '625 P.2d 977' because the case name alone is too common."""
        assert any(e["pattern"] == "625 P.2d 977" for e in _blacklist())


class TestNoFabricatedAuthorityShips:
    def test_the_scan_actually_examined_files(self) -> None:
        """Non-vacuity: a scan of nothing must never read as a clean scan."""
        scannable = _scannable()
        assert len(scannable) >= 10, (
            f"only {len(scannable)} scannable files found. A near-empty sweep "
            "would pass while protecting nothing."
        )

    def test_no_shipped_artifact_contains_a_blacklisted_authority(self) -> None:
        hits = scan(_scannable())
        assert not hits, chr(10).join(f"{path}: {pattern}" for path, pattern, _ in hits)


class TestTheGuardIsNotVacuous:
    """Prove the scanner catches a planted string.

    Without this, the self-referential exclusions above could silently grow
    until the guard examined nothing of consequence.
    """

    def test_a_planted_fabricated_case_is_detected(self, tmp_path: Path) -> None:
        probe = tmp_path / "probe.md"
        probe.write_text("As held in Schattinger v. Schattinger, curtailment is automatic.\n")
        hits = scan([probe])
        assert hits, "the scanner failed to detect a known-fabricated citation"
        assert hits[0][1] == "Schattinger v. Schattinger"

    def test_a_planted_overruled_case_is_detected(self, tmp_path: Path) -> None:
        probe = tmp_path / "probe.md"
        probe.write_text("See Rich, 625 P.2d 977, for the proposition that...\n")
        assert scan([probe]), "the scanner failed to detect the overruled Rich citation"

    def test_an_innocent_file_produces_no_hit(self, tmp_path: Path) -> None:
        """A scanner that flags everything is as useless as one that flags nothing."""
        probe = tmp_path / "clean.md"
        probe.write_text(
            "Curtailment authority rests on 23 CCR 875(b), upheld in Stanford Vina "
            "Ranch Irrigation Co. v. State of California (2020) 50 Cal.App.5th 976.\n"
        )
        assert not scan([probe])

    @pytest.mark.parametrize("entry", _blacklist(), ids=lambda e: e["kind"])
    def test_every_blacklist_entry_is_individually_detectable(self, entry: dict[str, str]) -> None:
        """Mutation-tests the fixture itself, one entry at a time.

        A malformed pattern that never matches anything would sit in the file
        looking protective while protecting nothing.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            probe = Path(td) / "probe.md"
            probe.write_text(f"prefix {entry['pattern']} suffix\n")
            hits = scan([probe])
            assert any(h[1] == entry["pattern"] for h in hits), (
                f"blacklist entry {entry['pattern']!r} does not match its own text"
            )
