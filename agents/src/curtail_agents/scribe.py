"""The Order Scribe: the one place a model writes text a state official would sign.

Everything around it already existed and had nothing to guard. `validate_draft` compares
a draft's checkable claims against the Allocation Core's computed ledger; `scrub_citations`
strips any authority not on the verified allowlist; the sanitizer fences untrusted order
text before it reaches a prompt. Until now no draft was ever produced, so the guards ran
only in tests and in the chaos drill. This wires the model, and the guards finally guard.

**The model is never trusted for a fact.** It is asked to write prose, and to STATE the
claims it is making in a structured field. Those claims are then checked against the
Core, which is the only authority on which rights are reached and how far curtailment
extends. A draft asserting a right, a priority date or an extent the Core did not compute
is rejected with the specific violation, retried once with that violation fed back, and
then escalated to the human queue flagged UNVERIFIED rather than retried again. A model
that fails the same check twice is looping, not learning.

**Structured claims are checked against the PROSE as well.** Asking a model for both
creates a gap the guard alone cannot see: the JSON claims can match the ledger perfectly
while the sentences say something else. Every right named in the prose must appear in the
claims and every claimed right must appear in the prose, so the two cannot diverge without
the draft being refused.

**Location is `global`, and that is a finding rather than a preference.** Gemini 3.5 is
listed in the Vertex catalogue for us-central1 and returns 404 NOT_FOUND when actually
called there, on this project, in every regional endpoint tried (us-central1, us-east5,
europe-west4). Only `global` serves it. The catalogue lists what exists; access is
separate, and a model id copied from a listing is not a working model id.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from curtail_agents.routing import (
    DraftAssertion,
    GuardResult,
    Verdict,
    scrub_citations,
    validate_draft,
)
from curtail_core.allocation import Recommendation

#: Gemini 3.5 or newer is a hard competition requirement, so the default is pinned to a
#: model that satisfies it rather than to whatever is newest.
DEFAULT_MODEL = "gemini-3.5-flash"

#: See the module docstring. Regional endpoints 404 for this model on this project.
DEFAULT_LOCATION = "global"

#: Two attempts, and the second is the last. The ceiling is the loop breaker: a model
#: that fails the same structural check twice is not converging on it.
MAX_ATTEMPTS = 2


class ScribeUnavailableError(RuntimeError):
    """Raised when no model can be reached. Never falls back to a canned draft.

    A stub order is the worst artifact this system could produce. It looks exactly like
    a real one, carries the same letterhead, and would be signed. Refusing is the only
    safe failure.
    """


@dataclass(frozen=True, slots=True)
class DraftOutcome:
    """A drafted order and everything a reviewer needs to judge it."""

    #: The prose, with any authority outside the verified allowlist already stripped.
    text: str
    verdict: Verdict
    guard: GuardResult
    attempts: int
    model: str
    #: True when the draft failed its checks to the end. It reaches the human queue
    #: LABELLED, never the PDF generator.
    escalated: bool = False
    #: Every violation seen along the way, including ones a later attempt fixed, so the
    #: record shows what the model had to be told rather than only where it landed.
    violations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def may_reach_pdf(self) -> bool:
        """One question, asked one way. Mirrors GuardResult.may_reach_pdf so a caller
        cannot accidentally consult the looser of the two."""
        return self.verdict is Verdict.PASS and not self.escalated


#: The claims a draft must state, so they can be checked rather than parsed out of prose.
CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_text": {"type": "string"},
        "curtailed_right_ids": {"type": "array", "items": {"type": "string"}},
        "asserted_priority_dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "right_id": {"type": "string"},
                    "priority_date": {"type": "string"},
                },
                "required": ["right_id", "priority_date"],
            },
        },
        "extent_rank": {"type": "integer", "nullable": True},
    },
    "required": ["order_text", "curtailed_right_ids", "asserted_priority_dates"],
}


def _authorities() -> str:
    """The authorities a draft may cite, read from the same file the scrubber uses.

    One source, so the prompt cannot drift from the guard. Telling a model it may cite
    something the scrubber will strip produces a draft that fails for a reason nobody
    intended, and the reverse leaves the model guessing.
    """
    from curtail_agents.routing import _CITATIONS_PATH

    raw = json.loads(_CITATIONS_PATH.read_text())
    return "\n".join(f"- {entry['authority']}" for entry in raw["allowlist"])


def _priority_as_stated(entry: Any) -> str:
    """How a ledger line renders priority, including when only a year is known.

    Three states, not two. A full date, a YEAR ONLY, and genuinely nothing. The middle
    one used to render as "not stated", which understated the record: the year is what
    established decree membership for that right in the first place, so the order text
    denied knowing the very fact the placement rests on. `SG005441` carries 1977, which
    is unambiguously after the 1932 Shasta Adjudication, and its Tier A placement is
    correct BECAUSE of that year.

    **This delegates rather than implements.** The same logic lived here alone, and the
    API went on serialising the bare date, so one right rendered two ways depending on
    which surface a reader was looking at. The rendering now belongs to the ledger entry
    and every consumer shares it.
    """
    stated: str = entry.priority_as_stated
    return stated


def build_prompt(recommendation: Recommendation, *, feedback: str = "") -> str:
    """The drafting instruction, built entirely from the Core's computed output.

    The ledger is included as DATA the draft must reproduce, not as background. That is
    the point: the model is writing the order around a set of facts it did not choose,
    and every one of those facts is checked afterwards.
    """
    reached = [e for e in recommendation.ledger if e.would_be_curtailed]
    rows = "\n".join(
        f"- {e.right_id} | priority "
        + _priority_as_stated(e)
        + f" | {e.placement.grouping_label} | {e.placement.citation}"
        for e in reached
    )
    correction = (
        f"\n\nA PREVIOUS ATTEMPT WAS REJECTED. Fix exactly this and change nothing "
        f"else:\n{feedback}\n"
        if feedback
        else ""
    )
    return f"""You are drafting a curtailment order for a California state watermaster to
