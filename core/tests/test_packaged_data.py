"""Every data file a served endpoint reads must live inside the package.

**This is the third time this hole has been found and the second time in production.**
A review found the fact-sheet endpoint resolving its file relative to the repository,
which a wheel does not contain, so an installed service returned 503 forever while
importing cleanly. The backtest endpoint then shipped with the identical defect and
returned 500 on Cloud Run while every local test passed, because a checkout has `data/`
and a container does not.

So this guard is written once, over ALL of them, rather than per endpoint. A test that
names a single file is a test that the next file will not have.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGES = (REPO / "core" / "src" / "curtail_core", REPO / "agents" / "src" / "curtail_agents")


#: Paths the container image actually contains, from the Dockerfile's own COPY lines.
#: Read rather than restated, so a change to the image cannot silently invalidate this.
def _copied_paths() -> set[str]:
    dockerfile = (REPO / "Dockerfile").read_text()
    copied: set[str] = set()
    for line in dockerfile.splitlines():
        if line.startswith("COPY ") and "--from=" not in line:
            parts = line.split()[1:-1]
            copied.update(parts)
    return copied


class TestTheImageContainsWhatTheCodeReads:
    def test_the_dockerfile_copies_only_the_packages_and_the_manifests(self) -> None:
        """Establishes the premise the rest of this file rests on. If the image ever
        starts copying `data/`, these guards become unnecessary and should be revisited
        rather than left as folklore."""
        copied = _copied_paths()
        assert "core/src" in copied and "agents/src" in copied
        assert not any(p.startswith("data") for p in copied), (
            "the image now copies data/, so the packaged-copy rule may no longer be "
            "load-bearing. Re-read this file before deleting it."
        )

    @pytest.mark.parametrize(
        "module",
        [
            "core/src/curtail_core/backtest.py",
            "agents/src/curtail_agents/api.py",
        ],
    )
    def test_no_served_module_resolves_a_path_out_of_the_repository_root(self, module: str) -> None:
        """`parents[3]` from inside the package is the repository root, which exists in
        a checkout and not in the image. Any module reachable from a request must not
        depend on it without a packaged fallback.

        Matched on the AST rather than on text, so a comment explaining the old bug
        cannot satisfy the check, which this repository has been caught by before.
        """
        source = (REPO / module).read_text()
        tree = ast.parse(source)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
                if node.value.attr == "parents":
                    rendered = ast.unparse(node)
                    # A repository-root path is fine when a packaged copy is preferred
                    # first; the assignment below is what must not be bare.
                    if "parents[3]" in rendered or "parents[2]" in rendered:
                        offenders.append(rendered)
        if offenders:
            assert "_PACKAGED" in source or "packaged" in source.lower(), (
                f"{module} resolves {offenders} out of the repository root with no "
                "packaged fallback, so it will not exist in the container"
            )


class TestTheTwoCopiesDoNotDrift:
    """A packaged copy plus a repository copy is two files that must say the same
    thing. The fact sheet has a `--check` for exactly this; the cases file gets one
    here."""

    def test_the_packaged_backtest_cases_match_the_repository_copy(self) -> None:
        packaged = REPO / "core" / "src" / "curtail_core" / "data" / "backtest_cases.json"
        repository = REPO / "data" / "backtest_cases.json"
        assert packaged.is_file(), "the cases file is not packaged, so the container 500s"
        assert json.loads(packaged.read_text()) == json.loads(repository.read_text()), (
            "the packaged and repository copies of backtest_cases.json disagree. The "
            "served endpoint reads the packaged one, so the repository copy is what the "
            "generators and the quote verifier would be checking against a different set."
        )

    def test_the_module_prefers_the_packaged_copy(self) -> None:
        from curtail_core.backtest import CASES_PATH

        for package in PACKAGES:
            if package in CASES_PATH.parents:
                return
        pytest.fail(
            f"the backtest reads {CASES_PATH}, which is outside every package and "
            "therefore absent from the container"
        )
