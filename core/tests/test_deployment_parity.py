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

#: The service every cataloged agent must point at.
SERVICE_URL = "https://curtail-console-api-672785135387.us-central1.run.app"

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


#: The marker the probe writes when the registry itself could not be read.
REGISTRY_UNREADABLE = "The registry could not be read:"


class TestTheGovernanceTableMatchesTheLiveRegistry:
    """The governance table is read as a list of what is RUNNING.

    It said "Reachable, not yet populated" for days, which was true and honest. The
    moment four agents were registered it became false in the UNDERSTATING direction,
    and a stale disclaimer misleads a judge exactly as much as an inflated claim while
    being easier to miss, because nobody re-reads a sentence that was true when written.

    Nothing in the repository can see the registry, so this reads the probe's record.
    Same split as the deployment checks above: the network half runs locally, the
    assertion half runs in CI.
    """

    def _registered(self, record: str) -> int | None:
        """How many Curtail agents the record shows, or None when unknown.

        An unreadable registry and an empty one are indistinguishable from outside, and
        treating a missing credential as "nothing is registered" would turn an auth
        failure into a published claim.
        """
        if REGISTRY_UNREADABLE in record:
            return None
        for line in record.splitlines():
            if line.endswith("Curtail agents are discoverable in the registry."):
                return int(line.split()[0])
        if "**No Curtail agent is registered.**" in record:
            return 0
        return None

    def test_the_table_does_not_understate_a_populated_registry(self, record: str) -> None:
        count = self._registered(record)
        if count is None:
            pytest.skip("the registry could not be read, so this proves nothing")
        flat = " ".join(README.read_text().split())
        if count > 0:
            assert "not yet populated" not in flat, (
                f"{count} Curtail agents are registered and the governance table still "
                "says the registry is not populated, which understates the project on "
                "the axis worth 30 percent"
            )
            assert "no Curtail agent is registered yet" not in flat, (
                "the registry holds Curtail agents and the README denies it"
            )
        else:
            assert "populated" not in flat or "not yet populated" in flat, (
                "no Curtail agent is registered and the governance table implies one is"
            )

    def test_every_recorded_agent_points_at_the_deployed_service(self, record: str) -> None:
        """A catalog entry pointing somewhere else is worse than no entry.

        The M0 spike's probe was deleted for naming a placeholder host. This asserts the
        recorded catalog names THIS service, so a stale or copied registration cannot sit
        in the table looking governed.
        """
        count = self._registered(record)
        if count is None:
            pytest.skip("the registry could not be read, so this proves nothing")
        rows = [
            line for line in record.splitlines() if line.startswith("| Curtail ") and "http" in line
        ]
        assert len(rows) == count, (
            f"the record summarises {count} agents but lists {len(rows)} rows, so the "
            "table and its own count disagree"
        )
        for row in rows:
            assert SERVICE_URL in row, f"a cataloged agent does not point at the service: {row}"


