"""Application-side defence against indirect prompt injection in order text.

**Why this is not Model Armor's job alone.** Google's own guidance is to filter and
validate external input in application code before it enters a prompt, and never to
rely on a single defensive measure. Model Armor's injection check has historically
looked at a 512 token window, scans only the first 40 URLs, is text only so images
inside a document are not screened at all, and it is a network service that can be
unavailable exactly when a poisoned document arrives. A layer we own runs first,
runs deterministically, and runs offline.

**What the untrusted input actually is.** Order PDFs are fetched from a government
web server, put through OCR, and their text is handed to the Order Scribe to draft
from. That is the textbook OWASP LLM01 indirect path: the attacker does not talk to
the model, the document does. A PDF that reaches this corpus with an embedded
instruction is not hypothetical for a system whose whole premise is ingesting other
people's documents.

**The design decision that matters: neutralise and REPORT, never delete.**

A sanitizer that silently removes text is worse than the attack it prevents. These
are legal documents, and a finding quietly dropped from an order is a curtailment
that reaches the wrong set of rights, which is the exact failure Order WR
2026-0005-DWR was issued to correct. So injected spans are ANNOTATED in place, the
character count is preserved as evidence, and every hit is reported to the caller. A
document trying to steer the drafter is itself something a watermaster must see, not
something to clean up quietly and forget.

**And the risk that the guard is the problem.** Real orders contain imperative legal
prose. "This Order supersedes the above." "Disregard prior addenda." A pattern set
tuned only against attacks will maul that text and nobody will notice, because the
output still reads like an order. Every pattern here therefore requires a
model-directed subject, and the tests assert that genuine regulatory sentences pass
through byte for byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

#: Wrapper marking untrusted text inside a prompt.
#:
#: The fence is not decoration. Without a delimiter the model has no way to tell the
#: instructions it was given from the document it was handed, and prompt-injection
#: works precisely by erasing that distinction. The token is unusual on purpose: a
#: fence a document can guess is a fence a document can close.
FENCE_OPEN = "<<<CURTAIL_UNTRUSTED_DOCUMENT>>>"
FENCE_CLOSE = "<<<END_CURTAIL_UNTRUSTED_DOCUMENT>>>"

#: What replaces a neutralised span.
#:
#: It does NOT preserve the character count, and an earlier version of this comment
#: claimed it did. That claim was false on its face, since the marker is a fixed
#: string and matches are not, and a review caught it. A false claim beside a guard
#: is the drift this whole project audits for, so it is corrected here rather than
#: quietly dropped: sanitized text is a DIFFERENT LENGTH from its input, and the
#: offsets on each hit index the ORIGINAL document, never the returned text.
NEUTRALISED = "[NEUTRALISED:{kind}]"


class InjectionKind(StrEnum):
    """What sort of steering the document attempted.

    Named rather than counted, because "3 injection attempts" tells a watermaster
    nothing and "this document tried to redefine your role" tells them they are
    looking at a deliberate attack rather than OCR noise.
    """

    INSTRUCTION_OVERRIDE = "instruction_override"
    ROLE_REDEFINITION = "role_redefinition"
    SYSTEM_IMPERSONATION = "system_impersonation"
    FENCE_ESCAPE = "fence_escape"
    EXFILTRATION = "exfiltration"


@dataclass(frozen=True, slots=True)
class InjectionHit:
    """One neutralised span, with enough detail to investigate the document.

    **`source_start` and `source_end` index the ORIGINAL document, not the sanitized
    text, and the names say so because an earlier pair called `start` and `end` did
    not.** Replacing a span with a fixed-length marker shifts every offset after it,
    so a caller highlighting `SanitizedText.text` at those positions would frame the
    wrong sentence. In a legal document that is not a cosmetic bug: the point of
    reporting a hit is to let a watermaster read what the document actually tried to
    do, and pointing at neighbouring text invents an accusation.

    Offsets into the source are also the more useful of the two, since investigating
    an injection means opening the original PDF.
    """

    kind: InjectionKind
    matched: str
    source_start: int
    source_end: int


@dataclass(frozen=True, slots=True)
class SanitizedText:
    """Text safe to place in a prompt, plus what had to be done to make it so."""

    text: str
    hits: tuple[InjectionHit, ...] = field(default_factory=tuple)

    @property
    def is_clean(self) -> bool:
        return not self.hits

    def fenced(self) -> str:
        """The form that may be interpolated into a prompt, and the only one.

        A caller holding `.text` could paste it straight into a template and lose
        the delimiter that makes the untrusted span identifiable. Handing back the
        fenced string from a named method makes the safe path the short one.
        """
        return f"{FENCE_OPEN}\n{self.text}\n{FENCE_CLOSE}"


#: Patterns, each requiring a MODEL-DIRECTED subject.
#:
#: "Disregard prior addenda" is ordinary order prose and must survive. "Disregard
#: your prior instructions" is an attack. The difference is the object, so every
#: pattern below names one: instructions, prompt, rules, the assistant itself. A
#: pattern matching a bare imperative would fail closed on real legal text, and
#: failing closed on the corpus is not safety, it is a broken ingester.
_PATTERNS: tuple[tuple[InjectionKind, re.Pattern[str]], ...] = (
    (
        InjectionKind.INSTRUCTION_OVERRIDE,
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}?"
            r"\b(?:previous|prior|above|earlier|all|any)?\s*"
            r"(?:instruction|instructions|prompt|prompts|rule|rules|direction|directions|"
            r"guideline|guidelines|constraint|constraints)\b",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.ROLE_REDEFINITION,
        re.compile(
            r"\byou\s+are\s+(?:now\s+)?(?:a|an|the)\b[^.\n]{0,60}"
            r"|\bact\s+as\s+(?:a|an|the)\b[^.\n]{0,60}"
            r"|\bfrom\s+now\s+on[,\s]+you\b[^.\n]{0,60}",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.SYSTEM_IMPERSONATION,
        re.compile(
            r"^\s*(?:system|assistant|developer)\s*:"
            r"|\[\s*(?:system|assistant|developer)\s*\]"
            r"|<\s*/?\s*(?:system|assistant|im_start|im_end)\s*>",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        InjectionKind.EXFILTRATION,
        re.compile(
            r"\b(?:send|post|transmit|email|upload|exfiltrate)\b[^.\n]{0,40}?"
            r"\b(?:to\s+https?://|to\s+[\w.-]+@[\w.-]+|your\s+(?:system\s+)?prompt|"
            r"the\s+(?:system\s+)?prompt|api[_\s]?key|credential|credentials|secret|secrets)\b",
            re.IGNORECASE,
        ),
    ),
)


def _fence_escape_pattern() -> re.Pattern[str]:
    """Any literal use of our own delimiters inside the document.

    Derived from the fence constants rather than written out again, so changing the
    fence cannot leave a stale pattern that guards a delimiter no longer in use.
    """
    return re.compile(
        "|".join(re.escape(token) for token in (FENCE_OPEN, FENCE_CLOSE)),
        re.IGNORECASE,
    )


def _normalise_whitespace(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to single spaces, keeping a map back to the source.

    **This is the fix for the evasion that mattered most.** The patterns bound their
    gaps with `[^.\\n]`, so they could not cross a line break, and a review pointed
    out that OCR line breaks are the NORMAL condition for this input rather than an
    exotic encoding: every document here is a scanned PDF put through OCR. A probe
    confirmed it. `Ignore your\\nprevious instructions` was reported clean, which is
    the single highest-severity pattern defeated by pressing Enter.

    Widening the patterns to `[^.]` instead would let a match run across paragraphs
    and start mauling legal prose, trading a false negative for a false positive in a
    document where both are expensive. Normalising once and mapping offsets back
    keeps the patterns tight and the reporting exact.

    Returns the normalised text and, for each of its characters, the index of the
    source character it came from, so a span found in the normalised copy can be
    reported and replaced in the ORIGINAL.
    """
    out: list[str] = []
    index_map: list[int] = []
    in_whitespace = False
    for position, char in enumerate(text):
        if char.isspace():
            if not in_whitespace:
                out.append(" ")
                index_map.append(position)
                in_whitespace = True
            continue
        in_whitespace = False
        out.append(char)
        index_map.append(position)
    # A trailing sentinel so a span ending at the very end maps to len(text).
    index_map.append(len(text))
    return "".join(out), index_map


