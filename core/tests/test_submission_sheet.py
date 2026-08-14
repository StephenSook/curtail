"""The submission sheet, checked against the code rather than against its generator.

**Why not just a `--check` drift gate.** This project has already shipped a generated
artifact whose `--check` passed while the artifact was wrong, because the file and the
generator were wrong together. A regeneration check proves the file was regenerated. It
proves nothing about whether what it says is true.

So these read the COMMITTED sheet and verify each claim independently, the same way a
judge with the repository open would.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]


def _generator() -> Any:
    """The real generator module, imported once.

    **The stripper used to be duplicated here, and a review caught what that costs.**
    Copying the EVIDENCE TABLES is deliberate: they are claims, and two independent
    statements of a claim mean one wrong edit cannot propagate into the check that
    guards it. Copying the STRIPPER is the opposite, because it is a mechanism rather
    than a claim: the tests exercised this file's copy while production ran the
    generator's, so the generator's could regress with every test still green.

    Data gets duplicated on purpose. Machinery gets imported.
    """
    import importlib.util

    path = REPO / "scripts" / "generate_submission.py"
    spec = importlib.util.spec_from_file_location("generate_submission", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_executable = _generator().executable_source
SHEET = REPO / "docs" / "SUBMISSION.md"
SOURCE_DIRS = (REPO / "agents" / "src", REPO / "core" / "src")

#: A service the form offers, and what would have to be in the source to tick it.
#: Deliberately a SECOND copy of the generator's table: if both are edited in step the
#: claim is at least stated twice, and if only one is edited these tests fail. A single
#: shared constant would let a wrong edit propagate silently to the check that guards it.
EVIDENCE: dict[str, tuple[str, ...]] = {
    # Cloud Run was MISSING from this table while the generator could claim it, which a
    # review caught. The guard's name said "ticks nothing the code lacks" and it covered
    # four of the five services and none of the SDKs, so a sheet claiming Cloud Run or
    # either SDK passed unguarded. A guard whose stated scope exceeds its actual scope
    # is the defect it exists to catch, one level up, and this project has hit it before.
    # `test_the_guard_covers_every_claim_the_generator_can_make` now makes the gap
    # impossible rather than relying on someone noticing.
    # `PORT` was the first marker here and it was USELESS: it matches inside
    # `TRANSPORT`, `TRANSPORTS` and `SYNTHETIC_TRANSPORT`, so the check passed with the
    # Dockerfile's Cloud Run contract deliberately broken. A mutation caught it. Same
    # substring failure as `curtail` matching inside `curtailments` in the normalizer,
    # which is twice in one day: **a marker short enough to be convenient is usually
    # short enough to appear somewhere it means nothing.**
    "Cloud Run": ("CLOUD_RUN_REQUEST_TIMEOUT_SECONDS", "--proxy-headers"),
    "Cloud SQL": ("cloudsql", "cloud_sql"),
    "Firestore": ("google.cloud.firestore",),
    "Google Kubernetes (GKE)": ("container_v1",),
    "Pub/Sub": ("pubsub_v1.PublisherClient", "pubsub_v1.SubscriberClient"),
}

#: Claims proved by ALL their markers, not any one. Stated separately from the
#: generator's copy, like the evidence itself.
#:
#: Cloud Run has no import to point at: it is a deployment contract whose parts are
#: separable. Under `any`, deleting `--proxy-headers` from the Dockerfile still passed
#: because a Python constant naming the platform's request timeout remained, so the
#: contract this row asserts was not the thing being enforced. A review caught that the
#: tightened marker had only moved the weakness rather than removed it.
REQUIRE_ALL: frozenset[str] = frozenset({"Cloud Run"})

#: The SDK row was guarded by NOTHING. Same table shape, same independence.
SDK_EVIDENCE: dict[str, tuple[str, ...]] = {
    "Agent Development Kit (ADK)": ("google.adk",),
    "Google GenAI SDK (google-genai)": ("from google import genai", "google.genai"),
    "Antigravity SDK": ("antigravity",),
    "Genkit": ("genkit",),
}


@pytest.fixture(scope="module")
def sheet() -> str:
    assert SHEET.exists(), "docs/SUBMISSION.md is missing. Run `make submission`."
    return SHEET.read_text()


@pytest.fixture(scope="module")
def source() -> str:
    """Shipped Python AND the Dockerfile.

    The Dockerfile is where Cloud Run's evidence lives: the container reads `$PORT` and
    runs with `--proxy-headers`, which is the platform's contract and appears in no .py
    file. A source fixture that stopped at Python could never have verified the one
    service this project most obviously uses.
    """
    parts: list[str] = []
    for directory in SOURCE_DIRS:
        for path in directory.rglob("*.py"):
            if "__pycache__" not in path.parts:
                parts.append(_executable(path.read_text(), python=True))
    docker = REPO / "Dockerfile"
    if docker.exists():
        parts.append(_executable(docker.read_text(), python=False))
    return "\n".join(parts)


def _ticked(sheet: str, row: str) -> str:
    for line in sheet.splitlines():
        if line.startswith(f"| {row}"):
            return line
    return ""


class TestTheSheetTicksNothingTheCodeLacks:
    """The checkbox form of wired-or-cut, and the last artifact nobody tests."""

    @pytest.mark.parametrize("service", sorted(EVIDENCE))
    def test_a_claimed_service_has_its_evidence_in_the_source(
        self, service: str, sheet: str, source: str
    ) -> None:
        row = _ticked(sheet, "Google Cloud service(s)")
        assert row, "the sheet has no cloud services row"
        if service not in row:
            return  # not claimed, nothing to prove
        test = all if service in REQUIRE_ALL else any
        assert test(marker in source for marker in EVIDENCE[service]), (
            f"the sheet ticks {service} and the source does not carry "
            f"{'every' if service in REQUIRE_ALL else 'any'} marker in "
            f"{EVIDENCE[service]}. A dropdown is a claim about the stack."
        )

    @pytest.mark.parametrize("sdk", sorted(SDK_EVIDENCE))
    def test_a_claimed_sdk_has_its_evidence_in_the_source(
        self, sdk: str, sheet: str, source: str
    ) -> None:
        """The SDK row was guarded by nothing at all.

        It is the row a judge is most likely to check first, because the rules make one
        Google agent framework mandatory.
        """
        row = _ticked(sheet, "Google SDK")
        assert row, "the sheet has no SDK row"
        if sdk not in row:
            return
        assert any(marker in source for marker in SDK_EVIDENCE[sdk]), (
            f"the sheet ticks {sdk} and no shipped source matches {SDK_EVIDENCE[sdk]}"
        )

    def test_the_sheet_claims_at_least_one_sdk(self, sheet: str) -> None:
        """The rules require at least one Google agent framework, so an empty row is a
        disqualification rather than a modest answer."""
        row = _ticked(sheet, "Google SDK")
        assert any(sdk in row for sdk in SDK_EVIDENCE), f"no SDK is claimed: {row!r}"

    def test_every_model_it_names_is_in_the_source(self, sheet: str, source: str) -> None:
        row = _ticked(sheet, "Google AI models")
        named = set(re.findall(r"\b(gemini-[0-9.]+-[a-z-]+|gemma[0-9]?:[0-9a-z.]+)\b", row))
        assert named, "the sheet names no model, which cannot be right"
        missing = sorted(m for m in named if m not in source)
        assert not missing, f"the sheet names {missing}, absent from every shipped file"

    def test_pubsub_stays_unticked_while_nothing_publishes(self, sheet: str, source: str) -> None:
        """The tempting one, and the reason this file exists.

        `messaging.py` implements the ordering-key, dead-letter and dedup discipline
        against the real library's types, the package is a declared dependency, and it
        is genuinely good work. Nothing publishes to a topic. Ticking the box would be
        true of the design and false of the system.
        """
        publishes = any(m in source for m in EVIDENCE["Pub/Sub"])
        row = _ticked(sheet, "Google Cloud service(s)")
        if not publishes:
            assert "Pub/Sub" not in row, (
                "the sheet ticks Pub/Sub and no shipped source constructs a publisher or subscriber"
            )

    def test_the_architecture_diagram_it_promises_exists(self, sheet: str) -> None:
        assert "docs/architecture.png" in sheet
        assert (REPO / "docs" / "architecture.png").exists(), (
            "the sheet names a required upload that is not in the repository"
        )


class TestItSaysWhatIsStillMissing:
    def test_the_outstanding_section_is_not_empty(self, sheet: str) -> None:
        """A sheet listing only what is done reads as a finished submission.

        The video is the largest remaining piece, and a blank where it should be is
        indistinguishable from a decision that it is handled.
        """
        body = sheet.split("## Outstanding", 1)
        assert len(body) == 2, "the sheet has no outstanding section"
        assert "Demo video" in body[1], "the demo video is not listed as outstanding"

    def test_it_carries_the_dates_this_project_previously_had_wrong(self, sheet: str) -> None:
        """Judging ends Sept 24. Our own constitution said Oct 1 for weeks."""
        assert "2026-08-31 17:00 PT" in sheet
        assert "2026-09-24 17:00 PT" in sheet

    def test_it_names_the_required_field_that_reads_optional(self, sheet: str) -> None:
        """Devpost field 28086 asks for an organization name "if submitting on behalf of
        an Organization" and is `required: true` regardless. A form that rejects on a
        field nobody read as mandatory is a bad way to spend the last hour."""
        assert "required even though it reads optional" in sheet


class TestTheGuardCoversEverythingTheGeneratorCanClaim:
    """Registry completeness, so this gap cannot reopen when an option is added.

    The tables above are deliberately a SECOND statement of the evidence, so that one
    wrong edit cannot propagate into the check that guards it. That independence is
    worth keeping and it created the hole a review found: the generator grew a Cloud Run
    entry and an SDK table, and the guard did not, so a sheet claiming either passed.

    Comparing KEY SETS gets both properties. The guard has to know about every option the
    generator can emit, and it still states its own evidence for each.
    """

    @staticmethod
    def _generator_tables() -> tuple[set[str], set[str]]:
        module = _generator()
        return set(module.CLOUD_SERVICES), set(module.SDKS)

    def test_no_service_the_generator_knows_is_unguarded(self) -> None:
        services, _ = self._generator_tables()
        unguarded = sorted(services - set(EVIDENCE))
        assert not unguarded, (
            f"the generator can claim {unguarded} and no test verifies them. A guard "
            "whose stated scope exceeds its actual scope is the defect it exists to "
            "catch."
        )

    def test_no_sdk_the_generator_knows_is_unguarded(self) -> None:
        _, sdks = self._generator_tables()
        unguarded = sorted(sdks - set(SDK_EVIDENCE))
        assert not unguarded, f"the generator can claim {unguarded} and nothing checks them"

    def test_the_guard_names_nothing_the_generator_cannot_emit(self) -> None:
        """The other direction. A table entry for an option that no longer exists is a
        check watching a line that cannot appear, which reads as coverage and is not."""
        services, sdks = self._generator_tables()
        stale = sorted((set(EVIDENCE) - services) | (set(SDK_EVIDENCE) - sdks))
        assert not stale, f"these are guarded and the generator cannot emit them: {stale}"


class TestProseAboutARequirementIsNotTheRequirement:
    """The hole a review found: the evidence search read comments.

    This repository's Dockerfile carries `# --proxy-headers is not optional behind Cloud
    Run`. While whole files were searched, deleting `--proxy-headers` from the actual
    `CMD` left the Cloud Run claim standing on that sentence. A comment explaining why
    something is required was satisfying the check that it IS required, which is the
    documentation-instead-of-implementation failure this project has hit before at other
    layers.
    """

    def test_a_dockerfile_comment_does_not_count_as_configuration(self) -> None:
        commented = '# --proxy-headers is required\nCMD ["uvicorn", "app"]\n'
        assert "--proxy-headers" not in _executable(commented, python=False)
        assert "uvicorn" in _executable(commented, python=False), "real config was dropped"

    def test_a_python_comment_does_not_count_as_code(self) -> None:
        commented = "# google.cloud.firestore is what we would use\nimport os\n"
        stripped = _executable(commented, python=True)
        assert "google.cloud.firestore" not in stripped
        assert "import os" in stripped, "real code was dropped"

    def test_a_docstring_does_not_count_as_code(self) -> None:
        """Docstrings survive `ast.unparse`, so they are removed explicitly.

        This module's own docstrings name several of these markers while describing why
        they matter, which would have made the guard self-satisfying.
        """
        documented = '"""We talk about google.cloud.firestore in this docstring."""\nimport os\n'
        stripped = _executable(documented, python=True)
        assert "google.cloud.firestore" not in stripped
        assert "import os" in stripped

    def test_real_code_still_survives_the_stripping(self) -> None:
        """Non-vacuity. A stripper that removed everything would pass the three checks
        above and make every claim unprovable, which would read as a very strict guard
        and be no guard at all."""
        real = "import google.cloud.firestore as firestore\nX = firestore.Client()\n"
        stripped = _executable(real, python=True)
        assert "google.cloud.firestore" in stripped


class TestTheGeneratorsOwnPipelineIsWhatIsTested:
    """End to end through the generator, not through a copy of its logic.

    The stripper tests above now import the real function, which closes the gap a review
    found. This closes the rest of it: they exercise one helper, and what actually
    matters is that the GENERATOR's source pipeline excludes comments, since that is what
    decides the sheet's contents.
    """

    def test_the_generator_source_excludes_dockerfile_comments(self) -> None:
        source = _generator()._source()
        assert "is not optional behind Cloud Run" not in source, (
            "the generator's own source pipeline is reading Dockerfile comments, so a "
            "comment naming a flag can satisfy a claim about that flag"
        )

    def test_the_generator_source_still_contains_the_real_contract(self) -> None:
        """Non-vacuity, and it also proves the Dockerfile is read at all."""
        source = _generator()._source()
        assert "--proxy-headers" in source, "the CMD's real flag was stripped too"
        assert "CLOUD_RUN_REQUEST_TIMEOUT_SECONDS" in source

    def test_the_generator_source_excludes_python_docstrings(self) -> None:
        """`season_store.py`'s module docstring names Cloud SQL while explaining why it
        was NOT chosen. Read as evidence, that sentence would tick a box for a service
        this project deliberately does not use."""
        source = _generator()._source()
        assert "Why Firestore and not Cloud SQL" not in source

    def test_the_sheet_on_disk_matches_what_the_generator_produces(self) -> None:
        """The drift half, so the two checks together cover both failure directions:
        the sheet saying something the code does not, and the sheet being stale."""
        module = _generator()
        assert module.main(["--check"]) == 0, "docs/SUBMISSION.md has drifted"


class TestTheStartDateRefusesRatherThanGuessing:
    """It broke main, which is the honest way to have found it.

    `actions/checkout` defaults to depth 1, so `git log --reverse` on a runner returns
    the newest commit. The generator would have reported the date of whatever merge CI
    was building as the date the project began, and that is the ELIGIBILITY field: a
    start date outside the submission period disqualifies the entry.

    The shape is one this project has a name for: a check whose precondition holds only
    on the machine where it was written. It passed here for hours.
    """

    def test_it_refuses_on_a_shallow_clone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _generator()
        monkeypatch.setattr(module, "_is_shallow", lambda: True)
        with pytest.raises(SystemExit, match="shallow"):
            module._first_commit_date()

    def test_it_answers_on_a_full_clone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-vacuity: refusing always would be a very safe function and no use."""
        module = _generator()
        monkeypatch.setattr(module, "_is_shallow", lambda: False)
        assert re.fullmatch(r"\d{2}-\d{2}-\d{2}", module._first_commit_date())

    def test_ci_fetches_the_history_the_check_needs(self) -> None:
        """The other half. Refusing on a shallow clone only converts a wrong answer into
        a red build unless CI is given the history, so this asserts the workflow does."""
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        checkouts = workflow.count("actions/checkout@v5")
        depths = workflow.count("fetch-depth: 0")
        assert checkouts > 0, "no checkout steps found, so this proves nothing"
        assert depths == checkouts, (
            f"{checkouts} checkout step(s) and {depths} fetch-depth setting(s). Every "
            "job that runs the submission check needs full history."
        )
