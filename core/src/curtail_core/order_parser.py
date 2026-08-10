"""Extract the curtailment action from an order or addendum.

Deterministic and regex-driven. No language model runs in this path, for the
same reason none runs in the allocation path: the extraction that feeds a legal
recommendation has to be inspectable line by line, and a reviewer must be able
to see exactly which sentence produced which value.

**The corpus is not uniform, and that is a measured fact rather than a worry.**
Of 25 unique documents fetched on 2026-08-10, 21 carry an extractable text layer
and **4 carry none at all**: `pdftotext` returns three or four bytes. Those are
pure scans. One of them is Scott Addendum 8, the July 22 2025 suspension that
followed Watermaster field measurements, which is the single most important
fixture in the project.

So extraction has two paths and every result records which one produced it:

    TEXT_LAYER   pdftotext succeeded. Deterministic, auditable, free.
    REQUIRES_OCR no usable text layer. This module refuses and says so.

The second path is where a vision model becomes genuinely load-bearing rather
than decorative: it is the only way to read 16 percent of the corpus. What this
module will not do is pretend. A document with no text layer returns
`ExtractionMethod.REQUIRES_OCR` and empty fields, never a plausible guess, and
the backtest must not score it until something actually reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

#: Below this, pdftotext produced nothing usable. Real orders run tens of
#: thousands of characters; the empty ones measure three or four bytes.
MIN_USABLE_TEXT_CHARS = 200


class ExtractionMethod(StrEnum):
    TEXT_LAYER = "text_layer"
    REQUIRES_OCR = "requires_ocr"


class OrderAction(StrEnum):
    """The verb: what a document does to existing curtailment.

    The verb ONLY. The Board's titles carry three orthogonal dimensions and
    flattening them into one enum destroys the distinction the backtest scores.
    "Limited Conditional Suspension of Curtailments for Group 8 Surface Water
    Rights" and "Conditional Suspension of Curtailments for All Surface Water
    Rights" share a verb, differ in qualifier, and differ in scope, and the gap
    between one priority grouping and every priority grouping is the entire
    subject of this project.

    So: verb here, qualifier in `SuspensionQualifier`, scope in its own fields.
    """

    IMPOSE = "impose"
    REINSTATE = "reinstate"
    SUSPEND = "suspend"
    RESCIND = "rescind"
    AMEND = "amend"
    UNDETERMINED = "undetermined"


class SuspensionQualifier(StrEnum):
    """How a suspension was qualified, in the Board's own words.

    Derived from the corpus rather than assumed. Every value below appears
    verbatim in a fetched document. `UNQUALIFIED` is a real and common state,
    not a parse failure: Shasta Addendum 3 is titled "Suspension of All
    Curtailments in Shasta River Watershed" with no qualifier at all, and
    stamping it "conditional" would be a fabricated legal characterisation.
    """

    FULL = "full"
    CONDITIONAL = "conditional"
    LIMITED_CONDITIONAL = "limited_conditional"
    TEMPORARY = "temporary"
    TEMPORARY_CONDITIONAL = "temporary_conditional"
    UNQUALIFIED = "unqualified"
    #: The verb is not a suspension, so no qualifier can apply.
    NOT_APPLICABLE = "not_applicable"


#: Verb patterns, ordered most specific first.
#:
#: IMPOSE sits above the suspension fallback because a base order is titled
#: "ORDER IMPOSING ... CURTAILMENT" while its header routinely discusses the
#: conditions under which curtailment is later suspended.
_ACTION_PATTERNS: tuple[tuple[re.Pattern[str], OrderAction], ...] = (
    (
        re.compile(r"reinstat(?:e|ement|ing)\s+(?:of\s+)?(?:conditional\s+)?curtailment", re.I),
        OrderAction.REINSTATE,
    ),
    (re.compile(r"\brescission\b|\brescind", re.I), OrderAction.RESCIND),
    # "ORDER IMPOSING WATER RIGHT CURTAILMENT" and the Shasta variant, which
    # inserts CONDITIONAL between IMPOSING and WATER.
    (
        re.compile(r"\bimpos(?:e|ing)\s+(?:conditional\s+)?water\s+right\s+curtailment", re.I),
        OrderAction.IMPOSE,
    ),
    (re.compile(r"\bsuspend(?:ing)?\b|\bsuspension\b", re.I), OrderAction.SUSPEND),
    # Scott Addendum 5: "Update to Scott River Surface Water Curtailments for
    # Farmer's Ditch Company and Scott Valley Irrigation District". An amendment
    # scoped to named diverters rather than to a priority grouping.
    (re.compile(r"\bupdate\s+to\b[^.]{0,80}?curtailment", re.I), OrderAction.AMEND),
    (re.compile(r"\bamend(?:ed|ment|s|ing)\b", re.I), OrderAction.AMEND),
)

#: Qualifier patterns, ordered most specific first. "Limited Conditional" must
#: match before "Conditional", and "Temporary and Conditional" before either.
_QUALIFIER_PATTERNS: tuple[tuple[re.Pattern[str], SuspensionQualifier], ...] = (
    (
        re.compile(r"limited\s+conditional\s+suspension", re.I),
        SuspensionQualifier.LIMITED_CONDITIONAL,
    ),
    (
        re.compile(r"temporary\s+and\s+conditional\s+suspension", re.I),
        SuspensionQualifier.TEMPORARY_CONDITIONAL,
    ),
    (re.compile(r"conditional\s+suspension", re.I), SuspensionQualifier.CONDITIONAL),
    (re.compile(r"full\s+suspension", re.I), SuspensionQualifier.FULL),
    (re.compile(r"temporary\s+suspension", re.I), SuspensionQualifier.TEMPORARY),
)

#: "All Surface Water Rights", "All Curtailments". Distinguishes a basin-wide
#: action from one scoped to a grouping, which is the core backtest distinction.
_ALL_SCOPE = re.compile(r"\ball\s+(?:surface\s+water\s+rights|curtailments?)\b", re.I)

#: Priority grouping references. The Board writes both "Groups 1-8" and "Group 8",
#: and separates a range with a hyphen, an en-dash or a word.
#:
#: The en-dash is spelled as the escape \u2013 rather than pasted as a glyph.
#: On screen it is indistinguishable from a hyphen, so a maintainer tidying
#: punctuation could delete it without noticing, and every en-dash range in the
#: corpus would silently stop matching.
#:
#: Verified against the fetched corpus: addenda headlines write "Groups 1-8"
#: with a hyphen, while Order WR 2026-0005-DWR writes "Priority Groups 1 - 8"
#: with a spaced en-dash. That instance sits in Finding 7, in the body, and
#: group extraction is deliberately headline-scoped, so it is not matched today.
#: The separator is kept because headline typography varies across a decade of
#: documents and the cost of a miss is silently dropping a priority grouping.
_RANGE_SEPARATORS = "-\u2013"  # hyphen-minus, en dash
_GROUPS_RANGE = re.compile(rf"Groups?\s+(\d)\s*(?:[{_RANGE_SEPARATORS}]|through|to)\s*(\d)", re.I)
_GROUP_SINGLE = re.compile(r"Group\s+(\d)\b", re.I)

#: A priority cutoff date, e.g. "November 25, 1912" or "January 1, 1958".
_PRIORITY_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s*(1[89]\d{2}|20\d{2})\b"
)

#: Discharge values. Captures the number preceding a cfs unit.
_CFS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:cfs|cubic feet per second)", re.I)

_MONTHS = {
    m: i
    for i, m in enumerate(
        "January February March April May June July August September October "
        "November December".split(),
        start=1,
    )
}


@dataclass(frozen=True, slots=True)
class Extraction:
    """What a document actually said, and how it was read.

    Every field is optional because a document that does not state a value must
    produce an absent value, never an inferred one.
    """

    method: ExtractionMethod
    action: OrderAction = OrderAction.UNDETERMINED
    qualifier: SuspensionQualifier = SuspensionQualifier.NOT_APPLICABLE
    #: The header text the action was matched from, so a reviewer can check it.
    action_evidence: str | None = None
    priority_groups: tuple[int, ...] = field(default_factory=tuple)
    #: True when the document says "All Surface Water Rights" or "All
    #: Curtailments". Basin-wide and grouping-scoped are different legal acts.
    affects_all: bool = False
    priority_dates: tuple[date, ...] = field(default_factory=tuple)
    cfs_values: tuple[float, ...] = field(default_factory=tuple)
    #: What the body says this document does, where it says so about ITSELF.
    #: UNDETERMINED means the body made no self-referential claim, which is the
    #: common case and is not a defect.
    body_action: OrderAction = OrderAction.UNDETERMINED
    #: True when the title and the body describe different acts. Never resolved
    #: automatically. Real case: Scott Addenda 3, 4 and 5 are titled "Update to
    #: Scott River Surface Water Curtailments for ..." while their bodies grant a
    #: limited, conditional suspension to named diverters.
    title_body_conflict: bool = False
    #: A limited suspension that lapses reverts rights to curtailment with no
    #: further order, so an unmodelled expiry means believing a suspension is
    #: still running months after it ended.
    expires_on: date | None = None
    text_chars: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_readable(self) -> bool:
        return self.method is ExtractionMethod.TEXT_LAYER

    @property
    def scorable(self) -> bool:
        """Whether the backtest may score this record.

        Requires a real read AND a determined action, and refuses when the title
        and the body disagree. A document that was read but whose action could
        not be classified is not a scoring failure of the engine, it is an
        extraction gap, and conflating the two would corrupt the metric in the
        flattering direction. A document whose own title and body describe
        different acts is worse: scoring it means picking a side silently.
        """
        return (
            self.is_readable
            and self.action is not OrderAction.UNDETERMINED
            and not self.title_body_conflict
        )


#: A base order's action lives in an all-caps title line, not a subject line.
_TITLE_LINE = re.compile(r"^\s*(?:ORDER|NOTICE|ADDENDUM)\b[A-Z0-9 ,'()/-]{8,}$")

#: Sentences that describe THIS document, not one it recites.
#:
#: The safe anchor is the phrase "this Addendum" or "this Order". A recital names
#: the thing it recites ("Addendum 7", "Order WR 2024-0024-DWR"), so a pattern
#: requiring the self-reference cannot pick up a neighbouring document's act.
#: Without that anchor, reading the body would be the recital hazard that
#: headline-scoping exists to avoid.
_SELF_REFERENTIAL = r"this\s+(?:addendum|order)\b"

#: The verb between the self-reference and the noun carries the legal effect, so
#: it is matched explicitly rather than skipped over.
#:
#: This was a real bug, caught on Scott Addendum 12. A pattern that allowed any
#: 120 characters between "this Addendum" and "conditional suspension" matched
#: the sentence "This Addendum to the Orders ENDS the conditional suspension" and
#: concluded the addendum WAS a conditional suspension. It terminates one. The
#: word "ends" inverts the entire legal effect, and reading a reinstatement as a
#: suspension tells diverters they may divert when in fact they must stop.
#:
#: Note also that `[^.]` matches newlines, so a permissive gap silently reaches
#: across paragraph breaks in a way that is invisible when testing with grep.
#: Every pattern below is anchored on an affirmative or terminating verb.
_GRANTS = r"(?:provides?\s+for|is|constitutes?|hereby\s+\w+)"
_ENDS = r"(?:ends?|terminat\w+|rescind\w*|lift\w*|conclud\w*)"

_BODY_ACTION_PATTERNS: tuple[tuple[re.Pattern[str], OrderAction, SuspensionQualifier], ...] = (
    # Terminating forms FIRST. "ends the conditional suspension" is a
    # reinstatement, and must never fall through to a suspension pattern.
    (
        re.compile(
            rf"{_SELF_REFERENTIAL}[^.]{{0,60}}?{_ENDS}\s+the\s+[^.]{{0,40}}?suspension", re.I
        ),
        OrderAction.REINSTATE,
        SuspensionQualifier.NOT_APPLICABLE,
    ),
    (
        re.compile(rf"{_SELF_REFERENTIAL}[^.]{{0,80}}?cease\s+all\s+diversions", re.I),
        OrderAction.REINSTATE,
        SuspensionQualifier.NOT_APPLICABLE,
    ),
    (
        re.compile(
            rf"{_SELF_REFERENTIAL}[^.]{{0,40}}?{_GRANTS}\s+the\s+limited,?\s+conditional\s+suspension",
            re.I,
        ),
        OrderAction.SUSPEND,
        SuspensionQualifier.LIMITED_CONDITIONAL,
    ),
    (
        re.compile(
            rf"{_SELF_REFERENTIAL}[^.]{{0,40}}?{_GRANTS}\s+the\s+conditional\s+suspension", re.I
        ),
        OrderAction.SUSPEND,
        SuspensionQualifier.CONDITIONAL,
    ),
    (
        re.compile(rf"{_SELF_REFERENTIAL}[^.]{{0,40}}?fully\s+suspends", re.I),
        OrderAction.SUSPEND,
        SuspensionQualifier.FULL,
    ),
)

#: "This addendum expires at 11:59 pm on Monday, September 30, 2024".
#:
#: An expiry is legally load-bearing and nothing else in this module captures it.
#: A limited suspension that expires reverts the affected rights to curtailment
#: with no further order, so a model that ignores the expiry believes a
#: suspension is still running months after it lapsed.
_EXPIRY = re.compile(
    rf"{_SELF_REFERENTIAL}\s+expires\s+at[^.]{{0,60}}?"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s*(20\d{2})",
    re.I,
)


def _headline(text: str) -> str:
    """The bounded region of a document that carries its action.

    Three layouts appear in the real corpus and all three are covered:

    * **Letterhead with a subject line.** Most addenda. The subject can wrap, so
      three lines are taken.
    * **All-caps title.** Every base order. `ORDER IMPOSING WATER RIGHT
      CURTAILMENT AND REQUIRING REPORTING` sits on roughly line 8, below a
      mail-merge address block, so a first-three-lines heuristic misses it.
    * **Email printout.** Some addenda were distributed by email and saved from
      the client, so line one is the title, sometimes below client chrome.

    The window is deliberately bounded to the header region. Searching the whole
    document would let a reinstatement addendum match `IMPOSING` from a passing
    reference to the base order it amends.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""

    parts: list[str] = []
    for i, line in enumerate(lines[:40]):
        if re.match(r"^Subject\s*:", line, re.I):
            parts.append(" ".join(lines[i : i + 3]))
            break

    parts.extend(line for line in lines[:25] if _TITLE_LINE.match(line))
    parts.extend(lines[:3])
    return " ".join(parts)