class TestTheFactSheetAgreesWithTheRecordAboutTheRegistry:
    """The defect this class exists for got past `generate_facts.py --check`.

    That check compares `docs/FACTS.md` against its GENERATOR, and the generator held a
    hardcoded string: "No OpenTelemetry export, and no Curtail agent registered in Agent
    Registry." Four agents were registered, the sentence became false, and the check
    stayed green because the file and the generator were wrong TOGETHER.

    **A generated-artifact check cannot catch a false constant inside the generator.**
    It proves the artifact was regenerated, never that what it says is true. So the
    claim now comes from the probe's record, and this binds the two together.
    """

    FACTS = REPO / "docs" / "FACTS.md"

    def _recorded_count(self, record: str) -> int | None:
        if REGISTRY_UNREADABLE in record:
            return None
        for line in record.splitlines():
            if line.endswith("Curtail agents are discoverable in the registry."):
                return int(line.split()[0])
        if "**No Curtail agent is registered.**" in record:
            return 0
        return None

    def test_the_fact_sheet_does_not_deny_a_populated_registry(self, record: str) -> None:
        count = self._recorded_count(record)
        if count is None:
            pytest.skip("the registry could not be read, so this proves nothing")
        facts = " ".join(self.FACTS.read_text().split())
        if count > 0:
            assert "no Curtail agent registered" not in facts, (
                f"{count} Curtail agents are registered and the fact sheet still says "
                "none is. The generated line was a hardcoded string, which is why "
                "--check could not see it."
            )
            assert f"{count} Curtail agents were registered" in facts, (
                "the fact sheet does not state the recorded registry count in the PAST "
                "tense. A present-tense claim asserts live external state from a stored "
                "snapshot, and nothing in CI can refresh it."
            )
            for label in ("as recorded by", "against revision", "snapshot, not a live reading"):
                assert label in facts, (
                    f"the registry claim is missing its provenance stamp ({label!r}). "
                    "A figure quoted without saying when it was taken reads as current."
                )
        else:
            assert "No Curtail agent was registered" in facts, (
                "no agent is registered and the fact sheet does not say so"
            )

    def test_the_telemetry_claim_matches_the_shipped_source(self) -> None:
        """Three conditions, all required, because "imports opentelemetry" is a lie.

        ADK opens `invoke_node` and `invoke_workflow` spans on its own, so the library
        is imported and spans exist whether or not anything routes them anywhere. A
        guard keyed on the import would have reported telemetry wired while every span
        was created and dropped: structure present, force absent.

        So: exporter imported, provider actually installed, and the ENTRYPOINT calling
        the configuration. A function nobody invokes is the same defect one layer out.
        """
        import ast as _ast

        shipped = "\n".join(path.read_text() for path in (REPO / "agents" / "src").rglob("*.py"))
        called = {
            n.func.id
            for n in _ast.walk(_ast.parse(API.read_text()))
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)
        }
        wired = (
            "opentelemetry.exporter.cloud_trace" in shipped
            and "set_tracer_provider" in shipped
            and "configure_tracing" in called
        )
        facts = " ".join(self.FACTS.read_text().split())
        if wired:
            assert "No OpenTelemetry export" not in facts, (
                "the exporter is imported, a provider is installed and the entrypoint "
                "configures it, and the fact sheet still denies any export"
            )
            assert "export to Cloud Trace is WIRED" in facts, (
                "telemetry is wired and the fact sheet does not say so, which "
                "understates the governance wrap on the axis worth 30 percent"
            )
        else:
            assert "No OpenTelemetry export" in facts, (
                "telemetry is not fully wired and the fact sheet does not say so, "
                "which overstates the governance wrap"
            )


class TestTheRecordSaysWhenItWasTaken:
    """A snapshot without a timestamp reads as current, which is the whole problem.

    A second-model review pointed out that `_registry_claim()` trusts any committed
    record, so `FACTS.md` could keep asserting a count after the live registry changed,
    and the guard reading the same record would stay green.

    **That is inherent to the split and cannot be fixed by a check CI can run**, because
    CI deliberately never queries the network: a deliberately powered-down service must
    not redden the build. What CAN be fixed is the framing. A claim scoped to a moment
    stays true forever; a present-tense claim from a snapshot silently becomes false.

    So the record carries a probe time and the serving revision, the fact sheet quotes
    both in the past tense, and `make deployed-check` is the freshness mechanism. It
    re-probes and compares the registry section, verified: corrupting the recorded count
    makes it exit non-zero.
    """

    def test_the_record_carries_a_probe_time_and_a_serving_revision(self, record: str) -> None:
        assert "Probed at: `" in record, (
            "the record has no probe timestamp, so a reader cannot tell how old it is"
        )
        assert "Serving revision at probe time: `" in record, (
            "the record does not name the revision that answered. A repository commit "
            "says when the record was WRITTEN; the revision says what was PROBED, and "
            "they come apart because nothing here deploys on merge."
        )
        assert "This is a SNAPSHOT" in record, "the record does not tell a reader it is historical"

    def test_the_recorded_revision_is_not_a_placeholder(self, record: str) -> None:
        """`unknown` is honest but must not pass silently into a judged artifact.

        The probe degrades to `unknown` when gcloud is unavailable rather than failing,
        which is right for generation. This is where that becomes unacceptable.
        """
        for line in record.splitlines():
            if "Serving revision at probe time:" in line:
                value = line.split(":", 1)[1].strip().strip("`")
                assert value != "unknown", (
                    "the record could not determine the serving revision, so it does "
                    "not identify what was probed. Re-run the probe with gcloud "
                    "available."
                )
                assert value.startswith("curtail-console-api-"), (
                    f"the recorded revision {value!r} is not a revision of this service"
                )
                return
        pytest.fail("the record names no serving revision")
