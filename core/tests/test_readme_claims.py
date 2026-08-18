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
        "Agent Runtime / Memory Bank": "agents/src/curtail_agents/season_store.py",
        # Was `None`, meaning "genuinely not built", and that entry was STALE: the
        # Cloud Trace exporter shipped and this row went on disclaiming it. A None
        # entry is skipped by the check below, so it is unfalsifiable BY
        # CONSTRUCTION, which is the vacuous-guard shape this suite exists to catch.
        # `test_no_entry_is_unfalsifiable` now forbids the whole branch.
        "Agent Observability": "agents/src/curtail_agents/telemetry.py",
    }

    def test_no_entry_is_unfalsifiable(self) -> None:
        """A `None` here means no artifact could ever contradict the disclaimer.

        The check below skips those, so registering one is registering a guard that
        cannot fire. It is exactly the stale exemption this project found in the env
        template on the same day: an excuse nobody re-examines, written down in the
        test suite, asserting that a capability does not exist.

        A genuinely unbuildable component would need a deliberate, separately named
        escape hatch rather than a quiet None.
        """
        unfalsifiable = sorted(k for k, v in self.OVERTAKEN_BY.items() if v is None)
        assert not unfalsifiable, (
            f"{unfalsifiable} are registered with nothing that could falsify them, so "
            "the staleness check skips them entirely. Name the artifact that would "
            "prove the disclaimer wrong."
        )

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
        # **This check used to conflate two questions and the conflation has now been
        # falsified.** It reasoned: the only session service is in memory, therefore
        # nothing persists, therefore the README must say "nothing persists". That
        # inference held only while the ADK session service was the sole candidate for
        # durability. `season_store.py` writes signed orders and their statutory clocks
        # to Firestore, so the graph's session state and the season's record are now
        # separate facts with opposite answers, and a test demanding the old sentence
        # would force the README to understate the project. A test that defends a
        # retired claim is worse than no test, because correcting the claim reads as
        # the regression.
        if "InMemorySessionService" in constructed and "DatabaseSessionService" not in constructed:
            assert "session state does not outlive a response" in flat, (
                "the only ADK session service constructed is in memory, so the README "
                "must say the graph's own session state does not survive the response. "
                "That is a narrower claim than 'nothing persists', and deliberately: "
                "the Season Ledger does persist."
            )

        durable_store = (REPO / "agents" / "src" / "curtail_agents" / "season_store.py").exists()
        if durable_store:
            for retired in ("nothing persists in production", "no database is wired"):
                assert retired not in flat, (
                    f"the README says {retired!r} while season_store.py ships a durable "
                    "store that production reports as durable. Understating a project "
                    "to a judge is the same defect as overclaiming, and it is the "
                    "direction nobody guards for."
                )

    def test_the_durability_claim_is_backed_by_failing_closed(
        self, readme: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file existing is not durability, and this test exists because it was.

        The first version of the check above concluded "season_store.py exists,
        therefore the README may claim production durability". A review pointed out
        that `store_for` caught an unreachable Firestore and returned the in-memory
        store, so a configured deployment could serve a volatile season for its whole
        life while this README said durable. **The guard could not see the
        contradiction because it was checking for a FILE and the claim is about
        BEHAVIOUR.**

        So this asserts the behaviour the claim rests on: a deployment that asked for a
        durable store and cannot have one raises, rather than quietly substituting.
        """
        if "durable" not in _flat(readme).lower():
            pytest.skip("the README makes no durability claim to back")

        import curtail_agents.season_store as store_module

        monkeypatch.delenv("CURTAIL_DISABLE_FIRESTORE", raising=False)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "a-configured-project")

        def _unreachable(project: str, client: object | None = None) -> object:
            raise store_module.SeasonStoreUnavailableError("unreachable")

        monkeypatch.setattr(store_module, "FirestoreSeasonStore", _unreachable)
        with pytest.raises(store_module.SeasonStoreUnavailableError):
            store_module.store_for()

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


def test_the_local_coverage_gate_matches_the_one_ci_runs() -> None:
    """A local gate weaker than the remote one is a false green by construction.

    The Makefile already carries that lesson for `mypy`, in a comment written after the
    paths drifted. The same drift was live for COVERAGE in the other direction: both
    measured `curtail_core` only, so the whole agents package was unmeasured by either,
    and `season_store.py` sat at 57% while the reported figure was 97%.

    This asserts the two invocations carry the same coverage flags, so widening one and
    forgetting the other cannot happen quietly.
    """
    makefile = (REPO / "Makefile").read_text()
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text()

    def _flags(text: str) -> set[str]:
        found: set[str] = set()
        for line in text.splitlines():
            if "pytest" in line and "--cov" in line:
                found |= {tok for tok in line.split() if tok.startswith("--cov")}
        return found

    local, remote = _flags(makefile), _flags(workflow)
    assert local, "no coverage invocation found in the Makefile"
    assert remote, "no coverage invocation found in the workflow"
    assert local == remote, (
        f"the local gate runs {sorted(local)} and CI runs {sorted(remote)}. They must "
        "measure the same thing, or one of them is reporting a number about a subset."
    )


class TestTheDeploymentRecordProvesCapabilityNotJustLiveness:
    """A deploy disabled two capabilities without anything going down.

    `gcloud run deploy --set-env-vars` REPLACES the environment. Using it to add a
    revision stamp wiped `GOOGLE_CLOUD_PROJECT`, the signing key and the demo
    passphrase. The service stayed up, every route answered, `/api/healthz` said `ok`,
    and the Season Ledger silently fell back to in-process memory.

    **Liveness could not see it because nothing was down.** So the record now carries
    what the container can DO, and these assert it.
    """

    @staticmethod
    def _record() -> str:
        return (REPO / "docs" / "DEPLOYMENT.md").read_text()

    def test_the_record_says_production_is_durable(self) -> None:
        assert "Season Ledger durable in production: **True**" in self._record(), (
            "the last probe found production NOT durable, which is what a wiped "
            "GOOGLE_CLOUD_PROJECT looks like from outside: everything answers, and the "
            "season is being kept in memory"
        )

    def test_the_record_names_the_commit_the_container_reports(self) -> None:
        record = self._record()
        assert "NOT STAMPED" not in record, (
            "the deployed container cannot say which commit it runs, so nothing can "
            "tell a current deployment from a stale one"
        )
        assert "Commit the container reports:" in record

    def test_the_deploy_target_cannot_wipe_the_environment(self) -> None:
        """The instance, guarded.

        **Continuations are joined first, and the first version did not do that.** A
        Makefile recipe is split across backslash-continued lines, so `--set-env-vars`
        sits on a different physical line from `gcloud run deploy`, and a line-by-line
        search cannot see it. The check read as covering the command and covered one
        line of it, which is the same narrow-scope defect this suite found in three
        other guards today. A mutation exposed it.
        """
        makefile = (REPO / "Makefile").read_text()
        joined = makefile.replace("\\\n", " ")
        commands = [ln for ln in joined.splitlines() if "gcloud run deploy" in ln]
        assert commands, "no deploy recipe found"
        for command in commands:
            assert "--set-env-vars" not in command, (
                "a deploy recipe uses --set-env-vars, which REPLACES the environment "
                "rather than adding to it. Use --update-env-vars: the difference wiped "
                "the signing key and silently dropped the Season Ledger to memory."
            )


def test_the_deployment_check_compares_the_capability_facts() -> None:
    """`--check` must actually look at the fields the record added.

    The durability and stamp lines were written into the record's HEADER first, and
    `_capability_block` starts lower down, so a deployment that lost either still
    reported "matches the live service". **A capability check that cannot see the
    capability is the shape this record exists to prevent**, occurring inside it.

    This asserts they sit inside the compared region, and that the revision VALUE does
    not: whether a container can name its commit is a stable fact, while which commit it
    names changes on every deploy, and comparing that would redden the gate for the
    system working normally.
    """
    import importlib.util

    path = REPO / "scripts" / "probe_deployment.py"
    spec = importlib.util.spec_from_file_location("probe_deployment", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    record = (REPO / "docs" / "DEPLOYMENT.md").read_text()
    compared = module._capability_block(record)

    assert "durable in production" in compared, (
        "the durability fact is outside the compared region, so a production database "
        "that vanished would still report as matching"
    )
    assert "reports its own commit" in compared, "the stamp fact is outside the compared region"

    volatile = re.findall(r"Commit the container reports: `([0-9a-f]{40})`", record)
    assert volatile, "the record does not carry the served commit at all"
    assert volatile[0] not in compared, (
        "the revision VALUE is inside the compared region, so this check goes red every "
        "time main advances, which is how a gate teaches people to override it"
    )


class TestThePreSubmitGateCannotSayReadyTooEarly:
    """A gate, not a checklist, and the exit code is the verdict.

    Near a deadline the dangerous failure is not a red suite. It is a GREEN one being
    read as "the submission is done" while the video does not exist, so this gate exits
    non-zero for as long as a human item is outstanding.
    """

    @staticmethod
    def _module() -> Any:
        import importlib.util
        import sys

        path = REPO / "scripts" / "pre_submit.py"
        spec = importlib.util.spec_from_file_location("pre_submit", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        # **Registered BEFORE exec, and that is required rather than tidy.** The
        # dataclass decorator resolves its field annotations through
        # `sys.modules[cls.__module__]`, so a module executed without being registered
        # dies with `'NoneType' object has no attribute '__dict__'` the moment it
        # defines one. The other loaders in this suite got away with it because those
        # modules define no dataclass.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_the_gate_can_actually_reach_ready(self, tmp_path: Path) -> None:
        """**The defect a review found: it could never pass.**

        The human items were hardcoded into `STEPS`, so `outstanding` was never empty
        and the gate exited non-zero forever. A gate that cannot pass is a gate people
        stop running, which is the failure this project keeps naming from the other
        direction, arriving inverted.

        So they are read from a file, and this proves the file can satisfy them.
        """
        module = self._module()
        links = tmp_path / "submission_links.json"
        links.write_text(
            json.dumps(
                {
                    "video_url": "",
                    "organization_name": "Individual",
                    "diagram_uploaded_at": "2026-08-31",
                }
            )
        )
        module.LINKS = links
        items = module._human_items()
        satisfied = {name: ok for name, _, ok in items}
        assert satisfied["organization name"] is True
        assert satisfied["architecture diagram uploaded"] is True
        assert any("video" in name and not ok for name, _, ok in items), (
            "an empty video URL must remain outstanding"
        )

    def test_an_empty_file_leaves_everything_outstanding(self, tmp_path: Path) -> None:
        """The other direction, so 'can reach ready' is not 'always ready'."""
        module = self._module()
        links = tmp_path / "submission_links.json"
        links.write_text(json.dumps({"video_url": "", "organization_name": ""}))
        module.LINKS = links
        assert all(not ok for _, _, ok in module._human_items())

    def test_a_typed_video_url_is_not_a_published_video(self, tmp_path: Path) -> None:
        """The rules require the content to be PUBLIC, not unlisted, so the gate asks
        the provider rather than trusting the string. A URL somebody typed proves only
        that they typed it."""
        module = self._module()
        ok, why = module._video_is_public("https://example.invalid/not-a-video")
        assert ok is False
        assert "YouTube or Vimeo" in why

    def test_the_exit_code_says_what_the_text_says(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**Asserted on the VERDICT, not the wording.**

        The earlier tests here read the source for phrases like "prove nothing", which
        is checking that a caveat is printed. A review pointed out that is not the same
        as checking it is acted on, and it was right: `--offline` printed the caveat and
        returned 0 anyway once the human items were filled. Three times in one session a
        caveat was printed while the exit code said otherwise.

        Every path is pinned by its number.
        """
        module = self._module()
        monkeypatch.setattr(module, "_run", lambda step: (True, "ok"))
        monkeypatch.setattr(module, "_human_items", lambda **_: [("video", "verified", True)])

        assert module.main(["--offline"]) == 3, (
            "offline SKIPS the deployed-service and drill checks, so it must never "
            "report READY however complete everything else is"
        )
        assert module.main([]) == 0, (
            "a full run with everything passing and nothing outstanding must be able to "
            "reach zero, or the gate is a permanent no"
        )

    def test_offline_makes_no_network_call_at_all(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """**The contract, tested by breaking the network rather than mocking past it.**

        `--offline` promised no network and then probed YouTube from `_human_items`,
        because the offline switch guarded the STEP list and that function is not in it.
        The tests here could not catch it: they mocked `_human_items` away, and a mock
        cannot violate the contract of the thing it replaced. Mocking a collaborator
        hides exactly the defects that live in the collaborator.

        So this poisons `urlopen` and lets the real function run.
        """
        module = self._module()
        links = tmp_path / "links.json"
        links.write_text(
            json.dumps(
                {
                    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "organization_name": "Individual",
                    "diagram_uploaded_at": "2026-08-31",
                }
            )
        )
        module.LINKS = links

        # **Counted, not raised.** The first version raised AssertionError from the
        # poisoned urlopen, and `_video_is_public` catches `Exception` broadly, so the
        # module swallowed the failure and the test passed under a mutation that put
        # the network call back. A probe that the code under test can catch is not a
        # probe. Counting cannot be swallowed.
        calls: list[str] = []

        def _record(url: object, *args: object, **kwargs: object) -> object:
            calls.append(str(url))
            raise OSError("unreachable")

        monkeypatch.setattr(module.urllib.request, "urlopen", _record)

        items = module._human_items(verify=False)
        assert calls == [], f"--offline made {len(calls)} network call(s): {calls}"
        video = next(name for name, _, _ in items if "video" in name)
        satisfied = {name: ok for name, _, ok in items}
        assert satisfied[video] is False, (
            "an unverified video must not count as satisfied: offline cannot know "
            "whether the URL is public, and unlisted counts as not public"
        )
        assert satisfied["organization name"] is True, (
            "the items needing no network must still be checked offline"
        )

    def test_main_offline_makes_no_network_call_through_the_real_helper(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """**Through `main`, with the real `_human_items`.**

        The sibling test above calls `_human_items(verify=False)` directly, which proves
        the helper honours the flag and proves nothing about whether `main` PASSES it.
        Changing that one call site to `verify=True` would put the network back into
        `--offline` with every test still green: the unit test would keep asserting the
        helper behaves, and the exit-code tests mock the helper away entirely.

        A review found that gap, which is the same shape as the defect it guards, one
        level up. So this one exercises the wiring: only `_run` is stubbed, because the
        subprocess steps are slow and orthogonal, and everything under test is real.
        """
        module = self._module()
        links = tmp_path / "links.json"
        links.write_text(
            json.dumps(
                {
                    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "organization_name": "Individual",
                    "diagram_uploaded_at": "2026-08-31",
                }
            )
        )
        module.LINKS = links
        monkeypatch.setattr(module, "_run", lambda step: (True, "ok"))

        calls: list[str] = []

        def _record(url: object, *args: object, **kwargs: object) -> object:
            calls.append(str(url))
            raise OSError("unreachable")

        monkeypatch.setattr(module.urllib.request, "urlopen", _record)

        code = module.main(["--offline"])
        assert calls == [], f"main --offline made {len(calls)} network call(s): {calls}"
        assert code == 3, "offline must report the skipped checks, whatever else passed"

    def test_main_online_does_reach_the_network_through_the_real_helper(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Non-vacuity for the wiring test above.

        If `main` passed `verify=False` unconditionally, the offline test would pass
        forever and the video would never be verified by anyone.
        """
        module = self._module()
        links = tmp_path / "links.json"
        links.write_text(json.dumps({"video_url": "https://www.youtube.com/watch?v=x"}))
        module.LINKS = links
        monkeypatch.setattr(module, "_run", lambda step: (True, "ok"))

        calls: list[str] = []

        def _record(url: object, *args: object, **kwargs: object) -> object:
            calls.append(str(url))
            raise OSError("unreachable")

        monkeypatch.setattr(module.urllib.request, "urlopen", _record)

        module.main([])
        assert calls, "main without --offline verified nothing"

    def test_verification_is_still_attempted_when_not_offline(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Non-vacuity: `verify=True` must actually reach for the network, or the
        offline branch is the only branch and the check verifies nothing."""
        module = self._module()
        links = tmp_path / "links.json"
        links.write_text(json.dumps({"video_url": "https://www.youtube.com/watch?v=x"}))
        module.LINKS = links
        called: list[str] = []

        def _record(url: object, *args: object, **kwargs: object) -> object:
            called.append(str(url))
            raise OSError("no network in tests")

        monkeypatch.setattr(module.urllib.request, "urlopen", _record)
        module._human_items(verify=True)
        assert called, "verify=True made no request, so nothing is being verified"
        assert "oembed" in called[0]

    def test_an_outstanding_human_item_exits_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = self._module()
        monkeypatch.setattr(module, "_run", lambda step: (True, "ok"))
        monkeypatch.setattr(module, "_human_items", lambda **_: [("video", "not published", False)])
        assert module.main([]) == 2

    def test_a_failed_check_exits_one_and_outranks_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken repository is reported as broken, not as 'waiting on a video'."""
        module = self._module()
        monkeypatch.setattr(module, "_run", lambda step: (False, "boom"))
        monkeypatch.setattr(module, "_human_items", lambda **_: [("video", "not published", False)])
        assert module.main([]) == 1

    def test_every_step_runs_something(self) -> None:
        """A step that runs nothing is decoration on a checklist."""
        for step in self._module().STEPS:
            assert step.command, f"{step.name!r} runs no command"

    def test_skipped_network_steps_are_reported_not_counted_as_passing(self) -> None:
        """`--offline` must say what it did not do.

        The same rule the chaos drill learned: a check that did not run is not a check
        that passed, and the difference has to be visible in the output rather than
        inferable from a flag somebody typed.
        """
        source = (REPO / "scripts" / "pre_submit.py").read_text()
        assert "prove nothing" in source
        assert "SKIP" in source

    def test_the_gate_runs_the_checks_that_guard_the_judged_artifacts(self) -> None:
        """Regenerating a claim is not the same as checking it is current, and the
        submission is judged on the committed files, not on what a rerun would say."""
        commands = " ".join(
            " ".join(s.command) for s in self._module().STEPS if s.command is not None
        )
        for required in ("generate_facts.py", "generate_submission.py", "deployed-check"):
            assert required in commands, f"the gate does not check {required}"


def test_the_deployment_record_states_durability_exactly_once() -> None:
    """One fact, one rendering, and the rendering that counts is the compared one.

    `build()` stated durability twice: in the volatile header alongside the store name,
    and again inside the compared capability block. Not wrong, and not stable: the
    header copy is the one `--check` never looks at, so the two could disagree with only
    the guarded one being enforced. That is the same one-fact-two-renderings defect this
    project fixed in the ledger and again in the console.

    The store NAME stays in the header, because which store is a detail that changes
    with configuration. Whether it is durable is the claim.
    """
    record = (REPO / "docs" / "DEPLOYMENT.md").read_text()
    occurrences = record.count("durable in production")
    assert occurrences == 1, (
        f"durability is stated {occurrences} times. One of them is outside the compared "
        "region and can drift unguarded."
    )


def _probe_module() -> Any:
    import importlib.util

    path = REPO / "scripts" / "probe_deployment.py"
    spec = importlib.util.spec_from_file_location("probe_deployment", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_image_paths_match_what_the_dockerfile_actually_copies() -> None:
    """The staleness guard is only as good as its list of runtime paths.

    `_runtime_drift` decides whether production is stale by diffing the paths that go
    into the image. A path added to the Dockerfile and not to `IMAGE_PATHS` is a runtime
    change the guard cannot see, which is the tracked-files blind spot one file over: a
    check whose scope is narrower than the thing it claims to cover.
    """
    module = _probe_module()
    dockerfile = (REPO / "Dockerfile").read_text()

    copied: set[str] = set()
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY ") or "--from=" in stripped:
            continue
        # COPY <src>... <dest>, so every token but the first and last is a source.
        tokens = stripped.split()[1:]
        copied.update(tokens[:-1])

    missing = sorted(copied - set(module.IMAGE_PATHS))
    assert not missing, (
        f"the Dockerfile copies {missing} into the image and IMAGE_PATHS does not list "
        "them, so a change to those would never be reported as a stale deployment"
    )


class TestTheStalenessGuardIsDecidableAndNonVacuous:
    """It must fire on a real runtime change and stay quiet on a cosmetic one.

    Both halves matter equally. A guard that never fires proves nothing, and a guard
    that fires on every commit gets routed around, which this project has already
    named as the way a check stops being run.
    """

    @staticmethod
    def _repo(tmp_path: Path) -> Path:
        import subprocess

        root = tmp_path / "repo"
        (root / "agents" / "src").mkdir(parents=True)
        (root / "docs").mkdir()

        def run(*args: str) -> None:
            subprocess.run(args, cwd=root, capture_output=True, check=True)

        run("git", "init", "-q")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "t")
        (root / "agents" / "src" / "app.py").write_text("VALUE = 1\n")
        (root / "docs" / "notes.md").write_text("first\n")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "base")
        return root

    @staticmethod
    def _at(module: Any, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(module, "REPO", root)

    def test_a_changed_runtime_file_reports_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        module = _probe_module()
        root = self._repo(tmp_path)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        (root / "agents" / "src" / "app.py").write_text("VALUE = 2\n")
        subprocess.run(["git", "commit", "-aqm", "runtime"], cwd=root, check=True)

        self._at(module, root, monkeypatch)
        verdict, paths = module._runtime_drift(base)
        assert verdict == "no", "a changed runtime file did not report as a stale deployment"
        assert paths == ["agents/src/app.py"]

    def test_a_docs_only_change_stays_quiet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        module = _probe_module()
        root = self._repo(tmp_path)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        (root / "docs" / "notes.md").write_text("second\n")
        subprocess.run(["git", "commit", "-aqm", "docs"], cwd=root, check=True)

        self._at(module, root, monkeypatch)
        verdict, paths = module._runtime_drift(base)
        assert (verdict, paths) == ("yes", []), (
            "a docs-only commit reported production as stale, which reddens the gate "
            "for the system working normally"
        )

    def test_an_unresolvable_commit_is_unknown_and_never_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The third value exists so a failed lookup cannot read as a clean result."""
        module = _probe_module()
        self._at(module, self._repo(tmp_path), monkeypatch)
        assert module._runtime_drift("0" * 40)[0] == "unknown"
        assert module._runtime_drift(None)[0] == "unknown"
        assert module._runtime_drift("")[0] == "unknown"

    def test_only_the_probe_stamp_moving_is_not_staleness_but_a_real_claim_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The normalization that lets this guard ever go quiet, and its limit.

        The generated fact sheet ships inside the image, so it is genuinely runtime
        source, and it carries a line naming the probe's own timestamp and the revision
        serving at that moment. Probing rewrites that line, so without normalization the
        guard reports stale, a deploy follows, the next probe rewrites it again, and the
        cycle never settles. A permanently red gate is one people route around.

        The limit matters as much: normalizing the stamp must not normalize the SHEET.
        A claim can change from an edit outside the image paths, and a live `/api/facts`
        serving the old claim is exactly the drift this record exists to catch.
        """
        import subprocess

        module = _probe_module()
        root = self._repo(tmp_path)
        sheet = root / "agents" / "src" / "curtail_agents" / "data" / "FACTS.md"
        sheet.parent.mkdir(parents=True)
        stamp = module.PROBE_STAMP_MARKER
        sheet.write_text(f"- a claim that is stable\n- 4 agents, {stamp}2026-01-01\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "sheet"], cwd=root, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        self._at(module, root, monkeypatch)

        # Only the stamp moves, which is what every probe does.
        sheet.write_text(f"- a claim that is stable\n- 4 agents, {stamp}2026-06-06\n")
        subprocess.run(["git", "commit", "-aqm", "restamp"], cwd=root, check=True)
        assert module._runtime_drift(base) == ("yes", []), (
            "a moved probe stamp reported as a stale deployment, which makes this gate "
            "permanently red by construction"
        )

        # A real claim moves, and that IS a stale deployment.
        sheet.write_text(f"- a claim that CHANGED\n- 4 agents, {stamp}2026-06-06\n")
        subprocess.run(["git", "commit", "-aqm", "claim"], cwd=root, check=True)
        verdict, paths = module._runtime_drift(base)
        assert verdict == "no", "a changed CLAIM in the served fact sheet reported as current"
        assert paths == ["agents/src/curtail_agents/data/FACTS.md"]


def test_every_figure_in_the_devpost_description_traces_to_a_source() -> None:
    """The submission description is the last artifact written and the least checked.

    `FACTS.md` states the rule in its own header: every number in the video narration,
    the README, the landing page and the Devpost description comes from that file and
    nowhere else. The README has guards. The description had none, and it is the text a
    judge actually reads.

    Numbers of two or more digits carry claims; a bare `4` does not. Section numbers
    like 875.9 and statute numbers like 1846 are claims too, and they are in scope on
    purpose: this project has already shipped a citation that named a real case for a
    proposition it never held.
    """
    description = (REPO / "docs" / "devpost_description.md").read_text()
    sources = (REPO / "docs" / "FACTS.md").read_text() + (REPO / "README.md").read_text()
    claims = set(re.findall(r"\$?[0-9][0-9,]+(?:\.[0-9]+)?", description))
    assert claims, "the description carries no figures at all, so this asserts nothing"

    flat = sources.replace(",", "")
    missing = sorted(c for c in claims if c not in sources and c.replace(",", "") not in flat)
    assert not missing, (
        f"{len(missing)} figure(s) in the Devpost description appear in neither FACTS.md "
        f"nor the README: {missing}. A number sourced from a memory ledger is how a "
        "published artifact ends up contradicting its own repository."
    )


def test_every_figure_on_the_thumbnail_traces_to_a_source() -> None:
    """The thumbnail carries numbers, and it is the artifact least able to be corrected.

    A judge sees it in a grid before reading a word, and unlike a README it cannot be
    edited after they have seen it. So the same rule the description lives under applies
    here: every figure comes from `FACTS.md` or the README.

    The HTML is checked rather than the PNG, because the HTML is the source the PNG is
    rendered from. Guarding the generated file instead would be guarding the copy.
    """
    source_html = (REPO / "docs" / "thumbnail.html").read_text()
    body = source_html.split("<body>", 1)[-1]
    sources = (REPO / "docs" / "FACTS.md").read_text() + (REPO / "README.md").read_text()
    claims = set(re.findall(r"\$?[0-9][0-9,]+(?:\.[0-9]+)?", body))
    assert claims, "the thumbnail carries no figures, so this asserts nothing"

    flat = sources.replace(",", "")
    missing = sorted(c for c in claims if c not in sources and c.replace(",", "") not in flat)
    assert not missing, (
        f"the thumbnail states {missing}, which appears in neither FACTS.md nor the "
        "README. An image cannot be corrected after a judge has seen it."
    )


def test_the_rendered_thumbnail_and_gallery_exist_and_are_not_blank() -> None:
    """Generated images, guarded by size the way the diagram already is.

    A blank PNG uploads perfectly well and looks like a successful upload, which is the
    false-green shape this project keeps finding in other forms.
    """
    thumbnail = REPO / "docs" / "thumbnail.png"
    assert thumbnail.is_file(), "docs/thumbnail.png is missing. Run `make thumbnail`."
    assert thumbnail.stat().st_size > 20_000, "the thumbnail is blank or unstyled"

    gallery = sorted((REPO / "docs" / "gallery").glob("*.png"))
    assert len(gallery) >= 4, f"expected at least 4 gallery captures, found {len(gallery)}"
    thin = [p.name for p in gallery if p.stat().st_size < 20_000]
    assert not thin, f"these gallery captures are suspiciously small and may be blank: {thin}"


class TestTheChaosRecordProvesWhatTheVideoWillClaim:
    """`docs/CHAOS.md` is the source for every drill claim spoken on camera.

    The drill's strongest evidence used to live only in terminal output: FACTS.md said
    the drill "reports both verdicts" and recorded neither. The video narrates that
    finding, and a published video cannot be corrected.

    These run offline. The record is written by a networked script that calls a live
    vendor filter, and CI must never depend on that being reachable, exactly as with the
    deployment probe.
    """

    @staticmethod
    def _record() -> str:
        path = REPO / "docs" / "CHAOS.md"
        assert path.is_file(), "docs/CHAOS.md is missing. Run `make chaos-record`."
        return path.read_text()

    def test_it_records_a_complete_drill_and_never_a_partial_one(self) -> None:
        record = self._record()
        assert "3 of 3 scenarios demonstrated their guard in full" in record
        for banned in ("PARTIAL", "NOT EXERCISED", "did NOT run"):
            assert banned not in record, (
                f"the record contains {banned!r}, so it memorialises a half-demonstrated "
                "drill. The writer refuses to produce one, so this file is stale."
            )

    def test_it_names_all_three_scenarios(self) -> None:
        record = self._record()
        for scenario in ("Herald killed mid-run", "Poisoned order PDF", "Scribe hallucination"):
            assert scenario in record, f"the record does not name {scenario!r}"

    def test_it_carries_both_model_armor_verdicts(self) -> None:
        """The finding that only exists when the drill runs with a project set.

        Layer 2 matches the bare payload and misses the same payload embedded in an
        order, while layer 1 catches both. That is the argument for defence in depth,
        made with a measurement instead of an assertion, and it is the single strongest
        thing the drill produces.
        """
        record = self._record()
        assert "on the bare injection: match_found" in record, (
            "the record does not show Model Armor matching the bare injection"
        )
        assert "inside an order: clean" in record, (
            "the record does not show Model Armor missing the embedded injection, which "
            "is the half of the finding that argues for two layers"
        )

    def test_the_retry_then_escalate_path_is_recorded(self) -> None:
        """The rubric asks how the system recovers from a hallucination, by name."""
        record = self._record()
        assert "attempt 1: RETRY" in record
        assert "attempt 2: ESCALATE" in record
        assert "UNVERIFIED" in record


def test_the_documented_drill_command_actually_writes_the_record() -> None:
    """The target everyone runs must be the target that records.

    A review caught these drifted apart: `make chaos-recording` is what the README, the
    pre-submit gate and the recording instructions all name, and it only ran the drill.
    The recorder sat behind a SECOND, near-identically named target nobody was told
    about, so `docs/CHAOS.md` could sit stale for weeks while the gate passed and the
    video quoted it.

    **Two commands whose names differ by three characters are one command with a
    trap in it.** This asserts the documented one invokes the recorder, so the record
    cannot go stale by following the instructions.
    """
    makefile = (REPO / "Makefile").read_text()
    body = makefile.split("chaos-recording:", 1)[1].split("\n.PHONY", 1)[0]
    assert "record_chaos.py" in body, (
        "make chaos-recording no longer invokes the recorder, so following the "
        "documented workflow leaves docs/CHAOS.md stale while the gate passes on it"
    )
    assert "--print" in body, (
        "the recorder runs without --print, so the documented command produces no "
        "on-camera output and the recording shows nothing"
    )
    assert "chaos-record:" not in makefile, (
        "the near-identically named second target is back. That name collision is what "
        "let the record and the documented command drift apart."
    )


def test_the_install_qr_encodes_the_field_url() -> None:
    """A QR code is the one artifact a reviewer cannot proofread.

    Printed in a README or held up in a video it is an opaque square, so a stale host
    inside it is invisible to every human who looks. Encoding is deterministic, so
    regenerating and comparing bytes proves the committed image encodes this URL and
    nothing else.
    """
    import importlib.util

    path = REPO / "scripts" / "render_qr.py"
    spec = importlib.util.spec_from_file_location("render_qr", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target = REPO / "docs" / "field-qr.png"
    assert target.is_file(), "docs/field-qr.png is missing. Run `make qr`."
    assert module._pixels(target.read_bytes()) == module._pixels(module.build()), (
        "the committed QR code does not encode " + module.FIELD_URL
    )
    assert module.FIELD_URL.endswith("/field"), "the QR points somewhere other than the field route"
    assert target.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_readme_does_not_promise_an_apk_it_does_not_ship() -> None:
    """Wired-or-cut, applied to a download link.

    A README that offers an `.apk` a reader can click must have one. If a release asset
    is ever added, this is the check that keeps the claim and the file together.
    """
    readme = (REPO / "README.md").read_text()
    offers_download = ".apk" in readme and (
        "releases/download" in readme or "Download the apk" in readme
    )
    if offers_download:
        assert "TWA_SHA256_FINGERPRINT" in (REPO / ".env.example").read_text(), (
            "the README offers an apk download and the trust link is not configurable, "
            "so the installed app would show a browser URL bar"
        )


def test_the_apk_the_readme_offers_is_actually_downloadable() -> None:
    """A download link on a judge-facing page must resolve.

    This project has already written down the failure: a hosted build artifact expires
    on the provider's retention clock, and the symptom is a dead link while the repo,
    the deploy and every check stay green. Release assets have no clock, and this
    asserts the README points at one rather than at a CI artifact.
    """
    readme = (REPO / "README.md").read_text()
    if "curtail-field.apk" not in readme:
        return  # nothing claimed, nothing to check
    assert "releases/download/" in readme, (
        "the README offers an apk from something other than a release asset. CI "
        "artifacts expire; release assets do not."
    )
    assert "/.well-known/assetlinks.json" in readme, (
        "an apk is offered with no way for a reader to see the fingerprint it is verified against"
    )
    assert "keytool -printcert" in readme, (
        "the README offers a binary download and no way to verify it is the one we "
        "signed, which is the whole point of publishing the fingerprint"
    )


def test_every_qr_code_encodes_its_url() -> None:
    """No QR code is readable by a human, so every one is checked by machine.

    Asserted over whatever `CODES` contains, plus the names that must be present.
    The first version asserted `len(CODES) == 2`, which broke the moment a third code
    was added and taught nothing when it did: a magic count is a claim about the
    inventory that has to be edited by hand every time the inventory changes, which is
    the same rot as restating a test count in the README.
    """
    import importlib.util

    path = REPO / "scripts" / "render_qr.py"
    spec = importlib.util.spec_from_file_location("render_qr", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.CODES, "no QR codes are declared at all"
    for name, url in module.CODES:
        target = REPO / "docs" / name
        assert target.is_file(), f"docs/{name} is missing. Run `make qr`."
        assert module._pixels(target.read_bytes()) == module._pixels(module.build(url)), (
            f"docs/{name} does not encode {url}"
        )
    urls = dict(module.CODES)
    assert urls["field-qr.png"].endswith("/field")
    assert urls["apk-qr.png"].endswith(".apk")
    # The TestFlight link is permanent from the moment the external group exists, so it
    # is pinned by host and shape rather than by whether the beta is open today.
    assert urls["testflight-qr.png"].startswith("https://testflight.apple.com/join/")


class TestNoSurfaceClaimsAnArtifactThatDoesNotExist:
    """The wired-or-cut rule, applied to the submission prose rather than to code.

    A rewrite of the Devpost description asserted "The demo video shows the Cloud Run
    console, a Cloud Trace Gantt across agent hops, and the Cloud Scheduler jobs,
    unedited." **No video existed.** It was a claim about an artifact that had not been
    built, on the single surface the Official Rules say judges may score without ever
    touching the app.

    The instance was easy to fix. The CLASS needs a guard, because the next one will be
    written the same way: describing what the finished submission will look like, in the
    present tense, in a file that ships.
    """

    SURFACES = ("README.md", "docs/devpost_description.md")

    def video_is_published(self) -> bool:
        links = json.loads((REPO / "docs" / "submission_links.json").read_text())
        return bool(str(links.get("video_url", "")).strip())

    #: Claims verified BY A HUMAN against the published video, recorded verbatim as the
    #: line's stripped text. Publication turns "video shows X" from categorically false
    #: into merely unverified, and a URL proves existence, not content. To retire a
    #: failure here: watch the published video, confirm the sentence, add the exact
    #: line. Empty means every present-tense content claim currently fails, which is
    #: the strict direction (an empty allowlist rejects everything, it approves
    #: nothing).
    CLAIMS_VERIFIED_AGAINST_PUBLISHED_VIDEO: frozenset[str] = frozenset()

    def _asserting_claim_lines(self) -> list[tuple[str, str]]:
        """(location, stripped line) for every present-tense video-content claim.

        A future or planned framing is fine and is deliberately not matched.
        """
        asserting = re.compile(
            r"\b(?:the\s+)?(?:demo\s+)?video\s+(?:shows|demonstrates|proves|contains|"
            r"captures|records|walks through)\b",
            re.IGNORECASE,
        )
        return [
            (f"{name}:{number}", line.strip())
            for name in self.SURFACES
            for number, line in enumerate((REPO / name).read_text().splitlines(), 1)
            if asserting.search(line)
        ]

    def test_the_video_is_not_described_in_the_present_tense_before_it_exists(self) -> None:
        offenders = self._asserting_claim_lines()
        if not self.video_is_published():
            assert not offenders, (
                "a judge-facing surface says the video shows something and no video_url "
                "is recorded in docs/submission_links.json, so the artifact does not "
                "exist yet:\n" + "\n".join(f"{loc}: {line[:110]}" for loc, line in offenders)
            )
            return

        # Published, never skipped: CI fails the whole job on a skip, because a skipped
        # guard is a false green. A recorded URL proves the video EXISTS, not that it
        # shows what the prose says, so the scan keeps running and each claim must be
        # individually verified against the published film. CI never queries the
        # network; `make pre-submit` probes the live URL.
        links = json.loads((REPO / "docs" / "submission_links.json").read_text())
        video_url = str(links.get("video_url", "")).strip()
        assert re.fullmatch(
            r"https://(?:youtu\.be/[\w-]{11}|www\.youtube\.com/watch\?v=[\w-]{11})",
            video_url,
        ), f"video_url is recorded but is not a YouTube video URL: {video_url!r}"
        assert "oembed" in str(links.get("_video_evidence", "")), (
            "video_url is recorded without an _video_evidence note naming the "
            "oembed check, so publication was reported rather than verified"
        )
        unverified = [
            (loc, line)
            for loc, line in offenders
            if line not in self.CLAIMS_VERIFIED_AGAINST_PUBLISHED_VIDEO
        ]
        assert not unverified, (
            "a judge-facing surface asserts what the video shows, and that sentence has "
            "not been verified against the published film. Watch the video, confirm the "
            "claim, then record the exact line in "
            "CLAIMS_VERIFIED_AGAINST_PUBLISHED_VIDEO:\n"
            + "\n".join(f"{loc}: {line[:110]}" for loc, line in unverified)
        )

    def test_the_guard_knows_when_it_would_stop_applying(self) -> None:
        """Guards the guard. If `video_url` were read from the wrong place this would
        skip forever and never protect anything, which is the vacuous-guard shape this
        repository keeps meeting."""
        links = json.loads((REPO / "docs" / "submission_links.json").read_text())
        assert "video_url" in links, (
            "submission_links.json has no video_url key, so the check above can never "
            "become active and is permanently vacuous"
        )
