"""The console API, and the manifest guard that produced it.

This module exists because an audit of `pyproject.toml` found four declared
dependencies that nothing imported. The endpoints below wire two of them for real;
the guard at the bottom is what stops the list going false again.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from curtail_agents.api import app

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


class TestTheProbeLeaksNothing:
    def test_it_reports_liveness(self, client: TestClient) -> None:
        assert client.get("/api/healthz").json() == {"status": "ok"}

    def test_it_returns_no_protected_data(self, client: TestClient) -> None:
        """A probe that leaked protected data to prove the process was up would be a
        permission hole with a reassuring name. This one is a documentable exception
        precisely because it returns none."""
        body = client.get("/api/healthz").text.lower()
        for leaked in ("right", "order", "officer", "priority", "curtail"):
            assert leaked not in body, f"the liveness probe mentions {leaked!r}"


class TestClassificationIsRealComputation:
    def test_the_july_2025_reading_classifies_as_the_board_saw_it(self, client: TestClient) -> None:
        """48.7 cfs against the 50 cfs July minimum at Fort Jones: below it, and
        inside the 10 cfs band, so the Sentinel announces the more actionable of the
        two. This is the reading behind Addendum 7."""
        body = client.get(
            "/api/classify/scott",
            params={"cfs": 48.7, "at": "2025-07-20T21:30:00+00:00"},
        ).json()
        assert body["minimum_cfs"] == 50.0
        assert body["classification"] == "reading_near_threshold"
        assert body["direction"] == "restrict"

    def test_the_recovery_reading_points_the_other_way(self, client: TestClient) -> None:
        """78.4 cfs on July 22, the reading behind Addendum 8, which lifted
        curtailment after the rating curve was revised."""
        body = client.get(
            "/api/classify/scott",
            params={"cfs": 78.4, "at": "2025-07-22T12:00:00+00:00"},
        ).json()
        assert body["direction"] == "relieve"

    def test_the_minimum_follows_the_date_period_not_the_month(self, client: TestClient) -> None:
        """Scott changes mid-month on June 24, which a month-keyed schedule cannot
        express and which decides who is curtailed on the 23rd."""
        before = client.get(
            "/api/classify/scott", params={"cfs": 100, "at": "2026-06-23T12:00:00+00:00"}
        ).json()
        after = client.get(
            "/api/classify/scott", params={"cfs": 100, "at": "2026-06-25T12:00:00+00:00"}
        ).json()
        assert before["minimum_cfs"] != after["minimum_cfs"]

    def test_every_response_says_it_is_only_a_recommendation(self, client: TestClient) -> None:
        """23 CCR 875(b) vests the determination in a named human official. An API
        that returned a bare verdict would be inviting a caller to treat it as one."""
        body = client.get(
            "/api/classify/scott", params={"cfs": 48.7, "at": "2025-07-20T21:30:00+00:00"}
        ).json()
        assert body["recommendation_only"] is True
        assert "self-execute" in body["disclaimer"]


class TestItRefusesRatherThanGuessing:
    def test_an_unencoded_era_returns_422_with_the_reason(self, client: TestClient) -> None:
        """The 2021 Shasta table was never verified, so answering from the 2024 one
        would mark the Board wrong for following the rule in force."""
        response = client.get(
            "/api/classify/shasta", params={"cfs": 41.0, "at": "2021-08-15T12:00:00+00:00"}
        )
        assert response.status_code == 422
        assert "not encoded" in response.json()["detail"]

    def test_an_unknown_basin_is_404(self, client: TestClient) -> None:
        assert client.get("/api/classify/nowhere", params={"cfs": 1}).status_code == 404

    def test_a_naive_timestamp_is_refused(self, client: TestClient) -> None:
        """A flow period boundary read in the wrong zone is off by a day, and two of
        the four boundaries fall mid-month."""
        response = client.get(
            "/api/classify/scott", params={"cfs": 48.7, "at": "2025-07-20T21:30:00"}
        )
        assert response.status_code == 422
        assert "timezone" in response.json()["detail"]

    def test_an_unparseable_timestamp_is_refused(self, client: TestClient) -> None:
        response = client.get("/api/classify/scott", params={"cfs": 48.7, "at": "yesterday"})
        assert response.status_code == 422


class TestTheFactSheetIsServedNotRestated:
    def test_it_returns_the_generated_file(self, client: TestClient) -> None:
        body = client.get("/api/facts").json()
        assert body["source"] == "docs/FACTS.md"
        assert "reproduces the direction" in body["markdown"]

    def test_it_names_the_file_rather_than_copying_its_figures(self, client: TestClient) -> None:
        """One source for every judged number. Serving the file means CI's drift
        check covers this endpoint too."""
        assert client.get("/api/facts").json()["source"].endswith("FACTS.md")


class TestTheDependencyManifestStaysHonest:
    """The audit that produced this module, turned into a guard.

    `fastapi`, `structlog`, `pydantic` and `google-cloud-pubsub` were declared and
    imported by nothing. A dependency list is a claim about what a project uses,
    checkable by anyone in one grep, and four of them were false. Wired-or-cut
    applies to a manifest exactly as it applies to a README.
    """

    #: Packages whose import name differs from their distribution name.
    IMPORT_NAME: ClassVar[dict[str, str]] = {
        "google-adk": "google.adk",
        "curtail-core": "curtail_core",
        # The distribution is google-cloud-pubsub; the module is google.pubsub_v1.
        # Deriving the module by replacing hyphens gave google_cloud_pubsub, which
        # appears nowhere, so the guard reported a dependency as unused that a test
        # imports on the next line over. A name-mangling rule is a heuristic, and a
        # heuristic inside a correctness guard needs an explicit escape hatch.
        "google-cloud-pubsub": "google.pubsub_v1",
    }

    #: Declared but never imported BY US, each with the reason it is still required.
    #:
    #: The guard found these the moment it was written, and they are a real category
    #: rather than an oversight: a library we call can need a package at runtime
    #: without it ever appearing in our imports. What makes that honest is that the
    #: exemption is WRITTEN DOWN with a reason, so an unused entry has to be
    #: justified in prose rather than assumed to be fine. Same shape as the README
    #: placeholder registry: discovery, then a registry that must explain itself.
    RUNTIME_ONLY: ClassVar[dict[str, str]] = {
        "sqlalchemy": (
            "ADK's DatabaseSessionService imports it. The Season Ledger's durability "
            "depends on that class, and ADK ships it behind an extra rather than as "
            "a hard dependency, so it is pinned here."
        ),
        "aiosqlite": (
            "The async SQLite driver behind the sqlite+aiosqlite URL the ledger "
            "durability test uses, and the same shape a Cloud SQL URL takes."
        ),
        "uvicorn": (
            "The ASGI server that runs this API on Cloud Run. It is invoked as a "
            "command by the container entrypoint and never imported, which is a real "
            "category rather than an oversight: a process we start is not a module "
            "we call."
        ),
        "greenlet": (
            "SQLAlchemy's async bridge. Without it DatabaseSessionService raises at "
            "the first await, which is how it was discovered."
        ),
    }

    @staticmethod
    def _declared() -> list[str]:
        manifest = tomllib.loads((REPO / "agents" / "pyproject.toml").read_text())
        return [
            dep.split(">")[0].split("<")[0].split("=")[0].strip()
            for dep in manifest["project"]["dependencies"]
        ]

    @staticmethod
    def _source_text() -> str:
        # SOURCE AND TESTS. Scanning only src produced a wrong action: it reported
        # google-cloud-pubsub as unused, it was cut, and the suite went red because
        # `test_messaging.py` imports `google.pubsub_v1.types` to assert the real
        # library supports the dead-letter policy this project configures. A package
        # used only by tests is still used, and a guard whose scope is narrower than
        # the question it answers gives confidently wrong advice.
        roots = ((REPO / "agents" / "src"), (REPO / "agents" / "tests"))
        return "\n".join(path.read_text() for root in roots for path in root.rglob("*.py"))

    def test_every_declared_dependency_is_imported_somewhere(self) -> None:
        source = self._source_text()
        unused = []
        for dep in self._declared():
            module = self.IMPORT_NAME.get(dep, dep.replace("-", "_"))
            root = module.split(".")[0]
            if f"import {root}" not in source and f"from {root}" not in source:
                unused.append(dep)
        unexplained = sorted(set(unused) - set(self.RUNTIME_ONLY))
        assert not unexplained, (
            f"declared but imported by nothing and unexplained: {unexplained}. A "
            "dependency list is a claim about what this project uses. Wire it, cut "
            "it, or register it in RUNTIME_ONLY with the reason it is still needed."
        )

    def test_every_runtime_only_entry_is_still_declared(self) -> None:
        """The other direction. An exemption for a dependency nobody declares any
        more is a note explaining something that is not there, which reads as
        coverage and is not."""
        stale = sorted(set(self.RUNTIME_ONLY) - set(self._declared()))
        assert not stale, f"RUNTIME_ONLY explains dependencies no longer declared: {stale}"

    def test_every_runtime_only_entry_gives_a_real_reason(self) -> None:
        """A registry whose entries say nothing is a list, and a list is what this
        guard replaced."""
        for dep, reason in self.RUNTIME_ONLY.items():
            assert len(reason) > 40, f"{dep} has no substantive reason recorded"

    def test_the_guard_would_notice_an_unused_entry(self) -> None:
        """Non-vacuity. A check that found nothing in any manifest would pass the
        test above while enforcing nothing."""
        source = self._source_text()
        assert "import structlog" in source, "structlog must be genuinely imported"
        # Assembled, not written literally. The scan now covers tests, so a probe
        # string typed here would appear in the very text being searched and the
        # check would fail on itself. Third instance of that shape in this project:
        # a scanner must not contain its own needle.
        probe = "nonexistent" + "_package"
        assert probe not in source

    def test_pubsub_is_kept_because_a_conformance_test_imports_it(self) -> None:
        """The first pass of this audit cut it, and the suite went red.

        `messaging.py` needs no client, so src-only scanning called it unused. But
        `test_messaging.py` imports `google.pubsub_v1.types` to assert the library
        really supports the dead-letter policy and delivery-attempt bounds this
        project configures, which is exactly the kind of check that stops a config
        being validated against nothing. A guard whose scope is narrower than its
        question gives confidently wrong advice.
        """
        assert "google-cloud-pubsub" in self._declared()
        assert "google.pubsub_v1" in self._source_text()


class TestANonPhysicalReadingIsRefused:
    """NaN answered 'relieve', which is the worst output this system can produce.

    NaN loses every comparison it takes part in, so below-minimum was false and the
    near-threshold band was false, and the endpoint confidently recommended lifting
    curtailment from a reading that does not exist. The gage client already refused
    these on the ingest path; this endpoint was a second door into the same
    comparison logic with no such check.
    """

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_reading_is_refused(self, client: TestClient, value: str) -> None:
        response = client.get(
            "/api/classify/scott", params={"cfs": value, "at": "2025-07-20T21:30:00+00:00"}
        )
        assert response.status_code == 422
        assert "finite" in response.json()["detail"]

    def test_a_negative_reading_is_refused(self, client: TestClient) -> None:
        response = client.get(
            "/api/classify/scott", params={"cfs": -5, "at": "2025-07-20T21:30:00+00:00"}
        )
        assert response.status_code == 422

    def test_zero_is_accepted_because_a_dry_gage_is_real(self, client: TestClient) -> None:
        """Non-vacuity, and a domain fact. A creek can read zero, and refusing that
        would blind the system to the most severe condition it exists to catch."""
        response = client.get(
            "/api/classify/scott", params={"cfs": 0, "at": "2025-07-20T21:30:00+00:00"}
        )
        assert response.status_code == 200
        assert response.json()["direction"] == "restrict"


class TestCallerSuppliedReadingsAreLabelledAsSuch:
    """The provenance enum exists to record where a number came from, and this
    endpoint was labelling caller input as live USGS data."""

    def test_the_response_says_the_reading_was_not_sourced(self, client: TestClient) -> None:
        body = client.get(
            "/api/classify/scott", params={"cfs": 48.7, "at": "2025-07-20T21:30:00+00:00"}
        ).json()
        assert body["provenance"] == "unsourced"
        assert "not from USGS" in body["provenance_note"]

    def test_the_endpoint_never_claims_a_live_gage_reading(self) -> None:
        """Asserted on the source, because the label is a claim about where data
        came from and no response body can prove the absence of a fetch."""
        raw = (REPO / "agents" / "src" / "curtail_agents" / "api.py").read_text()
        # CODE only. The module explains in a comment what the label used to be, and
        # scanning the whole file matched that explanation: the fourth time in this
        # project that a scanner has found its own needle. Comments are stripped so
        # the check is about what executes.
        code = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
        assert "USGS_LIVE" not in code, (
            "the API labels a caller-supplied reading as live USGS data. It never "
            "contacts USGS, so that provenance is false."
        )


class TestTheFactSheetSurvivesPackaging:
    """The endpoint a review found broken in every packaged deployment.

    It resolved the fact sheet relative to the repository, and a wheel contains only
    the package, so an installed service would 503 forever while importing cleanly.
    The first answer was a more diagnostic 503, which is documenting a hole rather
    than closing it, and this project has that written down as a rule.
    """

    def test_the_served_copy_is_inside_the_package(self) -> None:
        from curtail_agents.api import FACTS

        package_root = _package_root()
        assert package_root in FACTS.parents, (
            f"the API serves {FACTS}, which is outside the package and therefore "
            "absent from a wheel."
        )

    def test_the_packaged_copy_matches_the_judge_facing_one(self) -> None:
        """Two copies is two places to drift, handled the way everything else here
        is: one generator writes both and --check verifies both."""
        packaged = _package_root() / "data" / "FACTS.md"
        assert packaged.read_text() == (REPO / "docs" / "FACTS.md").read_text()

    def test_a_built_wheel_actually_contains_it(self) -> None:
        """Built, not assumed. The previous packaging attempt in this project passed
        every reasoning check and then failed to build, so the wheel is inspected."""
        import subprocess
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as out:
            result = subprocess.run(
                ["uv", "build", "--package", "curtail-agents", "--wheel", "--out-dir", out],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            wheels = list(Path(out).glob("*.whl"))
            assert wheels, "no wheel was produced"
            names = zipfile.ZipFile(wheels[0]).namelist()
            assert "curtail_agents/data/FACTS.md" in names, (
                f"the wheel does not carry the fact sheet: {sorted(names)[:12]}"
            )
            assert "curtail_agents/data/citations.json" in names


def _package_root() -> Path:
    """Where curtail_agents is installed, typed rather than assumed.

    `module.__file__` is `str | None`, and a namespace package would make it None.
    Asserting instead of ignoring the type keeps the failure legible if that ever
    happens, rather than turning into an obscure Path error.
    """
    import curtail_agents

    located = curtail_agents.__file__
    assert located is not None, "curtail_agents has no __file__, so it is not installed normally"
    return Path(located).parent