def extract(text: str) -> Extraction:
    """Extract the action and its supporting values from a document's text.

    Args:
        text: Output of `pdftotext -layout`, or empty for a scan.
    """
    chars = len(text)
    if chars < MIN_USABLE_TEXT_CHARS:
        return Extraction(
            method=ExtractionMethod.REQUIRES_OCR,
            text_chars=chars,
            notes=(
                f"No usable text layer ({chars} characters). This is a scanned "
                "document. It must be read by a vision model or transcribed, and "
                "it must NOT be scored until something actually reads it.",
            ),
        )

    headline = _headline(text)
    action = OrderAction.UNDETERMINED
    evidence: str | None = None
    for pattern, candidate in _ACTION_PATTERNS:
        if pattern.search(headline):
            action, evidence = candidate, headline[:220]
            break

    # A qualifier is only meaningful on a suspension. Attaching one to an
    # imposition would assert something the Board did not say.
    qualifier = SuspensionQualifier.NOT_APPLICABLE
    if action is OrderAction.SUSPEND:
        qualifier = SuspensionQualifier.UNQUALIFIED
        for pattern, candidate_q in _QUALIFIER_PATTERNS:
            if pattern.search(headline):
                qualifier = candidate_q
                break

    # Groups: a range expands, a single is itself.
    groups: set[int] = set()
    for lo, hi in _GROUPS_RANGE.findall(headline):
        groups.update(range(int(lo), int(hi) + 1))
    if not groups:
        groups.update(int(g) for g in _GROUP_SINGLE.findall(headline))

    affects_all = bool(_ALL_SCOPE.search(headline))

    # What the body says about ITSELF. Only self-referential sentences count,
    # so a recital of a neighbouring order cannot be mistaken for this one's act.
    body_action = OrderAction.UNDETERMINED
    body_qualifier = SuspensionQualifier.NOT_APPLICABLE
    for pattern, candidate, candidate_q in _BODY_ACTION_PATTERNS:
        if pattern.search(text):
            body_action, body_qualifier = candidate, candidate_q
            break

    # A conflict is recorded, never resolved. Picking a side silently is the
    # failure this project exists to prevent, and the Board's own drafting makes
    # this a real case rather than a hypothetical one.
    conflict = (
        body_action is not OrderAction.UNDETERMINED
        and action is not OrderAction.UNDETERMINED
        and body_action is not action
    )

    expires_on: date | None = None
    expiry_match = _EXPIRY.search(text)
    if expiry_match:
        month_name, day, year = expiry_match.groups()
        try:
            expires_on = date(int(year), _MONTHS[month_name.title()], int(day))
        except (ValueError, KeyError):
            expires_on = None  # a malformed expiry is absent, never coerced

    # Priority dates and cfs come from the whole document, not just the headline.
    dates: list[date] = []
    for month, day, year in _PRIORITY_DATE.findall(text):
        try:
            dates.append(date(int(year), _MONTHS[month], int(day)))
        except ValueError:
            continue  # a malformed date is dropped, never coerced

    cfs = sorted({float(v) for v in _CFS.findall(text)})

    notes: list[str] = []
    if action is OrderAction.UNDETERMINED:
        notes.append(
            f"Action could not be classified from the headline: {headline[:120]!r}. "
            "Recorded as undetermined rather than guessed."
        )
    if conflict:
        notes.append(
            f"TITLE AND BODY DISAGREE. The title reads as {action.value}; the body "
            f"says of itself that it is a {body_action.value}"
            + (
                f" ({body_qualifier.value})"
                if body_qualifier is not SuspensionQualifier.NOT_APPLICABLE
                else ""
            )
            + ". Not scorable until a human resolves which governs. Recording the "
            "disagreement is the honest state; picking a side would put a guessed "
            "legal characterisation into the record."
        )

    return Extraction(
        method=ExtractionMethod.TEXT_LAYER,
        action=action,
        qualifier=qualifier,
        action_evidence=evidence,
        priority_groups=tuple(sorted(groups)),
        affects_all=affects_all,
        priority_dates=tuple(sorted(set(dates))),
        cfs_values=tuple(cfs),
        body_action=body_action,
        title_body_conflict=conflict,
        expires_on=expires_on,
        text_chars=chars,
        notes=tuple(notes),
    )
