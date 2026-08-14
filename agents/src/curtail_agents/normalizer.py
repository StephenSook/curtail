"""The Gemma Normalizer: open weights, on the operator's own hardware.

**What it is for.** The Board publishes curtailment orders and addenda as PDFs whose
layout drifts between issuances. The deterministic parsers in `scripts/` read most of
them, and a real fraction of the corpus does not yield to a parser at all, because the
tables are ragged, the OCR layer is imperfect, or the sentence carrying the operative
number is prose rather than a cell. Those are the documents this module is for. A new
addendum appears and something has to turn it into the ingestion schema.

**Why an open-weights model specifically, and why local.** This is the data sovereignty
argument, and it is not decoration. A watermaster district is a public body handling
records about named landowners and their diversions. "Send the documents to a
third-party inference API" is a procurement conversation, sometimes a prohibition. Gemma
is open weights, so the same code runs against a model the agency hosts itself, on
hardware it controls, with no document leaving the building. This module talks to an
OpenAI-shaped local runtime rather than a managed endpoint for exactly that reason.

**The guard, and it is the whole point.** A language model reading a legal document will
produce a plausible number when it cannot find the real one, and a plausible order number
is far more dangerous than a missing one. So nothing this module returns is trusted
because the model said it:

    Every extracted value must appear VERBATIM in the source text, or it is
    reported as unverified and never as a value.

That makes fabrication structurally impossible for the fields that matter, rather than
discouraged by a prompt. It is the same rule this project already applies to quotes and
to citations, and it was learned the same way: a prohibition in a system prompt is not a
guardrail, and a composed sentence carrying correct-looking numbers is the hardest kind
of error to catch by reading.

The module never decides anything. It proposes a structured reading of a document, with
the parts it could not verify named, for a human or a deterministic parser to accept.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: Gemma 3 rather than Gemma 4: `gemma4` tags do not exist in the runtime's library as
#: of 2026-08-14, checked against the registry rather than assumed. The repository
#: previously reserved `gemma-4-26b-a4b-it` in its env template, which was aspirational.
DEFAULT_MODEL = "gemma3:4b"

#: The local runtime. Default is Ollama's. Anything speaking the same shape works,
#: which is the portability half of the sovereignty argument.
DEFAULT_HOST = "http://127.0.0.1:11434"

#: Long enough for a cold model load on a laptop, short enough to fail rather than hang.
TIMEOUT_SECONDS = 240.0

#: Documents longer than this are refused rather than truncated. A truncated legal
#: document still parses and produces a confident reading of the half that survived,
#: which is the worst available outcome. Same reasoning as the injection sanitizer's cap.
#:
#: Sized to the local context window rather than to the largest document: a published
#: addendum runs to roughly 60,000 characters, and almost all of that is the Attachment
#: A rights table. `split_narrative` routes that half to the deterministic parser that
#: already reads it well, so what arrives here is the operative narrative alone.
MAX_CHARS = 24_000

#: Where the narrative ends and the attachment begins. The attachment repeats a page
#: header on every page, and the enclosure line precedes it.
ATTACHMENT_BOUNDARY = re.compile(r"(?im)^\s*(enclosure\b|updated attachment to addendum)")


class NormalizerUnavailableError(RuntimeError):
    """The local model could not be reached.

    Deliberately distinct from "the model returned nothing useful". An unavailable
    layer is not a passing one, and a caller that cannot tell the two apart will
    eventually report an outage as a clean document.
    """


class NormalizerRefusedError(ValueError):
    """The input cannot be normalised, and saying so beats guessing."""


@runtime_checkable
class Generate(Protocol):
    """A single text-in, text-out call. Injected so tests never need a model."""

    def __call__(self, prompt: str, *, model: str) -> str: ...


#: The fields worth extracting, and the shape each must have to be believable. The
#: pattern is a SANITY check on shape; being present verbatim in the source is the
#: check that actually matters, and it is applied to every field regardless.
#: **Two fields were removed from this schema after the first live run, and the reason
#: is the design rule.** `minimum_cfs` and `observed_cfs` both looked obvious and both
#: are unanswerable from an order narrative. Addendum 6 states ELEVEN distinct cfs
#: figures and TWO observed readings, June 12 and June 15, so "the minimum" and "the
#: observed discharge" each have more than one correct answer in the text. Worse for
#: the minimum: `flow_minimums.py` already holds the operative schedule as code, so
#: asking a model for it creates a second source that can disagree with the first.
#: A field with no single right answer is a field that invites a confident wrong one.
FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "order_number": re.compile(r"^WR\s?\d{4}-\d{4}-[A-Z]{3,4}$"),
    "addendum_number": re.compile(r"^\d{1,2}$"),
}

#: **`priority_cutoff` was removed after two live runs disagreed, and that is the whole
#: argument for this module's scope.** Addendum 6 states a RANGE, "priority dates between
#: and including November 25, 1912 and December 31, 1957". Both endpoints are in the
#: document, so both pass a verbatim check, and the model returned a different one each
#: run. Neither answer is a fabrication and only one is legally operative.
#:
#: Choosing between them is a determination about which subdivision of 23 CCR 875.5 the
#: curtailment extends under, which is exactly the judgment 875(b) vests in the Deputy
#: Director and this project refuses to automate. So the model does not get to make it.
#:
#: What is left is a clean division of labour: **Gemma identifies and files the document,
#: the deterministic parsers read its table, and a human reads its law.**

#: Free-text fields. Shape cannot be asserted, so only the verbatim rule applies.
FREE_FIELDS = ("effective_date", "basin")

ALL_FIELDS = (*FIELD_PATTERNS, *FREE_FIELDS)

#: What the order DOES. Deliberately not a verified value, and this distinction cost a
#: live run to notice: the model answered "curtail" for an addendum whose own first
#: sentence says it is REINSTATING curtailments, and the verbatim rule passed it,
#: because the word "curtail" does appear in the document. It appears in "curtailments".
#:
#: A substring test cannot tell a quotation from an interpretation. So the action is
#: reported as the model's READING, never as an extracted value, exactly as the
#: Allocation Core surfaces judgment inputs instead of resolving them.
ACTIONS = ("curtail", "suspend", "reinstate", "rescind")

PROMPT = """You are reading one published California State Water Board curtailment order.

