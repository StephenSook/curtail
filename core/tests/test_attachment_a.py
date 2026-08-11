"""The Attachment A parser, against a fixture that reproduces every shape the real
table actually contains.

**The fixture is hand-authored with placeholder owners on purpose.** The real
attachment's first column is real private parties, several of them named in live
reconsideration proceedings, and this repository is public. Committing extracted text
would put those names in git history permanently. Column positions and every structural
oddity are reproduced faithfully; only the names are invented, and they are obviously
invented.

Every shape below was found in the real document, not imagined:

- a plain row, owner and source and date on one line
- a row whose priority is printed as the Board's own "--"
- a run of groundwater rows where the Source cell is carried down visually and is
  therefore BLANK on every row after the first
- a row whose owner name wraps to two lines, leaving the application number alone on
  its own line with its date on the line above (four real rows, one of them a water
  conservation district that is a named petitioner)
- a year with no month or day, printed "1977 -"
- a year RANGE, printed "1970 to 1981"
- a row whose priority cell is simply blank, not even "--"
"""

from __future__ import annotations

from datetime import date

import pytest

from curtail_core.adjudications import RightClass
from curtail_core.attachment_a import (
    AttachmentReport,
    DateBand,
    band_for,
    decree_membership,
    parse_attachment,
    to_water_rights,
)
from curtail_core.basins import Basin
from curtail_core.priority import place

PAGE = """\
               Updated Attachment to Addendum 6 of Order WR 2024-0006-DWR

                                      Application                                      Curtailment
             Primary Owner                                Source       Priority Date
                                       Number                                            Status*
                                                         UNNAMED                       Conditionally
            FIRST PLACEHOLDER          A031522                          7/30/2003
                                                          STREAM                        Curtailed
                                                                                       Conditionally
           SECOND PLACEHOLDER          D031166             UNST         1/17/2001
                                                                                        Curtailed
                                                                                            Conditionally
            THIRD PLACEHOLDER          SG005922       Groundwater 1             --
                                                                                             Curtailed
                                                                                            Conditionally
           FOURTH PLACEHOLDER          SG005923                                 --
                                                                                             Curtailed
    FIFTH PLACEHOLDER WRAPS                         Groundwater1    8/21/2014      Conditionally
                                      SG005432
                 CONTINUED                                                                 Curtailed
                                                                                            Conditionally
            SIXTH PLACEHOLDER          SG005441                             1977 -
                                                                                             Curtailed
                                                                                            Conditionally
          SEVENTH PLACEHOLDER          SG005439                          1970 to 1981
                                                                                             Curtailed
                                                                                            Conditionally
           EIGHTH PLACEHOLDER          SG005440           Groundwater1
                                                                                             Curtailed
"""


@pytest.fixture(scope="module")
def report() -> AttachmentReport:
    return parse_attachment({6: PAGE})


class TestEveryRowIsAccountedFor:
    """A parser that returns only its successes cannot be audited.

    The real table holds 87 unique application numbers and this parser reads 85,
    naming the other two and why. That is a usable result. Reading 85 and saying
    nothing would be the same output wearing a claim of completeness.
    """

    def test_nothing_is_silently_dropped(self, report: AttachmentReport) -> None:
        """Measured against the parser's own count of application numbers encountered,
        which is taken before any outcome is decided, so this cannot become a sum
        compared to itself."""
        assert report.application_numbers_seen == 8, "the fixture contains 8 application numbers"
        assert report.accounted_for == report.application_numbers_seen, (
            f"{report.application_numbers_seen} seen but {report.accounted_for} reached "
            "a bucket, so rows are vanishing between the page and the report"
        )

    def test_the_ordinary_rows_are_read(self, report: AttachmentReport) -> None:
        by_id = {r.application_number: r for r in report.rights}
        assert by_id["A031522"].priority_date == date(2003, 7, 30)
        assert by_id["D031166"].priority_date == date(2001, 1, 17)
        assert by_id["D031166"].source_as_printed == "UNST"

    def test_a_source_name_that_wraps_is_reported_blank_rather_than_halved(self) -> None:
        """ "UNNAMED STREAM" is printed across two lines, so the row's own line carries
        no source at all. Reading the visible half would record the source as "STREAM",
        which is a different thing from an unnamed stream and would group this right
        with any other row that happened to say STREAM."""
        report = parse_attachment({6: PAGE})
        by_id = {r.application_number: r for r in report.rights}
        assert by_id["A031522"].source_as_printed == ""
        assert "A031522" in report.blank_source

    def test_the_boards_own_missing_marker_is_carried_as_missing(
        self, report: AttachmentReport
    ) -> None:
        """Never imputed. Riparian and pre-1914 claims are self-reported Statements
        whose dates are not verified as a condition of the record existing, and
        substituting an application acceptance date would invent a seniority."""
        by_id = {r.application_number: r for r in report.rights}
        assert by_id["SG005922"].priority_date is None
        assert by_id["SG005922"].priority_date_missing is True
        assert by_id["SG005922"].band is DateBand.UNDETERMINED_NO_DATE

    def test_a_blank_source_is_left_blank_and_reported(self, report: AttachmentReport) -> None:
        """The printed table carries a Source down a block visually. Layout text gives
        no cell boundaries to prove where the block ends, and a wrong source merges
        unrelated rights into one shared-source group, which is where monotonicity is
        checked."""
        by_id = {r.application_number: r for r in report.rights}
        assert by_id["SG005923"].source_as_printed == ""
        assert "SG005923" in report.blank_source

    def test_the_footnote_marker_is_not_part_of_the_source_name(
        self, report: AttachmentReport
    ) -> None:
        """ "Groundwater 1" and "Groundwater1" are one source with a reference mark, and
        keeping the digit splits one source into several."""
        by_id = {r.application_number: r for r in report.rights}
        assert by_id["SG005922"].source_as_printed == "Groundwater"