def sanitize_document(text: str) -> SanitizedText:
    """Neutralise model-directed instructions in untrusted document text.

    Returns the annotated text and every hit. Deterministic and offline: no model
    call, no network, so this layer cannot be unavailable at the moment a poisoned
    document arrives, which is a property Model Armor cannot offer.

    Matching runs against a whitespace-normalised copy so an OCR line break cannot
    hide an instruction, and every span is translated back to source offsets before
    anything is replaced. Replacement then runs right to left over the ORIGINAL text
    so earlier offsets stay valid, the same reason the citation scrubber in
    `routing.py` works that way.
    """
    normalised, index_map = _normalise_whitespace(text)
    matches: list[InjectionHit] = []
    patterns = ((InjectionKind.FENCE_ESCAPE, _fence_escape_pattern()), *_PATTERNS)

    for kind, pattern in patterns:
        for found in pattern.finditer(normalised):
            if found.start() == found.end():
                # A zero-width match would replace nothing while reporting a hit,
                # which is a guard that reports success without acting.
                continue
            source_start = index_map[found.start()]
            source_end = index_map[found.end()]
            matches.append(
                InjectionHit(
                    kind=kind,
                    matched=text[source_start:source_end],
                    source_start=source_start,
                    source_end=source_end,
                )
            )

    # Overlapping hits from different patterns would corrupt the text if applied
    # naively, so the longest match at each position wins and the rest are dropped.
    matches.sort(key=lambda hit: (hit.source_start, -(hit.source_end - hit.source_start)))
    kept: list[InjectionHit] = []
    covered_to = -1
    for hit in matches:
        if hit.source_start >= covered_to:
            kept.append(hit)
            covered_to = hit.source_end

    out = text
    for hit in reversed(kept):
        marker = NEUTRALISED.format(kind=hit.kind.value)
        out = out[: hit.source_start] + marker + out[hit.source_end :]

    return SanitizedText(text=out, hits=tuple(kept))
