"""The architecture diagram is a claim about the system, so it is checked like one.

Devpost requires an architecture diagram as a file upload, and a judge reading it has
the repository open beside them. A diagram naming a service the code never calls is the
same defect as a README doing it, except harder to notice, because a picture is not
something anyone thinks to grep.

**This exists because the first draft of that diagram was wrong.** It drew an arrow from
USGS Water Data to the Gage Sentinel labelled "OGC API". The client is real and tested,
but nothing on a served path calls it: every reading is caller-supplied and the response
stamps it UNSOURCED. The arrow was removed before the file was committed, and this is
what stops the next one being drawn.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DIAGRAM = REPO / "docs" / "architecture.html"
PNG = REPO / "docs" / "architecture.png"
SOURCE_DIRS = (REPO / "agents" / "src", REPO / "core" / "src")

#: Below this the render is a blank page rather than a diagram. Mirrors
#: `scripts/render_architecture.py`, which refuses to write one that small.
MIN_PNG_BYTES = 60_000

#: A named Google Cloud service, and the import or endpoint that would prove we use it.
#: Every entry is a service the submission form offers, plus the ones most likely to be
#: drawn out of habit. The diagram may name one ONLY if its evidence is in the source.
SERVICE_EVIDENCE: dict[str, tuple[str, ...]] = {
    "Pub/Sub": ("pubsub_v1", "from google.cloud import pubsub"),
    "Cloud SQL": ("cloudsql", "cloud_sql"),
    "Firestore": ("firestore",),
    "Kubernetes": ("kubernetes",),
    "GKE": ("container_v1",),
    "BigQuery": ("bigquery",),
    "Memorystore": ("memorystore",),
    "Cloud Storage": ("storage.Client", "from google.cloud import storage"),
    "Secret Manager": ("secretmanager",),
    "Cloud Tasks": ("tasks_v2",),
    "Cloud Scheduler": ("scheduler_v1",),
    "Agent Engine": ("agent_engines", "reasoning_engines"),
}


def _diagram_text() -> str:
    assert DIAGRAM.exists(), f"the diagram source is missing: {DIAGRAM}"
    return DIAGRAM.read_text()


def _source_text() -> str:
    parts: list[str] = []
    for directory in SOURCE_DIRS:
        for path in directory.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            parts.append(path.read_text())
    return "\n".join(parts)


class TestTheDiagramNamesOnlyWhatTheCodeUses:
    def test_it_names_no_google_cloud_service_the_source_never_calls(self) -> None:
        """The expensive mistake here is drawing the intended architecture.

        Pub/Sub is the live example: `messaging.py` implements the ordering-key,
        dead-letter and dedup discipline against the real library's types, the package
        is a declared dependency, and it is genuinely good work. Nothing publishes to a
        topic. Drawing a Pub/Sub box would be true of the design and false of the system.
        """
        diagram, source = _diagram_text(), _source_text()
        claimed = [name for name in SERVICE_EVIDENCE if name in diagram]
        unsupported = [
            name for name in claimed if not any(ev in source for ev in SERVICE_EVIDENCE[name])
        ]
        assert not unsupported, (
            f"the diagram names {unsupported}, and no shipped source imports or calls "
            "them. Either wire the service or remove it from the picture."
        )

    def test_every_model_it_names_appears_in_the_source(self) -> None:
        """A model id is the single most checkable claim on the page."""
        diagram, source = _diagram_text(), _source_text()
        named = set(re.findall(r"\b(gemini-[0-9.]+-[a-z-]+|gemma-[0-9a-z.-]+)\b", diagram))
        assert named, "the diagram names no model at all, which cannot be right"
        missing = sorted(m for m in named if m not in source)
        assert not missing, (
            f"the diagram names {missing}, which appears in no shipped source file. "
            "This is the wired-or-cut rule applied to a picture."
        )

    def test_the_agents_it_draws_are_the_graph_s_own_nodes(self) -> None:
        """Four boxes, four node names. A fifth agent on the page would be fiction."""
        diagram = _diagram_text()
        fleet = (REPO / "agents" / "src" / "curtail_agents" / "fleet.py").read_text()
        for title, constant in (
            ("Gage Sentinel", '"gage_sentinel"'),
            ("Allocation Core", '"allocation_core"'),
            ("Order Scribe", '"order_scribe"'),
            ("Herald", '"herald"'),
        ):
            assert title in diagram, f"the diagram does not draw {title}"
            assert constant in fleet, f"{constant} is not a node name in fleet.py"

    @pytest.mark.parametrize(
        "absent",
        [
            # Real code, deliberately NOT drawn as part of the served system. Each was
            # considered and rejected: drawing them would overstate what runs.
            "Season Ledger is wired",
            "USGS Water Data",
            "fetches the gage",
        ],
    )
    def test_it_does_not_claim_the_unwired_parts_are_wired(self, absent: str) -> None:
        assert absent not in _diagram_text(), (
            f"the diagram claims {absent!r}. The Season Ledger and the USGS client are "
            "implemented and tested, but no served request path reaches either."
        )

    def test_it_states_the_missing_persistence_rather_than_omitting_it(self) -> None:
        """A diagram that quietly leaves out what a system lacks is a claim too.

        This asserts the honest label is PRESENT, which is the opposite direction from
        every other check here, and it is the one most likely to be silently dropped
        while tidying the picture up before a deadline.
        """
        diagram = _diagram_text()
        assert "No persistent store" in diagram
        assert "Nothing survives a request" in diagram


class TestTheRenderedArtifactExists:
    def test_the_png_the_submission_uploads_is_present_and_not_blank(self) -> None:
        assert PNG.exists(), (
            "docs/architecture.png is missing. Devpost field 28092 is a REQUIRED file "
            "upload; regenerate with `make diagram`."
        )
        size = PNG.stat().st_size
        assert size >= MIN_PNG_BYTES, (
            f"the rendered diagram is {size} bytes, which is a blank page rather than a "
            f"diagram. Expected at least {MIN_PNG_BYTES}."
        )