class TestTheShapesThatBreakNaiveParsers:
    def test_a_wrapped_owner_name_does_not_lose_the_row(self, report: AttachmentReport) -> None:
        """The owner wraps to two lines, so the application number sits alone and its
        date is on the line above. Four real rows land here, one of them a water
        conservation district that is a named petitioner in a live proceeding."""
        by_id = {r.application_number: r for r in report.rights}
        assert "SG005432" in by_id, "a wrapped owner name dropped a real right"
        assert by_id["SG005432"].priority_date == date(2014, 8, 21)
        assert "SG005432" in report.recovered_from_neighbour

    def test_a_recovered_row_takes_no_source_from_its_neighbour(
        self, report: AttachmentReport
    ) -> None:
        """Only the date is recovered. The neighbouring line holds the owner name, and a
        positional grab would capture it into a stored field. The guarantee that owner
        names are never kept is that they are never read."""
        by_id = {r.application_number: r for r in report.rights}
        assert by_id["SG005432"].source_as_printed == ""
        assert "PLACEHOLDER" not in str(by_id["SG005432"])

    def test_a_year_alone_is_kept_as_a_year(self, report: AttachmentReport) -> None:
        by_id = {r.application_number: r for r in report.rights}
        assert by_id["SG005441"].priority_year_only == 1977
        assert by_id["SG005441"].priority_date is None
        assert by_id["SG005441"].priority_date_missing is False, (
            "a stated year is imprecise, not missing, and conflating them loses that"
        )

    def test_a_year_range_is_refused_rather_than_collapsed(self, report: AttachmentReport) -> None:
        """The defect this test exists for was live for one run: the year-only pattern
        matches the END of a range, so "1970 to 1981" silently became a 1981 priority.
        Either endpoint writes a seniority the record does not state into the basis of
        a signed order."""
        assert not any(r.application_number == "SG005439" for r in report.rights)
        assert any("SG005439" in note and "1970 to 1981" in note for note in report.imprecise)

    def test_a_blank_priority_cell_is_named_not_guessed(self, report: AttachmentReport) -> None:
        assert "SG005440" in report.unparsed


class TestTheBandsComeFromTheDocumentsOwnRule:
    """The status column wraps across lines and the additional-conditions subset is
    encoded in ORANGE, so neither is read. Disposition comes from the priority-date
    bands the body states in words."""

    def test_the_two_stated_boundaries(self) -> None:
        assert band_for(date(1912, 11, 24)) is DateBand.SENIOR_TO_THE_BAND
        assert band_for(date(1912, 11, 25)) is DateBand.CONDITIONAL
        assert band_for(date(1957, 12, 31)) is DateBand.CONDITIONAL
        assert band_for(date(1958, 1, 1)) is DateBand.CONDITIONAL_WITH_ADDITIONAL_CONDITIONS

    def test_a_year_settles_the_band_only_when_it_cannot_straddle_a_boundary(self) -> None:
        assert band_for(None, year_only=1977) is DateBand.CONDITIONAL_WITH_ADDITIONAL_CONDITIONS
        assert band_for(None, year_only=1940) is DateBand.CONDITIONAL
        # 1958 does NOT straddle: the boundary is January 1, so every day of that year
        # is on one side of it. 1912 is the only straddling year in either boundary,
        # because November 25 falls inside it, and a coin flip there decides whether a
        # ranch irrigates.
        assert band_for(None, year_only=1958) is DateBand.CONDITIONAL_WITH_ADDITIONAL_CONDITIONS
        assert band_for(None, year_only=1912) is DateBand.UNDETERMINED_NO_DATE

    def test_the_colour_only_encoding_is_reported_as_unrecoverable(
        self, report: AttachmentReport
    ) -> None:
        """Addendum 6 says the additional-conditions rights "are listed in orange in the
        Attachment". A legally operative distinction carried by colour alone cannot be
        recovered from text, and claiming otherwise would be the fabrication."""
        assert any("orange" in note for note in report.unrecoverable)