Extract ONLY values that appear literally in the document text below. Copy each value
exactly as it is written there, character for character. If a value is not stated in the
document, use null. Do not infer, do not calculate, and do not complete a partial value.

Return JSON with exactly these keys:
  order_number      the order identifier, for example WR 2024-0006-DWR
  addendum_number   the addendum number as digits only, or null if this is a base order
  basin             scott or shasta
  effective_date    the date this order was issued, as written
  action            what this order does: curtail, suspend, reinstate, or rescind

A value you cannot find in the text must be null. A wrong number here shuts off water
from the wrong ranch, so null is always the better answer.

DOCUMENT TEXT:
{document}
"""


def split_narrative(document: str) -> tuple[str, str]:
    """Separate the order's narrative from its Attachment A rights table.

    **This is routing, not truncation, and the difference matters.** The attachment is a
    structured table with a deterministic parser that reads it exactly, right by right,
    and handing that table to a language model would replace an exact reader with an
    approximate one on the most safety-critical data in the system. The narrative is the
    opposite case: prose that states the operative numbers in sentences whose shape
    drifts between issuances, which is what a parser cannot follow and a model can.

    So each half goes to the reader suited to it. Returns (narrative, attachment); the
    attachment is empty when the document has none.
    """
    match = ATTACHMENT_BOUNDARY.search(document)
    if match is None:
        return document, ""
    return document[: match.start()], document[match.start() :]


@dataclass(frozen=True, slots=True)
class NormalizedOrder:
    """A proposed reading of one document, with its unverified parts named."""

    values: dict[str, str]
    #: Fields the model produced that could NOT be found verbatim in the source. These
    #: are reported, never returned as values, and their presence is the signal that a
    #: human should read the document.
    unverified: tuple[str, ...] = ()
    #: Fields the model declined to answer. Different from unverified: the model saying
    #: "not stated" is the behaviour we asked for, not a failure.
    absent: tuple[str, ...] = ()
    #: The model's READING of what the order does, never a verified value. Kept in its
    #: own field so no caller can mistake an interpretation for a quotation, which is
    #: precisely the mistake the first version of this module made.
    proposed_action: str | None = None
    model: str = DEFAULT_MODEL
    #: What was rejected and why, in words, for the audit record.
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_human_reading(self) -> bool:
        """True when anything was proposed that a human still has to confirm.

        The proposed action counts. It is the field most likely to be wrong and the
        least likely to look wrong: on a real addendum the model answered "curtail"
        for a document that reinstates curtailments, which is a different legal act
        with a different service obligation under Water Code 1121.
        """
        return bool(self.unverified) or self.proposed_action is not None


def _ollama_generate(prompt: str, *, model: str) -> str:
    """Call the local runtime. No API key, no egress, no third party."""
    host = os.environ.get("OLLAMA_HOST", DEFAULT_HOST).rstrip("/")
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            # Deterministic-as-possible: this is document extraction, not writing.
            "options": {"temperature": 0.0, "num_ctx": 8192},
        }
    ).encode()
    request = urllib.request.Request(
        f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NormalizerUnavailableError(
            f"the local model at {host} could not be reached ({exc}). This is an "
            "unavailable layer, not a clean document: nothing was normalised."
        ) from exc
    text = payload.get("response")
    if not isinstance(text, str) or not text.strip():
        raise NormalizerUnavailableError(f"the local model at {host} returned no text")
    return text


def _normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace, so a line break inside a phrase cannot hide it.

    An OCR layer breaks `November 25, 1912` across a line often enough that a naive
    substring test reports a correctly-read value as a fabrication. This project has
    already been bitten by a newline-bounded pattern failing on OCR output.
    """
    return re.sub(r"\s+", " ", text)


