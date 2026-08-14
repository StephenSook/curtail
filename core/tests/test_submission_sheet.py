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

import pytest

REPO = Path(__file__).resolve().parents[2]
SHEET = REPO / "docs" / "SUBMISSION.md"
SOURCE_DIRS = (REPO / "agents" / "src", REPO / "core" / "src")

#: A service the form offers, and what would have to be in the source to tick it.
#: Deliberately a SECOND copy of the generator's table: if both are edited in step the
#: claim is at least stated twice, and if only one is edited these tests fail. A single
#: shared constant would let a wrong edit propagate silently to the check that guards it.
EVIDENCE: dict[str, tuple[str, ...]] = {
    "Cloud SQL": ("cloudsql", "cloud_sql"),
    "Firestore": ("google.cloud.firestore",),
    "Google Kubernetes (GKE)": ("container_v1",),
    "Pub/Sub": ("pubsub_v1.PublisherClient", "pubsub_v1.SubscriberClient"),
}


@pytest.fixture(scope="module")
def sheet() -> str:
    assert SHEET.exists(), "docs/SUBMISSION.md is missing. Run `make submission`."
    return SHEET.read_text()


@pytest.fixture(scope="module")
def source() -> str:
    parts: list[str] = []
    for directory in SOURCE_DIRS:
        for path in directory.rglob("*.py"):
            if "__pycache__" not in path.parts:
                parts.append(path.read_text())
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
        assert any(marker in source for marker in EVIDENCE[service]), (
            f"the sheet ticks {service} and no shipped source matches "
            f"{EVIDENCE[service]}. A dropdown is a claim about the stack."
        )

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
