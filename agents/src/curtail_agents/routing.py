"""Failure-tolerant routing: what happens when a worker agent loops or lies.

The binding rubric for this track asks one question by name: **how does the
system recover if a worker agent loops or returns a hallucination?** This module
is the answer, and it is deliberately structural rather than instructional.

**A system prompt is not a guardrail.** On a previous project a model invented a
regulatory article number despite an explicit prompt forbidding invention, and
the only thing that stopped it shipping was a deterministic server-side scrubber
running AFTER the model call. Curtail's Scribe drafts legal citations into orders
a human official signs, which is strictly worse, so the same lesson is applied
with two independent layers.

**Layer one, ledger validation.** Every draft is checked against the Allocation
Core's computed set before it can reach a PDF. A draft asserting a water right, a
priority date or an extent that is not in the ledger is rejected with a diff,
because a fluent order curtailing a right the Core never reached is exactly the
failure mode that would survive human review: it looks correct.

**Layer two, a citation allowlist.** Not a blacklist. Six external models
reviewing this concept produced fabricated case law including two cases that
exist in no database, and you cannot blacklist a citation nobody has invented
yet. Anything shaped like a legal citation that is not on the verified allowlist
is stripped and flagged.

**Layer three, the loop breaker, is now wired.** `MAX_ATTEMPTS` drives the
retry-then-escalate decision here, so a model failing the same check twice
escalates rather than looping. The backoff, jitter and wall-clock ceiling below
are consumed by `fleet.py`, which builds them into an ADK `RetryConfig` and
attaches it, with a `timeout`, to the nodes of the curtailment graph. One source
for both, so the threshold this guard escalates at and the attempts the graph
permits cannot drift apart.

This paragraph previously said the opposite, and the history is worth keeping. A
review found it claiming `RetryConfig` and `NodeTimeoutError` were in force while
both were dead constants: the drift this project's own rule about auditing claims
against shipped code exists to catch, committed in the file whose subject is not
trusting assertions. It was then labelled honestly as unwired, and only now, with
`fleet.py` importing these constants and `build_curtailment_graph()` assembling,
does the claim hold.

Nothing here trusts the model to behave. Every check runs on its output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path

from curtail_core.allocation import Recommendation

#: Bounded retry for a worker agent. ADK-native, so the rubric's question is
#: answered by pointing at a value rather than at a paragraph of intent.
#:
#: Jitter is not decoration. Without it, N agents failing on the same downstream
#: outage retry in lockstep and re-create the load that caused the failure.
MAX_ATTEMPTS = 3
INITIAL_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
BACKOFF_FACTOR = 2.0
JITTER = 0.3

#: Wall-clock ceiling for one agent invocation. A model that loops does not fail,
#: it simply never returns, so an attempt limit alone cannot catch it.
NODE_TIMEOUT_SECONDS = 120.0


#: The allowlist, packaged with the code that enforces it.
#:
#: A single path, deliberately. An earlier version tried the packaged location
#: and then fell back to the repo root, which read as prudence and was actually a
#: symptom: the file was still outside the package and the build was broken. When
#: a loader needs a fallback chain to find its own data, the packaging is wrong.
_CITATIONS_PATH = Path(__file__).resolve().parent / "data" / "citations.json"

#: Characters that can appear inside a section number.
#:
#: U+2010 HYPHEN is spelled as an escape rather than pasted, because it is
#: indistinguishable on screen from an ordinary hyphen-minus and a maintainer
#: tidying punctuation would delete it without noticing. Government PDFs really
#: do contain it, and losing it means a section number stops matching, which
#: here means a real citation gets stripped out of a lawful order.
_SECTION_NUMBER_CHARS = "\\d.\u2010-"

#: What a legal citation LOOKS like, regardless of whether it is real.
#:
#: The scrubber has to find candidates before it can judge them, and a fabricated
#: citation is by definition not in any list of known citations. So this matches
#: the SHAPE: a case name with "v.", a reporter cite, a code section.
_CITATION_SHAPES: tuple[re.Pattern[str], ...] = (
    # A case name: "Lux v. Haggin" (real), "Placeholder v. Example" (synthetic).
    # The illustrations here are deliberately synthetic. Writing a real
    # fabricated authority into a comment ships that string in an artifact,
    # which the citation guard correctly refuses: an example is still text a
    # reader can copy.
    re.compile(
        r"\b[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4}\s+v\.\s+[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4}"
    ),
    # A reporter cite: "50 Cal.App.5th 976" (real), "111 P.9d 222" (synthetic).
    re.compile(
        # Spaced AND unspaced reporter forms. "999 Cal. App. 5th 123" is the
        # California Style Manual form and was invisible to an unspaced pattern,
        # so a fabricated authority in the state's own standard citation style
        # passed straight through to a signed order. The allowlist could not
        # help: text that is never matched as citation-shaped is never checked.
        r"\b\d{1,4}\s+(?:Cal\.\s*(?:App\.\s*)?(?:\d\s*(?:th|d|st|nd|rd))?"
        r"|P\.\s*\d?d|F\.\s*\d?d|U\.\s*S\.)\s*\d{1,4}\b"
    ),
    # "Water Code 1846", "23 CCR 875.5", "C.R.S. 37-92-502"
    re.compile(
        rf"\b(?:Water Code|CCR|C\.R\.S\.|IDAPA|Idaho Code)"
        rf"(?:\s*,?\s*(?:\u00a7|section)\s*|\s+)"
        rf"[{_SECTION_NUMBER_CHARS}]*\d(?:\([a-z0-9]+\))*"
    ),
    # `\d+(?:\.\d+)*`, never `[\d.]+`. The character class was GREEDY over a
    # trailing full stop, so a citation ending a sentence produced the candidate
    # "23 CCR 875." while the allowlist matches "23 CCR 875". Full coverage then
    # failed by one character and the scrubber deleted a VERIFIED authority out of
    # a lawful draft, replacing it with a removal notice mid-sentence.
    #
    # A drafted order ends sentences with citations constantly, so this fired on
    # ordinary correct output rather than on anything adversarial. It is the third
    # time a boundary disagreement between the shape matcher and the allowlist has
    # stripped a real authority, which is why the fix belongs in the shape rather
    # than in another special case downstream.
    re.compile(r"\b\d{1,3}\s+CCR\s+\d+(?:\.\d+)*"),
)


class Verdict(StrEnum):
    """What the guard decided. Three states, never two.

    RETRY exists because a first failure is usually a fixable one: feeding the
    violation back to the model recovers most drafts. ESCALATE exists because a
    second failure is not, and silently retrying forever is the loop this module
    is named for.
    """

    PASS = "pass"
    RETRY = "retry"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class DraftAssertion:
    """What a drafted order claims. The unit the guard checks.

    Deliberately not the prose. Prose is what a model is good at and what a human
    can review. These are the CHECKABLE claims, and they are extracted from the
    draft so they can be compared against a computed set rather than read.
    """

    right_ids: tuple[str, ...]
    #: (right_id, priority_date) PAIRS, not a bare set of dates.
    #:
    #: A set validated any date against any right. With A-001 at 1912-11-25 and
    #: B-002 at 1885-04-01, a draft asserting A-001 with B-002's date passed. The
    #: set was also built from the WHOLE ledger, including entries the Core
    #: explicitly did not reach, so a date belonging to an unreached right
    #: counted as support for a curtailed one.
    #:
    #: Given that the November 1 versus November 25 1912 ambiguity is a standing
    #: open item on this project, a date check that cannot say WHICH right a date
    #: belongs to is weaker than it reads.
    asserted_dates: tuple[tuple[str, date], ...]
    extent_rank: int | None
    body_text: str


@dataclass(frozen=True, slots=True)
class GuardResult:
    verdict: Verdict
    #: Claims in the draft that the Core's ledger does not support. Each one is
    #: a right the order would curtail on no computed basis.
    unsupported_rights: tuple[str, ...] = field(default_factory=tuple)
    unsupported_dates: tuple[date, ...] = field(default_factory=tuple)
    #: Rights the Core reached that the draft never names. An order omitting
    #: them curtails less than the law requires, which is the failure Order
    #: WR 2026-0005-DWR was issued to correct.
    missing_rights: tuple[str, ...] = field(default_factory=tuple)
    extent_mismatch: str | None = None
    stripped_citations: tuple[str, ...] = field(default_factory=tuple)
    #: Findings that are real but fit none of the categories above: the prose diverging
    #: from the stated claims, the wrong basin's ladder vocabulary, an answer that could
    #: not be read as claims at all.
    #:
    #: They are NAMED rather than left to the reason string because `approval.sign`
    #: fails closed on an unverified draft whose guard named no specific finding, and it
    #: is right to: there would be nothing for an officer to acknowledge. Without this
    #: field those drafts were permanently unsignable and the console offered a button
    #: that could never succeed, which is the dead end this project has a rule about.
    other_findings: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def may_reach_pdf(self) -> bool:
        """Only a clean pass reaches the PDF generator.

        Stated as a property rather than left to each caller, because "did the
        guard pass" is the one question every downstream consumer must ask and
        the one it would be easiest to forget.
        """
        return self.verdict is Verdict.PASS


def _load_allowlist() -> tuple[re.Pattern[str], ...]:
    """Compile the verified authorities, refusing anything that would disarm us.

    A single allowlist entry able to match the empty string produces zero-width
    allowed spans at every offset and silently makes the entire scrubber inert,
    while every test asserting that a VERIFIED authority survives keeps passing.
    That is the conditionally-skipped-guard shape: the run stays green because
    the assertion stops being exercised. So it raises here instead.
    """
    data = json.loads(_CITATIONS_PATH.read_text())
    entries = data["allowlist"]
    if not entries:
        raise ValueError(
            "the citation allowlist is empty, which would strip every citation "
            "from every draft. Refusing rather than shipping an inert guard."
        )
    compiled: list[re.Pattern[str]] = []
    for entry in entries:
        pattern = re.compile(entry["pattern"])
        if pattern.match("") is not None:
            raise ValueError(
                f"allowlist pattern {entry['pattern']!r} matches the empty string. "
                "It would mark every position in every draft as verified and turn "
                "the scrubber off without failing a single test."
            )
        compiled.append(pattern)
    return tuple(compiled)


def scrub_citations(text: str) -> tuple[str, tuple[str, ...]]:
    """Strip any citation-shaped string that is not on the verified allowlist.

    Runs AFTER the model, never as an instruction to it. Returns the cleaned text
    and everything removed, because a silent strip is its own failure: the
    official needs to see that the draft tried to cite something unverifiable.

    An allowlist rather than a blacklist. A blacklist stops the fabrications you
    already know about, and the fabrications that matter are the ones nobody has
    invented yet.

    **Matched by SPAN OVERLAP, not by string equality, and that was a real bug.**
    A first version found citation-shaped candidates and then asked whether each
    candidate string appeared in the allowlist. The two sets of patterns do not
    agree on boundaries: the shape matcher captured "Stanford Vina Ranch
    Irrigation Co. v. State", truncating before "of California", and "CCR 875(b)"
    without the leading "23". Both are verified authorities and both were
    stripped out of a lawful draft. Comparing character ranges in the ORIGINAL
    text removes the whole class, because a truncated or extended span still
    overlaps the authority it came from.
    """
    allow = _load_allowlist()

    # Every character an allowlisted authority occupies.
    allowed_chars: set[int] = set()
    for pattern in allow:
        for match in pattern.finditer(text):
            allowed_chars.update(range(*match.span()))

    def is_allowed(start: int, end: int) -> bool:
        """FULL COVERAGE, not overlap. This distinction is the whole guard.

        An overlap test asks "does this candidate touch something verified?",
        which a fabrication satisfies by being built on one. Both blockers found
        in review had that single root cause:

          "Water Code 18460"  passed, because "Water Code 1846" is allowlisted
                              and the spans overlap. 18460 does not exist.
          "California Water Curtailment Cases v. Siskiyou Water Users Assn."
                              passed, because the first party is a real case.

        A model steered toward the verified authorities will hallucinate ON TOP
        of them, so the anchored fabrication is the LIKELIEST one this system
        sees, and overlap gave it a free pass. Full coverage asks the right
        question: is every character of this claim vouched for?
        """
        return all(i in allowed_chars for i in range(start, end))

    # Spans, not strings. `str.replace` is positional-blind and would rewrite an
    # occurrence that the span check had explicitly ALLOWED elsewhere in the
    # document, deleting a verified citation as collateral damage while
    # reporting only one removal.
    rejected: list[tuple[int, int, str]] = []
    for shape in _CITATION_SHAPES:
        for match in shape.finditer(text):
            start, end = match.span()
            if is_allowed(start, end):
                continue
            rejected.append((start, end, match.group(0).strip()))

    # Drop spans nested inside a longer rejected span, so one citation is not
    # reported or replaced twice.
    rejected.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    kept: list[tuple[int, int, str]] = []
    for start, end, textual in rejected:
        if kept and start < kept[-1][1]:
            continue
        kept.append((start, end, textual))

    cleaned = text
    for start, end, _ in reversed(kept):
        cleaned = (
            cleaned[:start] + "[CITATION REMOVED: not on the verified allowlist]" + cleaned[end:]
        )

    seen: list[str] = []
    for _, _, textual in kept:
        if textual and textual not in seen:
            seen.append(textual)
    return cleaned, tuple(seen)


def validate_draft(
    draft: DraftAssertion,
    recommendation: Recommendation,
    *,
    attempt: int = 1,
) -> GuardResult:
    """Check a draft against the Core's computed set.

    Args:
        draft: The checkable claims extracted from a drafted order.
        recommendation: The Core's output, which is the only authority on which
            rights are reached and to what extent.
        attempt: Which attempt this is. A first failure retries with the
            violation fed back; a second escalates to the human queue flagged
            UNVERIFIED rather than retrying again, because a model that failed
            the same check twice is looping, not learning.
    """
    if attempt < 1:
        raise ValueError(
            f"attempt must be 1 or greater, got {attempt}. A zero-indexed caller "
            "would silently get an extra retry and escalate a round later than "
            "documented. A guard's own inputs are the last place to be permissive."
        )

    supported_rights = set(recommendation.rights_reached)
    unsupported = tuple(r for r in draft.right_ids if r not in supported_rights)

    # OMISSION, not just over-inclusion.
    #
    # A one-directional guard cannot see the failure this project's headline
    # artifact is about. Order WR 2026-0005-DWR exists solely because rights
    # "were not included in ... Order WR 2024-0024-DWR, even though they are
    # within Priority Groups 1 through 8". A guard checking only that a draft
    # claims nothing extra would have passed that defective order.
    missing = tuple(r for r in recommendation.rights_reached if r not in set(draft.right_ids))

    # Per RIGHT, and only from entries that would actually be curtailed. A date
    # belonging to a right the Core did not reach cannot vouch for one it did.
    dates_by_right: dict[str, date] = {
        e.right_id: e.priority_date
        for e in recommendation.ledger
        if e.priority_date is not None and e.would_be_curtailed
    }
    unsupported_dates = tuple(
        asserted
        for right_id, asserted in draft.asserted_dates
        if dates_by_right.get(right_id) != asserted
    )
    mismatched_pairs = tuple(
        f"{right_id} asserted as {asserted.isoformat()}, ledger says "
        + (dates_by_right[right_id].isoformat() if right_id in dates_by_right else "no date")
        for right_id, asserted in draft.asserted_dates
        if dates_by_right.get(right_id) != asserted
    )

    extent_mismatch: str | None = None
    if (
        draft.extent_rank is not None
        and draft.extent_rank != recommendation.recommended_extent_rank
    ):
        extent_mismatch = (
            f"draft asserts extent rank {draft.extent_rank}; the Core computed "
            f"{recommendation.recommended_extent_rank}"
        )

    _, stripped = scrub_citations(draft.body_text)

    # POSITIVE EVIDENCE is required before a pass.
    #
    # Every check above is of the form "nothing asserted that is unsupported",
    # which an EMPTY draft satisfies completely. An extraction failure, a schema
    # drift, or a model output the extractor does not recognise all degrade to
    # empty tuples, and the guard returned a confident PASS whose reason string
    # affirmatively said the draft matched the ledger. That is a failure
    # producing a plausible success, on the one code path whose entire job is to
    # prevent exactly that.
    read_nothing: list[str] = []
    if recommendation.rights_reached and not draft.right_ids:
        read_nothing.append("no rights were read from a draft whose ledger reaches some")
    if recommendation.recommended_extent_rank is not None and draft.extent_rank is None:
        read_nothing.append("no extent was read from a draft whose ledger computed one")
    if not draft.body_text.strip():
        read_nothing.append("the draft body is empty")

    clean = (
        not unsupported
        and not unsupported_dates
        and not missing
        and extent_mismatch is None
        and not stripped
        and not read_nothing
    )
    if clean:
        return GuardResult(verdict=Verdict.PASS, reason="draft matches the computed ledger")

    parts: list[str] = []
    if unsupported:
        parts.append(
            f"{len(unsupported)} right(s) asserted with no basis in the ledger: "
            f"{', '.join(unsupported[:5])}"
        )
    if unsupported_dates:
        parts.append(
            f"{len(unsupported_dates)} priority date(s) that do not match the right "
            "they are asserted for: " + "; ".join(mismatched_pairs[:3])
        )
    if missing:
        parts.append(
            f"{len(missing)} right(s) reached by the Core but ABSENT from the draft: "
            f"{', '.join(missing[:5])}"
        )
    if read_nothing:
        parts.append(
            "the draft appears not to have been read at all ("
            + "; ".join(read_nothing)
            + "), so a pass would be a claim about a document nobody parsed"
        )
    if extent_mismatch:
        parts.append(extent_mismatch)
    if stripped:
        parts.append(f"{len(stripped)} unverifiable citation(s): {', '.join(stripped[:3])}")

    verdict = Verdict.RETRY if attempt < MAX_ATTEMPTS - 1 else Verdict.ESCALATE
    tail = (
        " Retrying once with the violation fed back into the prompt."
        if verdict is Verdict.RETRY
        else (
            " Second failure on the same check. Escalating to the human queue "
            "flagged UNVERIFIED rather than retrying, because a model that fails "
            "the same check twice is looping, not learning."
        )
    )
    return GuardResult(
        verdict=verdict,
        unsupported_rights=unsupported,
        unsupported_dates=unsupported_dates,
        missing_rights=missing,
        extent_mismatch=extent_mismatch,
        stripped_citations=stripped,
        reason="; ".join(parts) + "." + tail,
    )