review and sign. You are NOT deciding anything. Every fact below was computed by a
deterministic engine and is the only authority on who is curtailed.

Basin: {recommendation.basin.value}
Evaluated for: {recommendation.evaluated_for.isoformat()}
Observed discharge: {recommendation.observed_cfs} cfs
Operative minimum: {recommendation.operative_minimum_cfs} cfs
Shortfall: {recommendation.shortfall_cfs} cfs
Recommended extent rank: {recommendation.recommended_extent_rank}

RIGHTS REACHED, and the order must name every one of them and no others:
{rows}

Write the order body. Include, in this order: the authority under which it issues, the
findings of fact, the priority grouping curtailment extends to, the effective date, the
exceptions for human health and safety, non-consumptive use and minimum livestock
watering, the certification duty, and the contact for questions.

RULES, each of which is checked mechanically after you answer:
0. Describe groupings using ONLY the grouping label shown for each right above. Do not
   describe this basin's rights using the other basin's ladder: the Scott ladder is
   numbered Priority Groups and turns on decree schedules and post-1914 status, the
   Shasta ladder is Tiers A to C under 875.5(b)(1).
1. Name every right id in the list above, and no right id that is not in it.
2. Where you state a priority date, state the one this list gives for that right.
3. State the extent rank exactly as given.
4. Cite ONLY these authorities. Anything else is stripped and the draft is rejected:
{_authorities()}
5. Do not state a penalty amount. The regulation's printed figure is obsolete.
6. This is a DRAFT for signature. Do not write as though it is already in force.

