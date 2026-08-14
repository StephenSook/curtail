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

    @staticmethod
    def _placeholders_in(readme: str) -> list[tuple[str, str]]:
        """EVERY "not built yet" occurrence, as (label, line) pairs.

        A list, not a dict, and a review is why. Keying by label silently collapsed
        duplicates: a second row whose first cell matched a registered one
        overwrote it and vanished, so an unregistered placeholder could hide behind
        a registered label in a guard whose whole claim is "every placeholder".
        Losing an occurrence during DISCOVERY defeats a completeness check more
        quietly than any bug in the check itself.

        DISCOVERED, not listed. A review pointed out that a hardcoded pair let any
        new placeholder pass silently while the class docstring claimed to cover
        every one, which is a guard whose stated scope exceeds its actual scope: the
        same defect it exists to catch, one level up.

        A table row is labelled by its first cell, and any other line by the line
        itself, so a placeholder cannot escape by being written in prose.
        """
        found: list[tuple[str, str]] = []
        for line in readme.splitlines():
            if "not built yet" not in line:
                continue
            # The status line DESCRIBES the convention rather than using it, and the
            # guard found it immediately, which is the guard working. A sentence
            # explaining what a marker means is not a claim about a component, so it
            # is excluded here explicitly. Any other meta-reference will fail this
            # test and have to be excluded deliberately, which is the right cost.
            if "Sections marked" in line:
                continue
            if line.lstrip().startswith("|"):
                label = line.strip().strip("|").split("|")[0].strip()
            else:
                label = line.strip()
            found.append((label, line))
        return found

    def test_every_placeholder_is_registered(self, readme: str) -> None:
        """Registry completeness, enforced rather than assumed.

        Adding a disclaimer without registering what would falsify it is how this
        guard would quietly stop covering the README it guards.
        """
        discovered = self._placeholders_in(readme)
        assert discovered, "no placeholders found, so the checks below prove nothing"
        unregistered = sorted({label for label, _ in discovered} - set(self.OVERTAKEN_BY))
        assert not unregistered, (
            f"these carry a 'not built yet' marker with no entry in OVERTAKEN_BY: "
            f"{unregistered}. Register each one with the path that would falsify it, "
            "or with None if it is genuinely unbuilt, so a placeholder overtaken by "
            "reality cannot pass unnoticed."
        )

    def test_the_registry_names_no_placeholder_the_readme_lacks(self, readme: str) -> None:
        """The other direction. A stale registry entry is a guard watching a line
        that no longer exists, which reads as coverage and is not."""
        labels = {label for label, _ in self._placeholders_in(readme)}
        stale = sorted(
            marker
            for marker in self.OVERTAKEN_BY
            if marker not in labels and not _line_containing(readme, marker)
        )
        assert not stale, f"OVERTAKEN_BY names markers absent from the README: {stale}"

    def test_no_marker_disclaims_something_that_now_exists(self, readme: str) -> None:
        for marker, artifact in self.OVERTAKEN_BY.items():
            if artifact is None:
                continue
            if not (REPO / artifact).exists():
                continue
            line = _line_containing(readme, marker)
            # Prefix, not the exact literal. The guard wants a "Partial" QUALIFIER on
            # a line carrying a "not built yet" marker, and it was matching
            # "**Partial.**" character for character, so rewording the qualifier to say
            # more broke a check about whether the qualifier was there at all.
            assert "**Partial" in line or "not built yet" not in line, (
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


class TestPlaceholderLabelsAreUnique:
    """The registry keys on label, so two placeholders sharing one cannot be told
    apart, and the second would inherit the first's registration.

    Discovery now preserves every occurrence, which surfaces the collision instead
    of swallowing it. Enforcing uniqueness is what makes the registration check mean
    what it says.
    """

    def test_no_two_placeholders_share_a_label(self, readme: str) -> None:
        from collections import Counter

        discovered = TestAPlaceholderCannotBeOvertakenByReality._placeholders_in(readme)
        counts = Counter(label for label, _ in discovered)
        duplicated = sorted(label for label, n in counts.items() if n > 1)
        assert not duplicated, (
            f"these placeholder labels appear more than once: {duplicated}. The "
            "registry is keyed by label, so the second would inherit the first's "
            "entry and evade the completeness check."
        )

    def test_discovery_returns_every_occurrence(self) -> None:
        """Non-vacuity for the fix itself. A discovery that still deduplicated would
        make the uniqueness test above impossible to fail."""
        sample = "| Thing | *(not built yet)* |\n| Thing | *(not built yet)* |\n"
        found = TestAPlaceholderCannotBeOvertakenByReality._placeholders_in(sample)
        assert len(found) == 2, f"discovery collapsed duplicates: {found}"


class TestTheFleetClaimIsComputedNotWritten:
    """The specific drift this file failed to catch.

    The README described the Core, Scribe and Herald as placeholders for a while after
    they had stopped being placeholders. The existing guards could not see it: they check
    a `not built yet` MARKER against a registry of file paths, and this was free prose.
    A stale disclaimer understates the project to a judge, which is the same defect as
    overclaiming and just as wrong.

    The fix is to make the claim computable. FACTS section 0 inspects each node's source
    and reports whether it returns its input unchanged, and these tests bind the README
    and the fact sheet to that computation.
    """

    @staticmethod
    def _passthrough_nodes() -> set[str]:
        import ast

        source = (REPO / "agents" / "src" / "curtail_agents" / "fleet.py").read_text()
        found: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if node.name not in {"_sentinel", "_core", "_scribe", "_herald"}:
                continue
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]
            if (
                len(body) == 1
                and isinstance(body[0], ast.Return)
                and isinstance(body[0].value, ast.Name)
                and body[0].value.id == "node_input"
            ):
                found.add(node.name.lstrip("_"))
        return found

    def test_the_fact_sheet_reports_every_node(self) -> None:
        facts = (REPO / "docs" / "FACTS.md").read_text()
        assert "## 0. What is wired" in facts
        for node in ("sentinel", "core", "scribe", "herald"):
            assert f"`{node}`" in facts, f"{node} is missing from the wired section"

    def test_the_fact_sheet_agrees_with_the_source(self) -> None:
        """The generated table must match what the AST says, or the generator has
        drifted from the thing it claims to inspect."""
        facts = (REPO / "docs" / "FACTS.md").read_text()
        passthrough = self._passthrough_nodes()
        for node in ("sentinel", "core", "scribe", "herald"):
            line = _line_containing(facts, f"| `{node}` |")
            if node in passthrough:
                assert "pass-through" in line, f"{node} IS a pass-through and the table denies it"
            else:
                assert "| yes |" in line, f"{node} acts and the table says otherwise"

    def test_the_readme_calls_no_acting_node_a_placeholder(self, readme: str) -> None:
        """The exact sentence that went stale. A node that acts must not be described as
        a placeholder anywhere in the README."""
        import re

        acting = {"sentinel", "core", "scribe", "herald"} - self._passthrough_nodes()
        for paragraph in readme.split("\n\n"):
            lowered = paragraph.lower()
            if "placeholder" not in lowered:
                continue
            # WORD boundaries. A substring test matched "core" inside "score" and
            # "record", so the Evidence paragraph tripped a guard about the fleet. A
            # checker that cries wolf is the one nobody reads when it is right.
            named = {node for node in acting if re.search(rf"\b{node}\b", lowered)}
            assert not named, (
                f"the README calls {sorted(named)} a placeholder, and the source says "
                "they act on their input. A stale disclaimer understates the project to "
                "a judge."
            )

    def test_the_readme_points_at_the_generated_section(self, readme: str) -> None:
        """Non-vacuity for the pair above: if the README stopped citing the computed
        source, the two could agree by coincidence rather than by construction."""
        assert "FACTS section 0" in _flat(readme)

    def test_no_row_claims_a_component_the_code_never_calls(self, readme: str) -> None:
        """Model Armor was listed as `Native` while nothing in the shipped code called
        it. A governance table is read as a list of what is running."""
        import re

        source = "\n".join(path.read_text() for path in (REPO / "agents" / "src").rglob("*.py"))
        armor_line = _line_containing(readme, "| Model Armor |")
        calls_armor = bool(re.search(r"model[_-]?armor", source, re.IGNORECASE))
        if not calls_armor:
            assert "NOT called" in armor_line, (
                "nothing in the shipped code calls Model Armor and the table does not say so"
            )


