"""CI gate: no fabricated authority reaches a shipped artifact.

Hard rule 13 says no legal citation enters any artifact without a verified
primary source. A rule enforced by vigilance does not survive the night before
a deadline, so this makes it mechanical (HARD RULE 74).

The self-reference problem is handled explicitly. The citations file and this
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
CITATIONS = REPO_ROOT / "agents" / "src" / "curtail_agents" / "data" / "citations.json"

#: Files that must contain the banned strings in order to do their job.
SELF_REFERENTIAL = {
    "agents/src/curtail_agents/data/citations.json",
    "core/tests/test_citation_guard.py",
}

#: Extensions worth scanning. Everything a judge, an official, or a reviewer reads.
SCANNED_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".txt", ".html", ".ts", ".tsx"}


def _tracked_files() -> list[Path]:
    """Every file that ships, plus every new file that is about to.

    `git ls-files` alone was a false green with a precise shape. A newly written
    file is untracked, so the guard could not see it, so the suite passed
    locally; the file was then committed, became tracked, and the very same
    assertion failed in CI. The local run was structurally incapable of catching
    it, and the first CI run after adding a file was the first real test of that
    file. That happened to `scripts/extract_corpus.py` on 2026-08-10.

    `--others --exclude-standard` adds untracked files that gitignore does not
    exclude, which is exactly the set that will ship on the next commit. Ignored
    files stay out: the research PDFs and the corpus are deliberately excluded
    and are not artifacts.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
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


class TestNoRetiredFigureShips:
    """A figure that propagated from research and appears in no source document.

    On 2026-08-10 a reading of 39.3 cfs was traced through the research hauls
    into the constitution, two test files, the corpus manifest and the public
    README. Reading Addendum 6 showed the string appears NOWHERE in it; the
    document records 45.3 and 46.5 cfs.

    This guard exists because the failure was not a typo. The number had a
    plausible provenance, survived a verification haul, and was asserted by a
    passing test. Only the primary document caught it.

    The rule is deliberately narrower than "this string must not appear". Code
    and docs SHOULD be able to explain what was corrected and why, or the
    correction becomes invisible to the next reader. So the figure may appear
    only on a line that also marks it as retired. Re-asserting it as a fact is
    what fails. Excluding whole files instead would have gutted the guard, which
    is the same trap the citation scanner's exclusions create.
    """

    RETIRED = "39.3"
    RETIREMENT_MARKERS = (
        "appears nowhere",
        "appears NOWHERE",
        "not in",
        "not_in",
        "RETIRED",
        "retired",
        "corrected",
        "CORRECTS",
        "earlier version",
        "propagated",
    )

    def _offending_lines(self, text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            if self.RETIRED not in line:
                continue
            if any(m in line for m in self.RETIREMENT_MARKERS):
                continue
            out.append(line.strip())
        return out

    def test_the_retired_reading_is_never_asserted_as_fact(self) -> None:
        offenders: list[tuple[str, str]] = []
        for path in _scannable():
            if path.name == "test_citation_guard.py":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in self._offending_lines(text):
                offenders.append((_display(path), line))
        assert not offenders, "the retired 39.3 cfs figure is asserted as fact in:\n" + "\n".join(
            f"  {p}: {ln}" for p, ln in offenders
        )

    def test_a_bare_assertion_of_the_retired_figure_is_caught(self) -> None:
        """Non-vacuity. The guard must fail on a naked re-assertion."""
        bad = "The Shasta ran at 39.3 cfs against a 50 cfs minimum.\n"
        assert self._offending_lines(bad), "guard failed to catch a bare re-assertion"

    def test_documenting_the_correction_is_allowed(self) -> None:
        """A guard that forbade explaining the fix would erase the lesson."""
        ok = "An earlier version asserted 39.3 cfs, which appears nowhere in the document.\n"
        assert not self._offending_lines(ok)
