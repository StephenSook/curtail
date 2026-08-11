"""Parse the rights table the Board attaches to a curtailment order.

Attachment A is the list of every water right an order actually reaches. Until it is
parsed, the Allocation Core has only ever run on rights invented in tests, and the
per-right ledger that makes a signed order reviewable has nothing real in it.

**Owner names are never retained.** The table's first column is the primary owner, and
those are real private parties, some of them named in live reconsideration proceedings.
The document is public and the state published it; republishing a structured, queryable
table of those names in this repository is a different act, and the standing rule is
that third-party personal data does not go into a public repository even when the
source is public. Nothing here needs it: the row anchor is the APPLICATION NUMBER, the
ladder keys on priority date and class, and notification contacts are synthetic and
labelled by design. So the owner column is not extracted, not stored, and not emitted.

**Two things this parser refuses to invent.**

1. *The curtailment status column.* In `pdftotext` layout output the status cell wraps
   across the lines above and below its own row, so associating it by position is
   guesswork. The status is therefore not read off the table. What IS read off the
   document is the body's own stated rule, which assigns disposition by priority date
   band, and that is applied here with the band boundaries cited.

2. *The additional-conditions subset.* Addendum 6 says, verbatim, that rights subject
   to the extra conditions "are listed in orange in the Attachment to this Addendum".
   **That is a legally operative distinction encoded by colour alone**, and no text
   extraction can recover it. It is reported as unrecoverable rather than guessed.
   Worth noting for what it is: the same defect this project's own status rule forbids
   in its interface, appearing in the source document a curtailment engine has to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from curtail_core.adjudications import ADJUDICATIONS, AdjudicationId, RightClass, WaterRight
from curtail_core.basins import Basin

#: Application and statement numbers as the Board prints them. Four prefixes appear in
#: the Shasta attachment: A (application), D (a small number of older records), C, and
#: SG (Statement of Groundwater diversion). The prefix set is explicit rather than
#: `[A-Z]+` so a new one surfaces as an unparsed row instead of being absorbed silently.
APPLICATION_NUMBER = re.compile(r"\b(?P<id>(?:SG|A|D|C)\d{4,7})\b")

_PRINTED_DATE = re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})\b")

#: A YEAR-ONLY priority, which the Board prints for a small number of records, e.g.
#: "1977 -". It is not a date and is never widened into one: inventing January 1 would
#: manufacture a priority more senior than the record supports, and priority seniority
#: is the whole doctrine. It is carried as a year and used only where the year settles
#: the question on its own.
_PRINTED_YEAR_ONLY = re.compile(r"\b(?P<y>1[89]\d{2}|20\d{2})\b\s*-?\s*$")

#: A RANGE of years, printed as "1970 to 1981". Checked BEFORE the year-only pattern
#: and refused, because the year-only pattern matches the end of a range and would
#: silently collapse it to one endpoint. That was not hypothetical: it turned a right
#: printed as "1970 to 1981" into a 1981 priority on the first run, which asserts a
#: seniority the record does not state and moves the right across groupings. Whichever
#: endpoint you pick, you have written a false statement into the basis of a signed
#: order.
_PRINTED_YEAR_RANGE = re.compile(
    r"\b(1[89]\d{2}|20\d{2})\b\s*(?:to|through|-|and)\s*\b(1[89]\d{2}|20\d{2})\b"
)

#: How the Board prints "this record states no priority date".
MISSING_DATE = "--"

#: 23 CCR 875.5(b)(1)(B), and Addendum 6's own scope sentence: the attachment covers
#: post-Adjudication appropriative rights under (b)(1)(A) "as well as more senior
#: appropriative rights with priority dates of or junior to November 25, 1912, as
#: described in section 875.5, subdivision (b)(1)(B)".
SHASTA_SENIOR_BOUND = date(1912, 11, 25)

#: Addendum 6: rights "of, or junior to, January 1, 1958" carry additional conditions.
SHASTA_ADDITIONAL_CONDITIONS_FROM = date(1958, 1, 1)


class DateBand(StrEnum):
    """Which of the document's own stated bands a priority date falls in.

    Derived from the date, not read from the coloured column, and named so a reader
    can tell the difference at a glance.
    """

    SENIOR_TO_THE_BAND = "senior_to_the_band"
    CONDITIONAL = "conditional"
    CONDITIONAL_WITH_ADDITIONAL_CONDITIONS = "conditional_with_additional_conditions"
    UNDETERMINED_NO_DATE = "undetermined_no_date"


@dataclass(frozen=True, slots=True)
class AttachedRight:
    """One row, carrying only what the document states and no owner identity."""

    application_number: str
    #: The Source column exactly as printed, empty when the cell is blank. Blank is
    #: NOT filled from the row above: the printed table carries a value down a block
    #: visually, and layout text gives no cell boundaries to prove where a block ends.
    #: A wrong source silently merges unrelated rights into one shared-source group,
    #: and monotonicity is only meaningful within a source.
    source_as_printed: str
    priority_date: date | None
    priority_date_missing: bool
    band: DateBand
    page: int
    #: Set when the record states only a year, e.g. "1977 -". Distinct from a missing
    #: date: the record says something, just not enough to order two rights inside the
    #: same year. Never widened into a date.
    priority_year_only: int | None = None

    def __post_init__(self) -> None:
        if self.priority_date is not None and self.priority_date_missing:
            raise ValueError(
                f"{self.application_number}: a date is present and also marked missing"
            )
        if self.priority_year_only is not None and self.priority_date is not None:
            raise ValueError(
                f"{self.application_number}: a full date and a year-only value cannot "
                "both be present"
            )
        if self.priority_year_only is not None and self.priority_date_missing:
            raise ValueError(
                f"{self.application_number}: a year is stated, so the date is imprecise "
                "rather than missing, and conflating the two loses that distinction"
            )


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Rights the ladder can place, rights it must not, and why."""

    rights: tuple[WaterRight, ...]
    #: Named, never defaulted. See to_water_rights for what defaulting these did.
    unplaceable: tuple[str, ...]
    open_questions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttachmentReport:
    """Rows, plus everything that did NOT become a row and why.

    A parser that returns only its successes cannot be audited. The counts here are
    what makes it possible to say a table was read completely rather than read
    somehow.
    """

    #: Every distinct application number the page text contained, counted as it was
    #: encountered and independently of what became of it. The one number in this
    #: record that is measured rather than derived.
    application_numbers_seen: int
    rights: tuple[AttachedRight, ...]
    #: Lines that carried an application number but no usable date cell.
    unparsed: tuple[str, ...] = field(default_factory=tuple)
    #: Rows whose Source cell was blank, listed by application number.
    blank_source: tuple[str, ...] = field(default_factory=tuple)
    #: Rows stating a priority too imprecise to order against neighbours, e.g. a year
    #: range. Named rather than narrowed.
    imprecise: tuple[str, ...] = field(default_factory=tuple)
    #: Rows where more than one adjacent line offered a priority, so ownership could
    #: not be proven. Refused rather than resolved by position.
    ambiguous: tuple[str, ...] = field(default_factory=tuple)
    #: Rows whose priority came from an adjacent line because a wrapped owner name left
    #: the application number alone on its own. Recorded so the provenance of those
    #: dates is visible rather than indistinguishable from a same-line read.
    recovered_from_neighbour: tuple[str, ...] = field(default_factory=tuple)
    #: Stated, always, so a reader of the output is told what the input could not say.
    unrecoverable: tuple[str, ...] = field(
        default_factory=lambda: (
            "The curtailment status column wraps across adjacent lines in layout text "
            "and is not read positionally; disposition is derived from the document's "
            "own priority-date bands instead.",
            "Addendum 6 states that rights subject to the additional conditions are "
            "'listed in orange in the Attachment'. That distinction is encoded by "
            "colour alone and cannot be recovered from extracted text.",
        )
    )

    @property
    def accounted_for(self) -> int:
        """Every row that reached a bucket. Must equal `application_numbers_seen`.

        `ambiguous` belongs here and was missing from the first version of the audit
        record, so a row refused for ambiguity reached no bucket and vanished from the
        accounting entirely. It read as clean only because no row in the real document
        was ambiguous yet.
        """
        return len(self.rights) + len(self.imprecise) + len(self.ambiguous) + len(self.unparsed)

    @property
    def with_dates(self) -> tuple[AttachedRight, ...]:
        return tuple(r for r in self.rights if r.priority_date is not None)

    @property
    def without_dates(self) -> tuple[AttachedRight, ...]:
        return tuple(r for r in self.rights if r.priority_date is None)