Return the prose in `order_text`, and separately state the claims you made:
`curtailed_right_ids`, `asserted_priority_dates`, and `extent_rank`.{correction}"""


def _client(location: str) -> Any:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise ScribeUnavailableError(
            "GOOGLE_CLOUD_PROJECT is not set, so no model can be reached. Refusing "
            "rather than returning a canned draft: a stub order looks exactly like a "
            "real one and would be signed."
        )
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover - the SDK is a declared dependency
        raise ScribeUnavailableError(f"the Gemini SDK is not installed: {exc}") from exc
    return genai.Client(vertexai=True, project=project, location=location)


def _assertion_from(payload: dict[str, Any]) -> DraftAssertion:
    """Turn the model's stated claims into the unit the guard checks.

    Malformed claims are NOT silently dropped. Every check in `validate_draft` is of the
    form "nothing asserted that is unsupported", which an empty assertion satisfies
    completely, and the guard has a positive-evidence check precisely because that
    degradation once produced a confident pass. Raising here keeps the two consistent:
    an unreadable claim is a failure, not an absence.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"the answer is a {type(payload).__name__}, not a set of claims")

    dates: list[tuple[str, date]] = []
    for pair in payload.get("asserted_priority_dates") or []:
        if not isinstance(pair, dict):
            raise ValueError(f"a priority date claim is a {type(pair).__name__}, not a record")
        dates.append((str(pair["right_id"]), date.fromisoformat(str(pair["priority_date"]))))

    rights = payload.get("curtailed_right_ids") or []
    if not isinstance(rights, list) or not all(isinstance(r, str) for r in rights):
        raise ValueError("curtailed_right_ids is not a list of right ids")

    extent = payload.get("extent_rank")
    if extent is not None and not isinstance(extent, int):
        raise ValueError(f"extent_rank is a {type(extent).__name__}, not a rank")

    text = payload.get("order_text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the draft carries no order text")

    return DraftAssertion(
        right_ids=tuple(rights),
        asserted_dates=tuple(dates),
        extent_rank=extent,
        body_text=text,
    )


def prose_disagrees_with_claims(assertion: DraftAssertion, reached: tuple[str, ...]) -> str:
    """Whether the sentences and the stated claims describe the same order.

    Asking a model for prose AND for structured claims opens a gap the ledger guard
    cannot see: the claims can match the computed set perfectly while the prose names a
    different set of rights. The guard reads the claims. A watermaster reads the prose.

    Checked in both directions. A right claimed but never written is an order that
    curtails less than it says, and a right written but never claimed is one that
    curtails more than anything checked.
    """
    body = assertion.body_text
    unwritten = [r for r in assertion.right_ids if r not in body]
    if unwritten:
        return (
            f"{len(unwritten)} right(s) are claimed but appear nowhere in the order "
            f"text: {', '.join(unwritten[:5])}"
        )
    unclaimed = [r for r in reached if r in body and r not in assertion.right_ids]
    if unclaimed:
        return (
            f"{len(unclaimed)} right(s) are named in the order text but absent from the "
            f"stated claims: {', '.join(unclaimed[:5])}"
        )
    return ""


#: Grouping vocabulary that belongs to one basin's ladder and not the other's. The Scott
#: ladder is numbered Priority Groups 1 to 9 and turns on decree schedules and post-1914
#: status; the Shasta ladder is Tiers A to C under 875.5(b)(1). A draft describing one
#: basin in the other's language is a materially wrong document even when every right id,
#: date and extent it states is correct.
_BASIN_VOCABULARY: dict[str, tuple[str, ...]] = {
    "scott": ("tier a", "tier b", "tier c", "875.5(b)"),
    "shasta": ("priority group", "post-1914", "post 1914", "schedule d", "surplus class"),
}


def wrong_basin_vocabulary(text: str, basin: str) -> tuple[str, ...]:
    """Language from the OTHER basin's ladder, which the ledger guard cannot see.

    Found by reading the first real drafted order: every checkable claim passed, and the
    prose described Shasta rights as curtailed by their "post-1914" status, which is a
    Scott concept. The guard checks right ids, priority dates, the extent and the
    authorities. It does not read the sentences, and a watermaster signs the sentences.

    This is a narrow check and it is stated as narrow: it catches the wrong LADDER, not
    every wrong characterization. What it removes is the class where a draft is correct
    in its facts and wrong about which body of law reached them.
    """
    lowered = text.lower()
    return tuple(term for term in _BASIN_VOCABULARY.get(basin, ()) if term in lowered)


