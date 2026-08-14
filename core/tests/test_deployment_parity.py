"""The repository must not claim a capability the DEPLOYED service does not serve.

`docs/FACTS.md` computes its claims from repository source, and repository source
cannot see that production is stale. That gap shipped: the commit wiring the console
through the ADK graph made "the console runs the graph" true of the repo and left it
false of the live URL, because nothing here deploys on merge. Every gate was green
and the deployed service answered 404 on the endpoint the README had just begun to
advertise. A judge clicks the live URL, not the repository.

**No network here, deliberately.** `scripts/probe_deployment.py` needs the network
and writes `docs/DEPLOYMENT.md`; this reads that record. The split is the same one
the quote-provenance check uses, and it keeps a deliberately powered-down service
from turning the build red. A red monitor is a claim about the world, not about the
monitor.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "docs" / "DEPLOYMENT.md"
README = REPO / "README.md"
API = REPO / "agents" / "src" / "curtail_agents" / "api.py"

#: The marker the probe writes when it could not read the service at all.
UNREACHABLE = "## The probe did not reach the service"


@pytest.fixture(scope="module")
def record() -> str:
    assert RECORD.exists(), (
        "docs/DEPLOYMENT.md is missing. Run scripts/probe_deployment.py: without it "
        "nothing checks whether production serves what the repository claims."
    )
    return RECORD.read_text()


def _repo_builds_the_runner() -> bool:
    """Whether the SOURCE wires the console through the graph.

    Both names required. Building a runner and never driving it would leave the graph
    as unexercised as before while making this read true, which is the shape of claim
    these files exist to prevent.
    """
    tree = ast.parse(API.read_text())
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return {"build_fleet_runner", "run_fleet"} <= called


def _serves(record: str, capability: str) -> bool | None:
    """Whether the recorded deployment serves a capability, or None if unknown.

    None is a real answer and is not the same as False. An unreachable host and a
    host serving nothing produce identical evidence from outside, and treating the
    first as absence would fail the build for a network error.
    """
    if UNREACHABLE in record:
        return None
    for line in record.splitlines():
        if line.startswith("|") and capability in line:
            return "| yes |" in line
    return None


class TestTheDeployedServiceSupportsWhatTheRepositoryClaims:
    def test_the_record_names_the_service_and_the_commit(self, record: str) -> None:
        """A record that does not say what it describes, or when, is not evidence.

        The commit matters more than it looks: it is what lets a reader tell a probe
        taken before a feature landed from one taken after.
        """
        assert "Service: https://" in record, "the record does not name the service it probed"
        assert "Probed at repository commit: `" in record, (
            "the record does not name the commit it was taken at, so nobody can tell "
            "whether it predates the feature it is being read as evidence about"
        )

    def test_the_committed_record_is_a_successful_probe(self, record: str) -> None:
        """A failed probe must never be COMMITTED, or the guard goes quiet forever.

        The two checks below skip when the probe could not reach the service, which is
        right for a transient local outage and wrong as a permanent state: a committed
        unreachable record would skip on every CI run, under a green check, exactly the
        way a conditionally-skipped guard once let an unscanned bundle ship here.

        So the skip stays available for the local case and this makes it impossible to
        persist. Re-run the probe when the service is up rather than committing the
        failure.
        """
        assert UNREACHABLE not in record, (
            "docs/DEPLOYMENT.md records a failed probe. Committing that would silence "
            "the parity checks on every CI run. Re-run scripts/probe_deployment.py "
            "when the service is reachable."
        )

    def test_the_console_claim_matches_the_deployment(self, record: str) -> None:
        """Asserted in BOTH directions.

        The guards this replaces spoke only on the branch that was true before the
        work landed, so all of them went silent at exactly the commit that flipped
        the fact. A conditional guard is armed against the past.
        """
        served = _serves(record, "the console runs the graph")
        if served is None:
            pytest.skip("the probe could not reach the service, so this proves nothing")

        claims = "runs the graph" in " ".join(README.read_text().split())
        if _repo_builds_the_runner() and claims:
            assert served, (
                "the README says the console runs the graph and the recorded deployment "
                "does not serve /api/fleet/{basin}. Redeploy, or delete the sentence: a "
                "claim a judge cannot exercise on the live URL scores as absent."
            )
        if served:
            assert claims, (
                "the deployment serves the fleet endpoint and the README does not say "
                "so, which understates the project on the axis worth 40 percent"
            )

    def test_no_capability_is_recorded_as_unsupported_while_still_claimed(
        self, record: str
    ) -> None:
        """The generator writes this sentence itself when any row reads no.

        Guarding on the generator's own summary rather than re-deriving it means the
        two cannot disagree, which is the failure mode a hand-written line inside a
        generated file always has.
        """
        if UNREACHABLE in record:
            pytest.skip("the probe could not reach the service, so this proves nothing")
        assert "Claims the deployment does not support:" not in record, (
            "the recorded deployment does not support a capability the repository "
            "claims. Redeploy, or remove the claim."
        )
