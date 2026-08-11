"""The README is a judged artifact, so its claims are checked against the repository.

Drift here runs in BOTH directions and the second is the one that actually happened.
Overclaiming is the familiar failure: a README naming an integration the code does
not import. This repository shipped the mirror image instead, a README that said
"the build is at M0" and "nothing is claimed here that is not yet true" while four
milestones, seven hundred tests and a live chaos drill sat in the same commit. A
judge reading that would have scored the project it described rather than the one
that exists.

So the guards below check that quoted figures come from the generated fact sheet,
that every path named exists, and, specifically, that a "not built yet" placeholder
has not been overtaken by the thing it disclaims.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar

import pytest

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"
FACTS = REPO / "docs" / "FACTS.md"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text()


class TestQuotedFiguresComeFromTheGeneratedFactSheet:
    def test_the_headline_metric_matches_facts_exactly(self, readme: str) -> None:
        """One source for the number. FACTS.md is regenerated from the corpus and
        the engine and CI fails if it drifts, so a figure that agrees with it cannot
        outlive the thing it measured."""
        facts = FACTS.read_text()
        headline = next(
            line.strip().lstrip("> ").strip()
            for line in facts.splitlines()
            if "reproduces the direction" in line
        )
        assert headline, "no headline metric found in FACTS.md"
        # Blockquote markers stripped from BOTH sides before flattening. The README
        # wraps the quote across two lines, so a naive flatten leaves an inner ">"
        # and the comparison fails on formatting rather than on content, which would
        # train a reader to ignore this test.
        normalised = " ".join(_unquote(headline).split())
        readme_flat = " ".join(_unquote(readme).split())
        assert normalised in readme_flat, (
            "the README's headline metric does not match FACTS.md verbatim. Two "
            f"artifacts stating one number is two places for it to drift.\n{normalised}"
        )

    def test_the_corpus_counts_match_the_manifest(self, readme: str) -> None:
        manifest: dict[str, Any] = json.loads((REPO / "data" / "corpus_manifest.json").read_text())
        totals = manifest["totals"]
        status = manifest["extraction_status"]
        for figure in (
            str(totals["base_orders"]),
            str(totals["addenda"]),
            str(totals["documents_total"]),
            str(status["scorable"]),
        ):
            assert figure in readme, f"the README does not carry the manifest's {figure}"

    def test_the_readme_restates_no_count_that_goes_stale(self, readme: str) -> None:
        """The rule this repository already learned on a corpus manifest note.

        Prose beside data must not restate the data: a test count, a coverage
        percentage or a module tally is maintained in two places the moment it is
        written down, and the copy in prose is the one nobody updates. CI reports
        those; the README says what is true regardless of the number.
        """
        forbidden = re.findall(r"\b\d{3,4} (?:tests|assertions)\b", readme)
        assert not forbidden, (
            f"the README restates a count that CI already reports: {forbidden}. It "
            "will be wrong by the next commit."
        )
        percentages = re.findall(r"\b\d{2}(?:\.\d+)? percent coverage", readme)
        assert not percentages, f"coverage restated in prose: {percentages}"


class TestEveryPathTheReadmeNamesExists:
    def test_no_broken_repository_link(self, readme: str) -> None:
        """A README pointing at a file somebody removed is the cheapest possible
        way to look careless in front of a judge."""
        targets = re.findall(r"\]\((?!https?://|#)([^)]+)\)", readme)
        assert targets, "no repository links found, so this check would prove nothing"
        missing = [t for t in targets if not (REPO / t.split("#")[0]).exists()]
        assert not missing, f"the README links to paths that do not exist: {missing}"

    def test_every_named_module_is_real(self, readme: str) -> None:
        named = set(re.findall(r"`(make [a-z]+|[a-z_]+\.py)`", readme))
        modules = {m for m in named if m.endswith(".py")}
        for module in modules:
            found = list(REPO.rglob(module))
            assert found, f"the README names {module}, which does not exist"


class TestAPlaceholderCannotBeOvertakenByReality:
    """The drift that actually happened, in the direction nobody guards for.

    A "not built yet" marker is honest when it is true and misleading the moment the
    thing ships, and nothing about the marker itself goes red when that happens.
    Each marker below is mapped to the artifact that would falsify it.
    """

    #: Marker text -> a path whose existence would falsify that disclaimer.
    #:
    #: Typed so mypy can see both branches. An all-None table made the loop below
    #: unreachable, which mypy caught and which meant the guard was vacuous: it
    #: could never fire however stale the README became. The Memory Bank row was in
    #: fact already overtaken, since the Season Ledger persists session state, so
    #: the row now says which half is built and this entry keeps it honest.
    OVERTAKEN_BY: ClassVar[dict[str, str | None]] = {
        "Agent Runtime / Memory Bank": "agents/src/curtail_agents/ledger.py",
        "Agent Observability": None,  # genuinely not built
    }

    def test_no_marker_disclaims_something_that_now_exists(self, readme: str) -> None:
        for marker, artifact in self.OVERTAKEN_BY.items():
            if artifact is None:
                continue
            if not (REPO / artifact).exists():
                continue
            line = _line_containing(readme, marker)
            assert "**Partial.**" in line or "not built yet" not in line, (
                f"{marker!r} is marked 'not built yet' with no qualifier, but "
                f"{artifact} exists. A stale disclaimer understates the project to "
                "a judge, which is the same defect as overclaiming and just as "
                "wrong."
            )

    def test_the_status_line_no_longer_claims_the_build_is_at_m0(self, readme: str) -> None:
        """The specific sentence that was false. Named explicitly, because the
        general guard above cannot see prose that disclaims the whole project."""
        assert "the build is at M0" not in readme
        assert "It is empty because" not in readme

    def test_the_running_section_is_not_empty(self, readme: str) -> None:
        """It was, for four milestones. An empty evidence section in a judged
        artifact is a scored criterion left blank."""
        body = readme.split("## What is actually running", 1)[1].split("## ", 1)[0]
        assert len(body.strip()) > 500, "the 'what is actually running' section is a stub"


class TestTheDisclaimerAndLicenceSurvive:
    def test_the_not_a_government_system_disclaimer_is_present(self, readme: str) -> None:
        """Constitution hard rule 8. This one may never be edited away quietly."""
        assert "not an official government system" in readme

    def test_the_licence_is_named_and_the_file_exists(self, readme: str) -> None:
        assert "Apache-2.0" in readme
        assert (REPO / "LICENSE").exists()

    def test_no_em_dash_reaches_the_public_front_page(self, readme: str) -> None:
        """Constructed from codepoints, not typed literally.

        The CI gate solved this the same way: a file that bans a character must
        not contain it, or the linter flags the guard for the thing the guard
        exists to prevent.
        """
        for banned in ("\u2014", "\u2013"):
            assert banned not in readme, f"banned dash U+{ord(banned):04X} on the front page"


def _line_containing(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle in line:
            return line
    return ""


def _unquote(text: str) -> str:
    """Strip markdown blockquote markers so a comparison is about content."""
    return "\n".join(re.sub(r"^\s*>\s?", "", line) for line in text.splitlines())