def _appears_verbatim(value: str, source: str) -> bool:
    """The rule that makes fabrication structurally impossible for these fields."""
    return _normalise_whitespace(value).casefold() in _normalise_whitespace(source).casefold()


def normalize(
    document: str,
    *,
    model: str | None = None,
    generate: Generate | None = None,
) -> NormalizedOrder:
    """Propose a structured reading of one order document.

    Raises:
        NormalizerRefusedError: the document is empty or too large to read whole.
        NormalizerUnavailableError: the local model could not be reached.
    """
    if not document or not document.strip():
        raise NormalizerRefusedError("an empty document cannot be normalised")
    if len(document) > MAX_CHARS:
        raise NormalizerRefusedError(
            f"the document is {len(document)} characters, over the {MAX_CHARS} limit. "
            "Refusing rather than truncating: a truncated order still parses, and "
            "produces a confident reading of whichever half survived."
        )

    chosen = model or os.environ.get("MODEL_NORMALIZER") or DEFAULT_MODEL
    call = generate or _ollama_generate
    raw = call(PROMPT.format(document=document), model=chosen)

    try:
        proposed: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NormalizerUnavailableError(
            f"the model returned text that is not JSON ({exc}). Reported rather than "
            "salvaged: a partially parsed extraction is indistinguishable from a "
            "complete one downstream."
        ) from exc
    if not isinstance(proposed, dict):
        raise NormalizerUnavailableError("the model returned JSON that is not an object")

    values: dict[str, str] = {}
    unverified: list[str] = []
    absent: list[str] = []
    notes: list[str] = []

    for name in ALL_FIELDS:
        candidate = proposed.get(name)
        if candidate is None or (isinstance(candidate, str) and not candidate.strip()):
            absent.append(name)
            continue
        text = str(candidate).strip()

        pattern = FIELD_PATTERNS.get(name)
        if pattern is not None and not pattern.match(text):
            unverified.append(name)
            notes.append(f"{name}: {text!r} is not the shape this field takes")
            continue

        # The check that matters. `basin` is exempt because it is a controlled
        # vocabulary we supply rather than a value copied out of the document.
        if name != "basin" and not _appears_verbatim(text, document):
            unverified.append(name)
            notes.append(f"{name}: {text!r} does not appear in the document text")
            continue

        if name == "basin" and text.casefold() not in {"scott", "shasta"}:
            unverified.append(name)
            notes.append(f"basin: {text!r} is not one of the two basins this system knows")
            continue

        values[name] = text

    # The action is handled apart from every extracted value, on purpose. It is checked
    # against the controlled vocabulary and NOT against the document, because a
    # substring test cannot separate a quotation from an interpretation: "curtail" is
    # present in any document containing the word "curtailments", including one whose
    # actual act is reinstatement.
    action = proposed.get("action")
    proposed_action: str | None = None
    if action is None or not str(action).strip():
        absent.append("action")
    else:
        text = str(action).strip().casefold()
        if text in ACTIONS:
            proposed_action = text
            notes.append(
                f"action: {text!r} is the model's reading of what this order does, not a "
                "value quoted from it. Confirm against the document before relying on it."
            )
        else:
            unverified.append("action")
            notes.append(f"action: {text!r} is not one of {ACTIONS}")

    return NormalizedOrder(
        values=values,
        unverified=tuple(unverified),
        absent=tuple(absent),
        proposed_action=proposed_action,
        model=chosen,
        notes=tuple(notes),
    )
