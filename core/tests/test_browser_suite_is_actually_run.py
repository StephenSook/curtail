"""The guard on a deselection, which is the only thing making it safe.

The default pytest run carries `-m "not browser"`, forced by a plugin conflict: one
Playwright test disables pytest-asyncio's auto mode for every async test that runs
after it in the same session. The separation is correct and the reason is real.

**What is not safe is the separation on its own.** A deselected suite is one deleted
CI job away from never running again, and nothing would go red, because a suite that
does not execute reports no failures. That is the same shape as the guard this project
found skipping under CI for the life of a repository beneath a green check, and as the
`--watch` exit code that read 0 through a crash.

So the deselection is paired with this: the default suite asserts that the browser
suite still exists, is still marked, and is still executed somewhere that would fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"
SUITE = REPO / "agents" / "tests" / "test_console_browser.py"
PYPROJECT = REPO / "pyproject.toml"


@pytest.fixture(scope="module")
def commands() -> list[str]:
    """Only what actually EXECUTES: every `run:` block in the workflow.

    **The first version of this searched the raw file text and was vacuous.** It
    passed with the browser job's command deleted, because the string it looked for
    also appears in the step's `name:` and in the comment above it explaining why the
    step exists. A scanner matching its own explanation is the fifth instance of that
    defect in this repository, and the fix is the same every time: read the field
    that has force, never the file that describes it.
    """
    workflow = yaml.safe_load(CI.read_text())
    return [
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]


class TestTheDeselectionIsPairedWithAnExecution:
    def test_ci_still_runs_the_browser_suite(self, commands: list[str]) -> None:
        """The load-bearing assertion. Delete the job's command and this fails."""
        assert commands, "no run steps parsed out of the workflow, so this is vacuous"
        runs = [c for c in commands if "pytest" in c and "-m browser" in c]
        assert runs, (
            "no CI step EXECUTES `pytest -m browser`, so the browser suite is "
            "deselected from the default run and run nowhere. It would be silently "
            f"dead. Commands seen: {commands}"
        )

    def test_ci_installs_a_browser_for_it(self, commands: list[str]) -> None:
        """Chromium is not in the lockfile, so without this step the job fails rather
        than passes, but a missing browser and a broken console want different fixes
        and this keeps them distinguishable."""
        assert any("playwright install" in c for c in commands)

    def test_the_suite_carries_the_marker_it_is_selected_by(self) -> None:
        """A marker rename would leave `-m browser` collecting nothing.

        pytest exits 5 on an empty collection, so CI would fail rather than pass
        green on zero tests, which is the property that makes the whole arrangement
        safe. This checks the near half of it.
        """
        assert "pytestmark = pytest.mark.browser" in SUITE.read_text()

    def test_the_marker_is_declared(self) -> None:
        """`--strict-markers` is on, so an undeclared marker is an error rather than
        a typo that silently matches nothing."""
        assert "browser:" in PYPROJECT.read_text()

    def test_the_suite_still_covers_every_defect_it_was_written_for(self) -> None:
        """Non-vacuity, and the second version of it.

        The first checked that each keyword appeared ANYWHERE in the file, which a
        test renamed to `helper_...` satisfies, since the keyword survives inside the
        new name. Same family as the workflow guard above: match the thing with
        force, which here is a collected test's NAME, not the file that contains it.
        """
        import re

        names = re.findall(r"^    def (test_\w+)", SUITE.read_text(), re.MULTILINE)
        assert len(names) >= 6, f"the browser suite has shrunk to {len(names)} tests: {names}"

        for defect in (
            "network_failure",
            "non_json",
            "missing_field",
            "unknown_classification",
            "slow_earlier",
        ):
            assert any(defect in name for name in names), (
                f"no collected test covers {defect}, which was one of the defects an "
                f"adversarial review found in the console. Tests present: {names}"
            )
