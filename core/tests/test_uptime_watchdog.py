"""The watchdog that has to survive a month of nobody looking at it.

Judging runs Sept 1 to Oct 1 with this repository frozen. A service that dies
quietly in that window scores exactly as it would if it had never been deployed, so
the monitor is part of the deliverable rather than housekeeping.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "uptime.yml"
SCRIPT = REPO / "scripts" / "uptime_check.py"


@pytest.fixture(scope="module")
def workflow() -> str:
    return WORKFLOW.read_text()


class TestTheScheduleAvoidsTheContendedSlots:
    def test_the_cron_uses_offset_minutes(self, workflow: str) -> None:
        """GitHub deprioritises schedules on the hour and on round intervals. A
        `*/10` keepalive on a previous project fired every 65 to 80 minutes while
        every run reported success, so the cadence was wrong and nothing was red."""
        cron = re.search(r'cron:\s*"([^"]+)"', workflow)
        assert cron, "no cron schedule found"
        minutes = cron.group(1).split()[0]
        assert "*/" not in minutes, f"{minutes!r} is a round interval and gets deprioritised"
        assert minutes not in {"0", "00"}, "an on-the-hour schedule is the most contended slot"
        assert "," in minutes, "expected explicit offset minutes"

    def test_it_can_also_be_run_by_hand(self, workflow: str) -> None:
        """A monitor nobody can trigger cannot be verified on demand, which matters
        most in the hour before a deadline."""
        assert "workflow_dispatch" in workflow


class TestItChecksBehaviourRatherThanLiveness:
    def test_the_script_asserts_the_engines_actual_answer(self) -> None:
        """A 200 from a process that has lost its logic is worse than a 500, because
        it looks like health."""
        source = SCRIPT.read_text()
        assert "reading_near_threshold" in source
        assert "restrict" in source

    def test_it_asserts_the_refusals_too(self) -> None:
        """A service that stops refusing is answering from the wrong era's table or
        classifying a reading that does not exist, and both look like working
        software from outside."""
        source = SCRIPT.read_text()
        assert "MUST_REFUSE" in source
        assert "422" in source

    def test_it_checks_the_fact_sheet_is_still_served(self) -> None:
        assert "reproduces the direction" in SCRIPT.read_text()


class TestTheCheckRunsBareAndCanFail:
    def test_the_workflow_puts_no_pipe_on_the_exit_path(self, workflow: str) -> None:
        """A pipeline reports its LAST command's status. This project has shipped a
        merge on a red branch twice through `cmd | tail`, and the same mistake
        appeared while testing THIS script: `script | tail` reported exit 0 for a
        run that had failed five checks."""
        run_lines = [line for line in workflow.splitlines() if line.strip().startswith("run:")]
        assert run_lines, "no run steps found"
        for line in run_lines:
            assert "|" not in line, f"a pipe on the exit path: {line.strip()}"

    def test_the_script_exits_non_zero_on_failure(self) -> None:
        """Proven by running it, not by reading it. A monitor that cannot fail is
        a green light wired to nothing."""
        import subprocess

        result = subprocess.run(
            ["python3", str(SCRIPT), "--base", "https://example.com"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "::error::" in result.stdout


class TestNoUntrustedInputReachesTheWorkflow:
    def test_no_event_payload_is_interpolated(self, workflow: str) -> None:
        """Workflow injection: an event payload interpolated into a run block is
        attacker-controlled shell. This one takes none, and that stays true."""
        assert "github.event" not in workflow
        assert "${{" not in workflow.split("jobs:")[1], (
            "no expression interpolation belongs in these run steps"
        )