class TestConversionToTheLadder:
    def test_class_is_read_from_the_document_not_from_the_prefix(
        self, report: AttachmentReport
    ) -> None:
        """Addendum 6's scope sentence puts every right in this attachment in the
        appropriative class, surface or groundwater, under 875.5(b)(1)(A) and (B). That
        is stated, so it is not an inference from the SG prefix."""
        rights = to_water_rights(report).rights
        assert rights
        assert all(r.right_class is RightClass.APPROPRIATIVE for r in rights)
        assert all(r.basin is Basin.SHASTA for r in rights)

    @pytest.mark.parametrize(
        ("priority", "year", "expected"),
        [
            # Hardcoded, NOT recomputed from the implementation's own comparison. The
            # first version built its expected set with the same `< date(1932, 12, 29)`
            # expression the code uses, so a wrong boundary would have satisfied both.
            (date(1932, 12, 28), None, True),
            (date(1932, 12, 29), None, False),  # the decree date itself is not before it
            (date(1932, 12, 30), None, False),
            (date(1912, 11, 25), None, True),
            (date(2003, 7, 30), None, False),
            (None, 1977, False),  # every day of 1977 is after the decree
            (None, 1900, True),  # every day of 1900 is before it
            (None, 1932, None),  # the decree date falls inside 1932, so it cannot say
            (None, None, None),  # no priority at all says nothing
        ],
    )
    def test_decree_membership_is_established_or_refused(
        self, priority: date | None, year: int | None, expected: bool | None
    ) -> None:
        assert decree_membership(priority, year_only=year, decree=date(1932, 12, 29)) is expected

    def test_a_row_with_no_establishable_membership_is_not_placed_at_all(
        self, report: AttachmentReport
    ) -> None:
        """The critical finding from an adversarial pass, and it was real.

        `adjudication=None` is a positive claim that a right is in none of the four
        decrees, and the Shasta ladder reads it as evidence for Tier A, returning
        "appropriative diversion initiated after the Shasta Adjudication" under
        875.5(b)(1)(A). Passing None for every undated row put fourteen rights the
        document says nothing about into the FIRST grouping curtailed, with a citation
        that made the guess look authoritative.
        """
        converted = to_water_rights(report)
        placed = {r.right_id for r in converted.rights}
        assert "SG005922" not in placed, "an undated right was placed on the ladder"
        assert "SG005923" not in placed
        assert any("SG005922" in note for note in converted.unplaceable)
        assert all(
            r.priority_date is not None or r.right_id == "SG005441" for r in converted.rights
        )

    def test_a_year_only_row_is_placed_when_the_year_settles_it(
        self, report: AttachmentReport
    ) -> None:
        """1977 lies wholly after the 1932 decree, so membership IS established. The
        refusal above is about what cannot be known, not about imprecision as such."""
        converted = to_water_rights(report)
        by_id = {r.right_id: r for r in converted.rights}
        assert "SG005441" in by_id
        assert by_id["SG005441"].adjudication is None
        assert by_id["SG005441"].priority_date is None

    def test_every_converted_right_places_on_the_ladder(self, report: AttachmentReport) -> None:
        """The point of the whole module. Before this the Core had only ever run on
        rights invented in tests."""
        rights = to_water_rights(report).rights
        assert rights
        for right in rights:
            placement = place(right)
            assert placement.rank >= 1
            assert placement.citation.startswith("23 CCR 875.5")

    def test_the_open_questions_are_stated_with_counts(self, report: AttachmentReport) -> None:
        """Recording what was NOT read, with a number, is what makes the result usable
        rather than merely confident."""
        questions = to_water_rights(report).open_questions
        assert questions
        assert any("no priority date" in q for q in questions)
        assert any("blank Source" in q for q in questions)
        assert any("orange" in q for q in questions)

    def test_the_undated_count_does_not_include_year_only_rows(
        self, report: AttachmentReport
    ) -> None:
        """The first version counted them together and said the table "prints them as
        '--'", which is untrue of a row printed "1977 -". A count in a sentence is a
        claim like any other."""
        questions = to_water_rights(report).open_questions
        undated = [q for q in questions if "state no priority date" in q]
        assert len(undated) == 1
        assert undated[0].startswith("2 of "), undated[0]