def band_for(priority: date | None, *, year_only: int | None = None) -> DateBand:
    """Which stated band a priority falls in. Never guesses past what is stated.

    A YEAR alone can still settle this, but only when every day in that year lands on
    the same side of both boundaries. 1977 does, because the whole year is junior to
    January 1 1958. 1912 does not, because November 25 falls inside it, and 1958 does
    not for the same reason. Those years return undetermined rather than a coin flip
    that decides whether a ranch irrigates.
    """
    if priority is None and year_only is not None:
        first, last = date(year_only, 1, 1), date(year_only, 12, 31)
        bands = {band_for(first), band_for(last)}
        return bands.pop() if len(bands) == 1 else DateBand.UNDETERMINED_NO_DATE
    if priority is None:
        return DateBand.UNDETERMINED_NO_DATE
    if priority < SHASTA_SENIOR_BOUND:
        return DateBand.SENIOR_TO_THE_BAND
    if priority >= SHASTA_ADDITIONAL_CONDITIONS_FROM:
        return DateBand.CONDITIONAL_WITH_ADDITIONAL_CONDITIONS
    return DateBand.CONDITIONAL


def parse_attachment(pages: dict[int, str]) -> AttachmentReport:
    """Parse the attachment from per-page layout text.

    Keyed by page so every row can say where it came from, which is what a citation
    needs and what makes a disputed row checkable against the original.

    Rows are anchored on the APPLICATION NUMBER rather than on the owner name. That is
    not only the privacy-preserving choice: the printed table leaves the owner cell
    blank on continuation rows, so owner-anchoring would lose them.
    """
    rights: list[AttachedRight] = []
    unparsed: list[str] = []
    blank_source: list[str] = []
    imprecise: list[str] = []
    ambiguous: list[str] = []
    recovered_from_neighbour: list[str] = []
    seen: set[str] = set()
    encountered: set[str] = set()

    for page in sorted(pages):
        lines = pages[page].splitlines()
        for index, line in enumerate(lines):
            match = APPLICATION_NUMBER.search(line)
            if match is None:
                continue

            number = match.group("id")
            # Counted BEFORE any outcome is decided, and this is the ground truth the
            # accounting reconciles against. Deriving the total from the outcomes
            # instead made the reconciliation a sum compared to itself: it could not
            # fail, and it hid a whole category that reached no bucket at all.
            encountered.add(number)
            # The Board reissues attachments, and a duplicate would double a right's
            # weight in any count computed off this table.
            if number in seen:
                continue

            cell = _read_cell(line[match.end() :])
            source = cell.source

            if cell.empty:
                # The ID sits alone on its line, which happens whenever the owner name
                # wraps to two lines and pushes the rest of the row up or down. Without
                # this, four real rows were dropped, one of them a water conservation
                # district that is a named petitioner in a live reconsideration
                # proceeding: exactly the row you cannot afford to lose.
                #
                # ONLY the date is recovered from a neighbour. The source is left blank
                # and reported, because a neighbouring line holds the owner name and a
                # positional grab would capture it into a stored field. This module does
                # not keep owner names, and the way to guarantee that is not to read
                # them in the first place.
                # EXACTLY ONE candidate, or the row is left unparsed. Excluding
                # neighbours that carry their own application number is not enough:
                # a wrapped row's date can sit on a line that carries no ID either, so
                # a first-hit-wins loop could attach one right's priority to another.
                # A confident wrong seniority is worse than an auditable parse failure,
                # and priority seniority is the entire doctrine.
                candidates = [
                    found
                    for found in (_read_cell(n) for n in _neighbours(lines, index))
                    if not found.empty or found.range_as_printed
                ]
                if len(candidates) == 1:
                    cell = candidates[0]
                    source = ""
                    recovered_from_neighbour.append(number)
                elif len(candidates) > 1:
                    ambiguous.append(
                        f"{number} has a readable priority on more than one adjacent "
                        "line and no way to prove which row owns it"
                    )
                    continue

            if cell.range_as_printed:
                # A range is a real statement, just not one that orders a right against
                # its neighbours. Named and set aside, never narrowed to an endpoint.
                imprecise.append(f"{number} states a range, {cell.range_as_printed!r}")
                continue

            if cell.empty:
                # Neither a date, a year, a range, nor the Board's own missing marker.
                # Recorded WITHOUT the line, because the line contains the owner name.
                unparsed.append(number)
                continue

            source = _clean_source(source)
            if not source:
                blank_source.append(number)

            seen.add(number)
            rights.append(
                AttachedRight(
                    application_number=number,
                    source_as_printed=source,
                    priority_date=cell.day,
                    priority_date_missing=cell.missing,
                    priority_year_only=cell.year,
                    band=band_for(cell.day, year_only=cell.year),
                    page=page,
                )
            )

    return AttachmentReport(
        application_numbers_seen=len(encountered),
        rights=tuple(rights),
        unparsed=tuple(unparsed),
        blank_source=tuple(blank_source),
        imprecise=tuple(imprecise),
        ambiguous=tuple(ambiguous),
        recovered_from_neighbour=tuple(recovered_from_neighbour),
    )


