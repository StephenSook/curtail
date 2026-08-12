"""The config template and the code must agree, in both directions.

A previous cycle shipped a tracked `.env.example` advertising three variables nothing
read and omitting the one the running service actually needed, while the README beside it
had already been cleaned. Config templates are where plan-tier claims survive after the
prose has been fixed, because nobody re-reads them.

This is the guard that would have caught it. `CURTAIL_SIGNING_KEY` was added to the code
and not to the template, and nothing failed until it was written.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / ".env.example"
SOURCE_DIRS = (REPO / "core" / "src", REPO / "agents" / "src", REPO / "scripts")

#: The marker separating wired configuration from the architecture's planned seams.
PLANNED_MARKER = "PLANNED, NOT WIRED."

#: Variables the template documents for a HUMAN operator or a deploy step rather than for
#: `os.environ` in this repository. Each one is listed with why, so the exemption cannot
#: quietly become a place to hide drift.
DOCUMENTED_ELSEWHERE = {
    # Read by the Google SDKs themselves, not by this code.
    "GOOGLE_CLOUD_PROJECT": "read by google-genai and gcloud, not by our modules",
    "GOOGLE_CLOUD_REGION": "deploy-time value for gcloud run deploy",
    "GOOGLE_CLOUD_LOCATION": "read by the ADK and Vertex clients",
    "GOOGLE_GENAI_USE_VERTEXAI": "read by google-genai",
    "GEMINI_API_KEY": "read by google-genai when not using Vertex",
    "MODEL_SENTINEL": "reserved for the Sentinel's model, not yet wired",
    "MODEL_HERALD": "reserved for the Herald's model, not yet wired",
    "MODEL_NORMALIZER": "reserved for the Gemma normalizer, not yet wired",
    "MODEL_EMBEDDING": "reserved for semantic search, not yet wired",
    "AGENT_REGISTRY_ENDPOINT": "governance wiring, blank runs the documented Plan B2",
    "AGENT_GATEWAY_ENDPOINT": "governance wiring, blank runs the documented Plan B2",
    "MODEL_ARMOR_TEMPLATE": "governance wiring, applied at deploy",
}


def _split() -> tuple[str, str]:
    text = TEMPLATE.read_text()
    if PLANNED_MARKER not in text:
        return text, ""
    wired, planned = text.split(PLANNED_MARKER, 1)
    return wired, planned


def declared(section: str | None = None) -> set[str]:
    body = section if section is not None else TEMPLATE.read_text()
    return {
        match.group(1)
        for line in body.splitlines()
        if (match := re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip()))
    }


def referenced() -> set[str]:
    """Every env-var name the source mentions, including through a constant.

    Two wrong versions preceded this one, in opposite directions, and the pair is the
    lesson. Matching only `environ("LITERAL")` reported CURTAIL_SIGNING_KEY as unread,
    because the code holds the name in `SIGNING_KEY_ENV` and passes the constant.
    Widening to "any uppercase string literal" then swept in every enum value in the
    codebase, so the other direction of the check exploded. A guard that cries wolf and a
    guard that sleeps are both unread.

    So it resolves ONE hop of indirection and nothing more: an environ call takes either
    a literal or a module-level constant whose value is a literal.
    """
    names: set[str] = set()
    call = re.compile(r"""(?:environ(?:\.get)?|getenv)\(\s*(["']?)([A-Za-z_][A-Za-z0-9_]*)\1""")
    for root in SOURCE_DIRS:
        for path in root.rglob("*.py"):
            body = path.read_text()
            constants = dict(
                re.findall(
                    r"""^([A-Z][A-Z0-9_]*)\s*=\s*["']([A-Z][A-Z0-9_]*)["']""", body, re.MULTILINE
                )
            )
            for match in call.finditer(body):
                quoted, token = match.group(1), match.group(2)
                resolved = token if quoted else constants.get(token, "")
                if resolved:
                    names.add(resolved)
    return names


class TestTheTemplateAndTheCodeAgree:
    def test_it_is_not_vacuous(self) -> None:
        assert declared(), "no variables parsed from the template"
        assert referenced(), "no environment reads found in the source"

    def test_every_variable_the_code_reads_is_documented(self) -> None:
        """The direction that bites an operator: the service needs something the
        template never mentions, so a correct deployment is impossible to derive."""
        missing = referenced() - declared()
        assert not missing, (
            f"{sorted(missing)} are read by the code and absent from .env.example, so a "
            "stranger following the template cannot run this."
        )

    def test_every_variable_above_the_line_is_read_or_explained(self) -> None:
        """The direction that misleads a judge: the template advertises a capability
        nothing implements.

        This found 24 of them on its first run, including a database, a message bus and
        push notification keys. They were not deleted, because the architecture is real;
        they were moved below an explicit PLANNED, NOT WIRED marker so a reader diffing
        this file against the README cannot mistake a seam for a feature.
        """
        wired, _ = _split()
        unread = declared(wired) - referenced() - set(DOCUMENTED_ELSEWHERE)
        assert not unread, (
            f"{sorted(unread)} appear above the '{PLANNED_MARKER}' line and nothing "
            "reads them. Wire them, move them below the line, or add them to "
            "DOCUMENTED_ELSEWHERE with a reason."
        )

    def test_the_planned_section_holds_only_unwired_values(self) -> None:
        """The other half of the split, and the one that goes stale silently. A variable
        that gets wired and left below the line understates the project to a judge, which
        this repository has already done once in a README."""
        _, planned = _split()
        wired_but_filed_as_planned = declared(planned) & referenced()
        assert not wired_but_filed_as_planned, (
            f"{sorted(wired_but_filed_as_planned)} are filed as PLANNED and the code "
            "reads them. Move them above the line."
        )

    def test_the_planned_section_exists_and_is_not_empty(self) -> None:
        """Non-vacuity for both checks above: with no marker, `_split` returns the whole
        file as wired and the planned check passes against nothing."""
        _, planned = _split()
        assert declared(planned), "the PLANNED section is empty or missing"

    def test_the_signing_key_carries_no_usable_default(self) -> None:
        """A committed key would let any deployment that forgot to configure one mint
        signatures with a published value."""
        for line in TEMPLATE.read_text().splitlines():
            if line.startswith("CURTAIL_SIGNING_KEY="):
                value = line.split("=", 1)[1]
                assert "generate" in value.lower(), (
                    f"the template ships a signing key value: {value!r}"
                )
                return
        pytest.fail("CURTAIL_SIGNING_KEY is not in .env.example")