def _flat(text: str) -> str:
    """Collapse whitespace before matching a phrase.

    Markdown prose is hard-wrapped and gets rewrapped whenever a sentence changes, so a
    substring check against the raw file fails whenever a phrase happens to straddle a
    line break. That is a false negative in a guard, which trains a reader to ignore it,
    and it fired on correct text the first time this file checked a multi-word phrase.
    """
    return " ".join(text.split())


class TestNoClaimAboutTheDeployedServiceItDoesNotSupport:
    """The contradiction a review found after the first audit pass.

    The README said weeks-long session state "persists across a process restart via ADK
    DatabaseSessionService", citing ledger.py. That class appears nowhere in agents/src:
    it is constructed by a TEST, which injects one and proves the ledger round-trips. The
    deployed service constructs no session service at all, so it persists nothing.

    A neighbouring FACTS line then said "no database", and the two shipped together. The
    contradiction was the symptom; the cause was a claim about the DEPLOYED SERVICE that
    only a test supports.
    """

    @staticmethod
    def _api_source() -> str:
        return (REPO / "agents" / "src" / "curtail_agents" / "api.py").read_text()

    @staticmethod
    def _shipped_source() -> str:
        return "\n".join(path.read_text() for path in (REPO / "agents" / "src").rglob("*.py"))

    def test_the_readme_says_whether_the_http_path_runs_the_graph(self, readme: str) -> None:
        """40 percent of this track is whether the agent acts end to end, so the README
        has to say which of the two is true. It must never be able to say neither.

        **This guard went VACUOUS rather than red, and that is why it is shaped like
        this now.** It asserted the honest qualifier only `if not runs_graph`, so the
        moment `api.py` gained `build_fleet_runner` the whole body was skipped: the
        suite stayed green while the README went on saying the console "does not
        construct the ADK runner", which had just become false. A guard whose only
        assertion sits inside an `if` stops guarding at exactly the commit that
        changes the thing it watches.

        Both branches assert now, so whichever way the source goes, the README is
        checked against it.
        """
        runs_graph = "build_fleet_runner" in self._api_source()
        flat = _flat(readme)
        if runs_graph:
            assert "does not construct the ADK runner" not in flat, (
                "api.py builds the fleet runner and the README still says it does not"
            )
            assert "runs the graph" in flat, (
                "the console runs the fleet and the README does not say so, which "
                "understates the project on the axis worth 40 percent"
            )
        else:
            assert "does not construct the ADK runner" in flat, (
                "api.py never builds the fleet runner and the README does not say so"
            )

    def test_no_session_service_claim_beyond_what_the_source_constructs(self, readme: str) -> None:
        """ANY session service, not one named class.

        Keyed on `DatabaseSessionService(` alone, this passed happily when the fleet
        endpoint began constructing an `InMemorySessionService` per request, leaving
        both artifacts asserting that nothing in `agents/src` constructs one. The
        question was never which class: it is whether the deployed service builds a
        session store at all, and what it therefore persists.
        """
        source = self._shipped_source()
        constructed = sorted(
            name
            for name in ("DatabaseSessionService", "InMemorySessionService")
            if f"{name}(" in source
        )
        flat = _flat(readme)
        if not constructed:
            assert "Nothing in `agents/src` constructs a session service" in flat, (
                "the README claims persistence the shipped service cannot provide"
            )
        else:
            assert "Nothing in `agents/src` constructs a session service" not in flat, (
                f"{constructed} is constructed in agents/src and the README denies it"
            )
        if "InMemorySessionService" in constructed and "DatabaseSessionService" not in constructed:
            assert "nothing persists" in flat or "persists nothing" in flat, (
                "the only session service constructed is in memory, so the README must "
                "say that nothing survives the response rather than leaving a reader to "
                "assume a season ledger exists"
            )

    def test_the_fact_sheet_agrees_with_the_readme_about_persistence(self) -> None:
        """The two artifacts must not be able to contradict each other, which is what
        happened: one said state persists, the other said there is no database."""
        facts = (REPO / "docs" / "FACTS.md").read_text()
        source = self._shipped_source()
        if "InMemorySessionService(" in source or "DatabaseSessionService(" in source:
            assert "No session service is constructed anywhere in `agents/src`" not in facts, (
                "a session service is constructed and the fact sheet still denies it"
            )
        else:
            assert "No session service is constructed anywhere in `agents/src`" in facts
        assert "no database" not in facts.lower().replace("no database, and", ""), (
            "the fact sheet still makes the blunt claim that contradicts the ledger test"
        )

    def test_no_runtime_dependency_is_used_only_by_tests(self) -> None:
        """sqlalchemy and aiosqlite were runtime dependencies of the agents package and
        nothing in agents/src imported either. A runtime dependency nothing runs is a
        claim about the system, checkable by anyone in one grep."""
        import re

        manifest = (REPO / "agents" / "pyproject.toml").read_text()
        block = manifest.split("dependencies = [", 1)[1].split("]", 1)[0]
        declared = {
            match.group(1).replace("-", "_")
            for match in re.finditer(r'"([A-Za-z][A-Za-z0-9_-]+)', block)
        }
        source = self._shipped_source()
        # Only the ones this test knows how to check by import name.
        for name in ("sqlalchemy", "aiosqlite"):
            if name in declared:
                assert re.search(rf"\b{name}\b", source), (
                    f"{name} is a runtime dependency of the agents package and nothing "
                    "in agents/src imports it"
                )