@dataclass(frozen=True, slots=True)
class _Cell:
    """What one date cell says. `empty` means it said nothing readable at all."""

    day: date | None = None
    year: int | None = None
    missing: bool = False
    source: str = ""
    #: A year range read verbatim. Never resolved to a year, and deliberately does NOT
    #: make the cell non-empty, so the row is reported rather than placed.
    range_as_printed: str = ""

    @property
    def empty(self) -> bool:
        return self.day is None and self.year is None and not self.missing


def _read_cell(region: str) -> _Cell:
    """Read a priority cell, preferring the most precise reading available."""
    printed = _PRINTED_DATE.search(region)
    if printed is not None:
        return _Cell(
            day=date(int(printed.group("y")), int(printed.group("m")), int(printed.group("d"))),
            source=region[: printed.start()],
        )
    if MISSING_DATE in region:
        return _Cell(missing=True, source=region[: region.index(MISSING_DATE)])
    span = _PRINTED_YEAR_RANGE.search(region)
    if span is not None:
        return _Cell(range_as_printed=span.group(0).strip())
    year = _PRINTED_YEAR_ONLY.search(region)
    if year is not None:
        return _Cell(year=int(year.group("y")), source=region[: year.start()])
    return _Cell()


def _neighbours(lines: list[str], index: int) -> list[str]:
    """The line above and the line below, and only when they hold no ID of their own.

    A neighbour carrying its own application number belongs to a different right, and
    borrowing its date would attach one right's priority to another. Getting that wrong
    is worse than dropping the row, because it produces a confident wrong seniority.
    """
    out = []
    for offset in (-1, 1):
        position = index + offset
        if 0 <= position < len(lines) and APPLICATION_NUMBER.search(lines[position]) is None:
            out.append(lines[position])
    return out