def draft_order(
    recommendation: Recommendation,
    *,
    model: str | None = None,
    location: str | None = None,
    generate: Any = None,
) -> DraftOutcome:
    """Draft an order for signature, guarded, with one retry and then escalation.

    Args:
        recommendation: The Core's output. The only authority on the facts.
        model: Overrides MODEL_SCRIBE.
        location: Overrides the `global` default. See the module docstring.
        generate: An injected callable taking (prompt) and returning the raw JSON text,
            so the guard chain can be tested without a model. Production passes nothing.

    Raises:
        ScribeUnavailableError: when no model can be reached, for ANY reason: no
            project, no SDK, a region that does not serve the model, a timeout, a
            transport failure, or an empty answer. Never returns a stub.

            Read as a specification. The earlier version guarded only the credential
            path, so the 404 this module's own docstring describes escaped as a raw
            ClientError and crashed the drafting path.
    """
    chosen = model or os.environ.get("MODEL_SCRIBE") or DEFAULT_MODEL
    where = location or os.environ.get("VERTEX_LOCATION") or DEFAULT_LOCATION
    call = generate or _vertex_generate(chosen, where)

    reached = recommendation.rights_reached
    violations: list[str] = []
    feedback = ""
    guard = GuardResult(verdict=Verdict.ESCALATE, reason="no attempt was made")
    assertion = DraftAssertion((), (), None, "")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = call(build_prompt(recommendation, feedback=feedback))
        except ScribeUnavailableError:
            # Already the stated failure, already carrying its own reason. Re-wrapping
            # would bury the specific cause under a generic one.
            raise
        except Exception as exc:
            # The documented contract said an unreachable model becomes this error, and
            # only the CREDENTIAL path was guarded. A 404 from a region that does not
            # serve the model, a timeout, or any transport failure escaped as itself and
            # crashed the drafting path. The module's own docstring named the case.
            #
            # Raised rather than escalated, deliberately. An outage and a rejected draft
            # are DIFFERENT CAUSES: escalation means a draft exists and failed its
            # checks, and reporting an outage that way sends a reviewer to read prose
            # that was never written.
            raise ScribeUnavailableError(
                f"the model call failed on attempt {attempt} of {MAX_ATTEMPTS}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        try:
            assertion = _assertion_from(json.loads(raw))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            feedback = f"the previous answer could not be read as claims: {exc}"
            violations.append(feedback)
            guard = GuardResult(
                verdict=Verdict.RETRY if attempt < MAX_ATTEMPTS else Verdict.ESCALATE,
                other_findings=(feedback,),
                reason=feedback,
            )
            continue

        foreign = wrong_basin_vocabulary(assertion.body_text, recommendation.basin.value)
        divergence = prose_disagrees_with_claims(assertion, reached)
        if foreign and not divergence:
            divergence = (
                f"the order describes a {recommendation.basin.value} basin curtailment "
                f"in the other basin's ladder vocabulary: {', '.join(foreign)}"
            )
        if divergence:
            feedback = divergence
            violations.append(divergence)
            guard = GuardResult(
                verdict=Verdict.RETRY if attempt < MAX_ATTEMPTS else Verdict.ESCALATE,
                other_findings=(divergence,),
                reason=divergence,
            )
            continue

        guard = validate_draft(assertion, recommendation, attempt=attempt)
        if guard.may_reach_pdf:
            clean, _ = scrub_citations(assertion.body_text)
            return DraftOutcome(
                text=clean,
                verdict=guard.verdict,
                guard=guard,
                attempts=attempt,
                model=chosen,
                violations=tuple(violations),
            )
        feedback = guard.reason
        violations.append(guard.reason)

    # Escalated, and the text still travels. A watermaster reviewing why a draft was
    # refused needs to see what was written, and the label is what keeps it out of the
    # PDF generator rather than the text being withheld.
    clean, _ = scrub_citations(assertion.body_text)
    return DraftOutcome(
        text=clean,
        verdict=Verdict.ESCALATE,
        guard=guard,
        attempts=MAX_ATTEMPTS,
        model=chosen,
        escalated=True,
        violations=tuple(violations),
    )


def _vertex_generate(model: str, location: str) -> Any:
    """The real call, deferred so nothing touches the network at import time."""

    def call(prompt: str) -> str:
        from google.genai import types

        client = _client(location)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                # thinking_level, not temperature. temperature, top_p and top_k are
                # deprecated on these models.
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
                response_mime_type="application/json",
                response_schema=CLAIM_SCHEMA,
            ),
        )
        text: str | None = response.text
        if not text:
            raise ScribeUnavailableError(
                f"{model} returned no text. An empty answer is not a draft, and an "
                "empty draft passes every check of the form 'nothing unsupported'."
            )
        return text

    return call