class TestTheReachabilityTableIsComputed:
    """The correction to the correction to the correction.

    Version one said the HTTP surface calls "the four node functions"; it called three
    domain functions the nodes wrap, and `deliver_order` had no call site at all.
    Version two computed that per node and was right about the wrong question: it asked
    whether `api.py` calls each node's function DIRECTLY, which stopped being the
    interesting question the moment the console started running the graph. A node
    reached through a traversal is reached.

    So the table answers two separable questions now, because collapsing them is what
    hid the Scribe: whether the node RUNS THE FUNCTION IT IS NAMED FOR, and whether the
    CONSOLE reaches it. The Scribe was acting on its input, and sanitizing rather than
    drafting, so a single column reported it green while the graph could not produce an
    order at all.
    """

    #: The domain function each node wraps, mirrored from the generator on purpose: if
    #: the two ever disagree, this test fails rather than silently checking nothing.
    NODE_LOGIC: ClassVar[dict[str, str]] = {
        "sentinel": "evaluate",
        "core": "recommend",
        "scribe": "draft_order",
        "herald": "deliver_order",
    }

    @staticmethod
    def _called_in_api() -> set[str]:
        import ast

        tree = ast.parse((REPO / "agents" / "src" / "curtail_agents" / "api.py").read_text())
        return {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    def test_the_table_says_whether_the_console_runs_the_graph(self) -> None:
        """Asserted in BOTH directions, because the guard that this replaces asserted in
        one and went silent at the commit that mattered."""
        facts = (REPO / "docs" / "FACTS.md").read_text()
        called = self._called_in_api()
        assert called, "no calls parsed out of api.py, so this proves nothing"
        runs_graph = {"build_fleet_runner", "run_fleet"} <= called
        for node in self.NODE_LOGIC:
            line = _line_containing(facts, f"| `{node}` |")
            if runs_graph:
                assert "yes, the console runs the graph" in line, (
                    f"the console runs the graph and the table denies it for {node}"
                )
            else:
                assert "does not run the graph" in line, (
                    f"the console does not run the graph and the table claims it does for {node}"
                )

    def test_a_node_that_does_not_run_its_own_function_is_named(self) -> None:
        """The column that would have caught the Scribe.

        It acted on its input for several revisions while never calling `draft_order`,
        so "acts on its input" was true and useless. This asks the sharper question and
        follows helpers, because both the Scribe and the Herald dispatch their domain
        function through `asyncio.to_thread`, where it is an argument rather than a call.
        """
        import ast

        module = ast.parse((REPO / "agents" / "src" / "curtail_agents" / "fleet.py").read_text())
        edges: dict[str, set[str]] = {}
        for fn in ast.walk(module):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names: set[str] = set()
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                if isinstance(call.func, ast.Name):
                    names.add(call.func.id)
                names.update(a.id for a in call.args if isinstance(a, ast.Name))
            edges[fn.name] = names

        def reaches(start: str, target: str) -> bool:
            seen: set[str] = set()
            queue = [start]
            while queue:
                current = queue.pop()
                if current in seen:
                    continue
                seen.add(current)
                if target in edges.get(current, set()):
                    return True
                queue.extend(edges.get(current, set()))
            return False

        facts = (REPO / "docs" / "FACTS.md").read_text()
        for node, function in self.NODE_LOGIC.items():
            line = _line_containing(facts, f"| `{node}` |")
            if reaches(f"_{node}", function):
                assert f"yes, `{function}`" in line, (
                    f"{node} runs {function} and the table denies it"
                )
            else:
                assert f"**no**, `{function}` is never called" in line, (
                    f"{node} never calls {function} and the table claims it does"
                )

    def test_the_generator_and_this_test_agree_on_the_mapping(self) -> None:
        """Both hold the node-to-function map. If they drift, the check above would be
        asking about functions nobody calls and passing for the wrong reason."""
        generator = (REPO / "scripts" / "generate_facts.py").read_text()
        for node, function in self.NODE_LOGIC.items():
            assert f'"_{node}": "{function}"' in generator, (
                f"the generator maps {node} differently, so this test checks the wrong function"
            )

    def test_the_readme_agrees_with_the_fact_sheet_about_the_console(self, readme: str) -> None:
        """A judge may never open the fact sheet, so the README carries the same claim.

        Both branches assert. The predecessor only spoke when a node was unreachable,
        which meant the README's stale "Herald is not reachable through the console"
        sentence would have survived the commit that made it reachable.
        """
        called = self._called_in_api()
        flat = _flat(readme)
        runs_graph = {"build_fleet_runner", "run_fleet"} <= called
        if runs_graph:
            for node in self.NODE_LOGIC:
                assert f"{node.capitalize()} is not reachable through the console" not in flat, (
                    f"the console runs the graph and the README still says {node} is not reachable"
                )
        else:
            for node, function in self.NODE_LOGIC.items():
                if function in called:
                    continue
                assert f"{node.capitalize()} is not reachable through the console" in flat, (
                    f"{node} cannot be exercised through the console and the README is silent"
                )