def _clean_source(raw: str) -> str:
    """Strip the footnote markers the Board prints against source names.

    "Groundwater 1" and "Groundwater1" both mean groundwater with footnote 1 attached.
    The digit is a reference mark, not part of the source name, and leaving it in would
    split one source into several.
    """
    # Stripped BEFORE the substitution, not after. The cell arrives padded, so `\d+$`
    # never reached the footnote digit and "Groundwater 1" stayed distinct from
    # "Groundwater1", splitting one source into two groups where monotonicity is
    # checked per source.
    return re.sub(r"\s*\d+$", "", raw.strip()).strip()


def decree_membership(priority: date | None, *, year_only: int | None, decree: date) -> bool | None:
    """Whether a right predates the decree. None means the record cannot say.

    A right whose priority precedes the Shasta decree cannot have been initiated after
    it, which is what separates Tier B from Tier A. A YEAR can settle this too, but
    only when the whole year falls on one side: 1977 does, 1932 does not, because the
    decree date falls inside it.
    """
    if priority is not None:
        return priority < decree
    if year_only is not None:
        first, last = date(year_only, 1, 1), date(year_only, 12, 31)
        if (first < decree) == (last < decree):
            return first < decree
    return None


def to_water_rights(report: AttachmentReport) -> ConversionResult:
    """Convert parsed rows into the record the priority ladder consumes.

    **A row whose decree membership cannot be established is NOT converted**, and that
    is the whole design of this function. `WaterRight.adjudication = None` is a positive
    claim, that the right is in none of the four decrees, and the Shasta ladder reads it
    as positive evidence for Tier A, returning the reason "appropriative diversion
    initiated after the Shasta Adjudication" under 23 CCR 875.5(b)(1)(A).

    The first version of this function passed None for every row with no date. Fourteen
    rights the document says nothing about were therefore placed in the FIRST grouping
    curtailed, carrying a statutory citation that made the guess look authoritative. The
    ladder itself had already been hardened against exactly this, its comment reading
    "Positive evidence only", and this converter reintroduced the same defect from the
    other side by feeding it a confident None. A missing-date flag rides along but it is
    a data-quality note, not a brake: it does not stop the placement or the sentence.

    So unresolved rows come back named, in `unplaceable`, and nothing asserts anything
    about them.

    What the document DOES state, and is therefore not a guess: Addendum 6's scope
    sentence puts every right in this attachment in the appropriative class, surface or
    groundwater, under 23 CCR 875.5(b)(1)(A) and (b)(1)(B). So `right_class` is read
    from the document rather than inferred from the application prefix.
    """
    decree = ADJUDICATIONS[AdjudicationId.SHASTA].decree_date
    rights: list[WaterRight] = []
    unplaceable: list[str] = []
    open_questions: list[str] = []

    for row in report.rights:
        in_decree = decree_membership(
            row.priority_date, year_only=row.priority_year_only, decree=decree
        )
        if in_decree is None:
            unplaceable.append(
                f"{row.application_number}: the record states no priority precise enough "
                "to say whether the right predates the Shasta decree, so neither tier "
                "can be asserted"
            )
            continue
        rights.append(
            WaterRight(
                right_id=row.application_number,
                basin=Basin.SHASTA,
                right_class=RightClass.APPROPRIATIVE,
                adjudication=AdjudicationId.SHASTA if in_decree else None,
                priority_date=row.priority_date,
                priority_date_missing=row.priority_date_missing,
                source=row.source_as_printed,
            )
        )

    if unplaceable:
        open_questions.append(
            f"{len(unplaceable)} of {len(report.rights)} parsed rows are not placed on "
            "the ladder at all, because the record states no priority precise enough to "
            "establish decree membership. They are named rather than defaulted: passing "
            "adjudication=None would assert they were initiated after the Shasta "
            "Adjudication and put them in the first grouping curtailed."
        )
    year_only = sum(1 for r in report.rights if r.priority_year_only is not None)
    if year_only:
        open_questions.append(
            f"{year_only} rows state a year with no month or day. The year is used only "
            "where it settles the question on its own, and is never widened into a date."
        )
    undated = sum(1 for r in report.rights if r.priority_date_missing)
    if undated:
        open_questions.append(
            f"{undated} of {len(report.rights)} rows state no priority date. The table "
            "prints them as '--'. They are carried as missing rather than imputed, "
            "because riparian and pre-1914 claims are self-reported Statements whose "
            "dates are not verified as a condition of the record existing."
        )
    if report.blank_source:
        open_questions.append(
            f"{len(report.blank_source)} rows have a blank Source cell, carried down "
            "visually in the printed table. Not filled from the row above: layout text "
            "gives no cell boundaries, and a wrong source merges unrelated rights into "
            "one shared-source group where monotonicity is checked."
        )
    if report.ambiguous:
        open_questions.append(
            f"{len(report.ambiguous)} rows had a readable priority on more than one "
            "adjacent line, with no way to prove which row owns it."
        )
    if report.unparsed:
        open_questions.append(
            f"{len(report.unparsed)} rows carried an application number with neither a "
            f"date nor the '--' marker: {', '.join(report.unparsed)}"
        )
    open_questions.extend(report.unrecoverable)

    return ConversionResult(
        rights=tuple(rights),
        unplaceable=tuple(unplaceable),
        open_questions=tuple(open_questions),
    )
