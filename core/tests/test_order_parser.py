"""The order parser is tested against verbatim text from the real documents.

Every fixture below is copied character for character out of a document fetched
from the State Water Board on 2026-08-10. None of it is paraphrased, and none of
the expected values were written from memory.

That rule exists because of a specific incident. A retired figure of 39.3 cfs,
which appears nowhere in Shasta Addendum 6, propagated out of a research haul
into the constitution, two test files, the corpus manifest and a public README. It
survived a dedicated verification pass and a passing test suite. Only opening the
PDF caught it: the document says 45.3 and 46.5. A test whose expected value came
from the same contaminated source as the code proves nothing at all.

Fixtures are embedded rather than read from `data/corpus/`, which is gitignored,
so these tests run identically in CI and never skip. A conditionally skipped test
is a false green.
"""

from __future__ import annotations

import pytest

from curtail_core.order_parser import (
    MIN_USABLE_TEXT_CHARS,
    Extraction,
    ExtractionMethod,
    OrderAction,
    SuspensionQualifier,
    extract,
)

# Verbatim headers. Line breaks preserved where the source wrapped.
SCOTT_BASE_ORDER = """State Water Resources Control Board
July 23, 2024
DELIVERY CONFIRMATION
<<MAIL_RECEIVER_NAME>>
<<Line1>>                                     DELIVERY MAIL NO:
                                              <<Delivery_Confirmation_No>>
<<Line2>>
ORDER IMPOSING WATER RIGHT CURTAILMENT AND REQUIRING REPORTING
Order WR 2024-0024-DWR, Scott River watershed. Flows at the Fort Jones gage
measured 46.1 cfs against a minimum of 50 cfs.
"""

SHASTA_BASE_ORDER = """State Water Resources Control Board
June 7, 2024
DELIVERY CONFIRMATION
 <<MAIL_RECEIVER_NAME>>
 <<BOTTOM_ADDRESS_LINE>>                          DELIVERY MAIL NO:
ORDER IMPOSING CONDITIONAL WATER RIGHT CURTAILMENT AND
REQUIRING REPORTING
Shasta River watershed, minimum flow of 50 cfs at the compliance gage.
"""

SCOTT_ADDENDUM_10 = """Subject: Scott River Watershed: Limited Conditional Suspension of Curtailments
for Group 8 Surface Water Rights- (Addendum 10)
To: Scott River Water Right Holders
Flows at the Fort Jones gage are 162 cfs against the 150 cfs minimum.
"""

SCOTT_ADDENDUM_11 = """Subject: Scott River Watershed: Conditional Suspension of Curtailments for All
Surface Water Rights- (Addendum 11)
To: Scott River Water Right Holders
Flows measured 167 cfs against the 150 cfs minimum.
"""

SCOTT_ADDENDUM_12 = """Subject: Scott River Watershed: Reinstatement of Curtailments for All Surface
Water Rights (Groups 1-8) - Addendum 12
To: Scott River Water Right Holders
Flows have fallen to 119 cfs against the 125 cfs minimum.
"""

#: Note the en-dash after "Addendum 6". It is the Board's own typography and is
#: preserved verbatim, because a fixture retyped in cleaner punctuation is no
#: longer evidence of what the parser will meet in production.
SCOTT_ADDENDUM_6 = """From:          WB-DWR-ScottShastaDrought
To:            WB-DWR-ScottShastaDrought
Subject:       Addendum 6 – Full Suspension of Curtailments in Scott River Watershed
Date:          Wednesday, November 13, 2024 4:03:21 PM

                         Having trouble viewing this? View it as a webpage

California State Water Resources Control Board (SWRCB)

   Addendum 6 – Full Suspension of Curtailments in
   Scott River Watershed

  To:         Scott River Water Right Holders (sent to water right holders for which the
  Board has email addresses and to the Scott-Shasta Drought E-mail List)

  Based on the current flow and upward trend of flows at the United States
  Geological Survey (USGS) Fort Jones gage, precipitation and flow forecasts, and
  available water demand information, this addendum fully suspends curtailments
  in the Scott River watershed. Flows exceed 60 cfs at the Fort Jones gage.
"""

SCOTT_ADDENDUM_1 = """Addendum 1 - Temporary and Conditional Suspension to Scott River Curtailments Orders

California Water Boards < public@info.waterboards.ca.gov>
Fri 8/23/2024 4:24 PM
Based on the forecast of precipitation this weekend, flows of 33 cfs.
"""

SHASTA_ADDENDUM_2 = """State Water Resources Control Board
October 3, 2024
Subject: Order WR 2024-0006-DWR, Addendum 2: Temporary Suspension of All
         Curtailments in Shasta River Watershed through October 31, 2024
To:      Shasta River Watershed Water Right Holders
Flows measured 126 cfs against the 105 cfs minimum.
"""

SHASTA_ADDENDUM_3 = """State Water Resources Control Board
November 1, 2024
Subject: Order WR 2024-0006-DWR, Addendum 3: Suspension of All Curtailments
         in Shasta River Watershed
To:      Shasta River Watershed Water Right Holders
Flows measured 140 cfs against the 125 cfs minimum.
"""

SHASTA_ADDENDUM_6 = """State Water Resources Control Board
June 16, 2026
Subject: Order WR 2024-0006-DWR, Addendum 6: Reinstatement of Conditional
         Curtailments for Junior Water Rights in Shasta River Watershed
To:      Shasta River Watershed Water Right Holders
The priority cutoff is November 25, 1912. Flows measured 46.5 cfs on June 14
and 45.3 cfs on June 15, against the minimum of 50 cfs.
"""

SCOTT_ADDENDUM_5 = """- Outlook
Update to Scott River Surface Water Curtailments for Farmer's Ditch Company and Scott Valley
Irrigation District
From California Water Boards <public@info.waterboards.ca.gov>
Date Tue 10/15/2024 1:24 PM
"""

#: What `pdftotext` actually returns for Scott Addendum 8, the July 22 2025
#: suspension. Four bytes. The document is a pure scan with no text layer.
SCANNED_NO_TEXT_LAYER = "\x0c\n\x0c\n"


class TestScansAreRefusedNotGuessed:
    """The single most important behaviour in this module.

    Four of the 25 documents fetched carry no text layer at all. One of them is
    Scott Addendum 8, the July 22 2025 suspension that followed Watermaster field
    measurements, which is the project's central fixture. Producing a plausible
    action for a document nothing has read would be the exact failure this
    codebase exists to prevent.
    """

    def test_a_scan_reports_that_it_requires_ocr(self) -> None:
        result = extract(SCANNED_NO_TEXT_LAYER)
        assert result.method is ExtractionMethod.REQUIRES_OCR

    def test_a_scan_yields_no_action(self) -> None:
        assert extract(SCANNED_NO_TEXT_LAYER).action is OrderAction.UNDETERMINED

    def test_a_scan_yields_no_values_of_any_kind(self) -> None:
        """Not merely an undetermined action. No cfs, no dates, no groups."""
        result = extract(SCANNED_NO_TEXT_LAYER)
        assert result.cfs_values == ()
        assert result.priority_dates == ()
        assert result.priority_groups == ()
        assert result.affects_all is False

    def test_a_scan_is_never_scorable(self) -> None:
        assert extract(SCANNED_NO_TEXT_LAYER).scorable is False

    def test_a_scan_says_why_in_its_notes(self) -> None:
        notes = " ".join(extract(SCANNED_NO_TEXT_LAYER).notes)
        assert "text layer" in notes.lower()

    def test_the_threshold_is_a_real_boundary(self) -> None:
        """Just under refuses, just over attempts. Guards against the constant
        being raised so high that real documents are silently discarded."""
        assert extract("x" * (MIN_USABLE_TEXT_CHARS - 1)).method is ExtractionMethod.REQUIRES_OCR
        assert extract("x" * (MIN_USABLE_TEXT_CHARS + 1)).method is ExtractionMethod.TEXT_LAYER


class TestTheVerbIsReadFromTheDocument:
    @pytest.mark.parametrize(
        ("name", "text", "expected"),
        [
            ("scott base order", SCOTT_BASE_ORDER, OrderAction.IMPOSE),
            ("shasta base order", SHASTA_BASE_ORDER, OrderAction.IMPOSE),
            ("scott addendum 10", SCOTT_ADDENDUM_10, OrderAction.SUSPEND),
            ("scott addendum 11", SCOTT_ADDENDUM_11, OrderAction.SUSPEND),
            ("scott addendum 12", SCOTT_ADDENDUM_12, OrderAction.REINSTATE),
            ("scott addendum 6", SCOTT_ADDENDUM_6, OrderAction.SUSPEND),
            ("scott addendum 1", SCOTT_ADDENDUM_1, OrderAction.SUSPEND),
            ("scott addendum 5", SCOTT_ADDENDUM_5, OrderAction.AMEND),
            ("shasta addendum 2", SHASTA_ADDENDUM_2, OrderAction.SUSPEND),
            ("shasta addendum 3", SHASTA_ADDENDUM_3, OrderAction.SUSPEND),
            ("shasta addendum 6", SHASTA_ADDENDUM_6, OrderAction.REINSTATE),
        ],
    )
    def test_verb(self, name: str, text: str, expected: OrderAction) -> None:
        assert extract(text).action is expected, name

    def test_the_base_order_title_is_found_below_the_address_block(self) -> None:
        """A first-three-lines heuristic misses it.

        Every base order buries `ORDER IMPOSING ...` under a mail-merge address
        block around line 8. This was a real parser failure: all five base orders
        came back undetermined until the header window was widened.
        """
        assert extract(SCOTT_BASE_ORDER).action is OrderAction.IMPOSE

    def test_shasta_conditional_inserted_mid_phrase_still_reads_as_impose(self) -> None:
        """Shasta writes IMPOSING CONDITIONAL WATER RIGHT CURTAILMENT.

        The word CONDITIONAL sits between IMPOSING and WATER, which defeated a
        pattern requiring the two to be adjacent.
        """
        assert extract(SHASTA_BASE_ORDER).action is OrderAction.IMPOSE


class TestTheQualifierIsNeverInvented:
    """The Board qualifies suspensions five different ways, and sometimes not at all."""

    @pytest.mark.parametrize(
        ("name", "text", "expected"),
        [
            ("limited conditional", SCOTT_ADDENDUM_10, SuspensionQualifier.LIMITED_CONDITIONAL),
            ("conditional", SCOTT_ADDENDUM_11, SuspensionQualifier.CONDITIONAL),
            ("full", SCOTT_ADDENDUM_6, SuspensionQualifier.FULL),
            (
                "temporary and conditional",
                SCOTT_ADDENDUM_1,
                SuspensionQualifier.TEMPORARY_CONDITIONAL,
            ),
            ("temporary", SHASTA_ADDENDUM_2, SuspensionQualifier.TEMPORARY),
            ("unqualified", SHASTA_ADDENDUM_3, SuspensionQualifier.UNQUALIFIED),
        ],
    )
    def test_qualifier(self, name: str, text: str, expected: SuspensionQualifier) -> None:
        assert extract(text).qualifier is expected, name

    def test_an_unqualified_suspension_is_not_upgraded_to_conditional(self) -> None:
        """Shasta Addendum 3 is titled "Suspension of All Curtailments".

        An earlier parser mapped every unmatched suspension to CONDITIONAL, which
        asserted a legal characterisation the Board never wrote. Conditional
        suspensions carry conditions that can fail and reinstate curtailment;
        an unconditional one does not. The two are not interchangeable.
        """
        assert extract(SHASTA_ADDENDUM_3).qualifier is not SuspensionQualifier.CONDITIONAL

    def test_a_temporary_suspension_is_not_recorded_as_conditional(self) -> None:
        assert extract(SHASTA_ADDENDUM_2).qualifier is SuspensionQualifier.TEMPORARY

    def test_limited_conditional_is_not_flattened_to_conditional(self) -> None:
        """The difference is one priority grouping versus every grouping."""
        assert extract(SCOTT_ADDENDUM_10).qualifier is SuspensionQualifier.LIMITED_CONDITIONAL

    @pytest.mark.parametrize(
        "text", [SCOTT_BASE_ORDER, SHASTA_BASE_ORDER, SCOTT_ADDENDUM_12, SHASTA_ADDENDUM_6]
    )
    def test_no_qualifier_is_attached_to_a_non_suspension(self, text: str) -> None:
        assert extract(text).qualifier is SuspensionQualifier.NOT_APPLICABLE


class TestScopeSeparatesOneGroupingFromAllOfThem:
    def test_a_range_expands_to_every_group_it_covers(self) -> None:
        assert extract(SCOTT_ADDENDUM_12).priority_groups == (1, 2, 3, 4, 5, 6, 7, 8)

    def test_a_single_group_is_not_expanded(self) -> None:
        assert extract(SCOTT_ADDENDUM_10).priority_groups == (8,)

    def test_all_surface_water_rights_is_recorded_as_basin_wide(self) -> None:
        assert extract(SCOTT_ADDENDUM_11).affects_all is True

    def test_a_grouping_scoped_action_is_not_basin_wide(self) -> None:
        """Addendum 10 suspends for Group 8 only. Treating it as basin-wide
        would report that seven senior groupings were released when they were not."""
        assert extract(SCOTT_ADDENDUM_10).affects_all is False

    def test_all_curtailments_also_counts_as_basin_wide(self) -> None:
        assert extract(SHASTA_ADDENDUM_3).affects_all is True

    def test_a_range_separated_by_an_en_dash_is_read(self) -> None:
        """The Board's typography is not consistent across a decade of documents.

        Order WR 2026-0005-DWR writes "Priority Groups 1 - 8" with a spaced
        en-dash where the addenda write "Groups 1-8" with a hyphen. An en-dash is
        visually indistinguishable from a hyphen, so a separator dropped by a
        maintainer tidying punctuation would fail silently, and the failure mode
        is not an error, it is a document reporting no priority groupings at all.
        """
        headline = "Subject: Reinstatement of Curtailments for Groups 1 – 8"
        text = headline + "\n" + "body text. " * 40
        assert extract(text).priority_groups == (1, 2, 3, 4, 5, 6, 7, 8)

    def test_groups_named_in_the_body_are_not_attributed_to_this_document(self) -> None:
        """A real hazard, taken verbatim from Order WR 2026-0005-DWR, Finding 7.

        That finding reads: "the Deputy Director ... issued State Water Board
        Order 2024-0024-DWR, curtailing Priority Groups 1 - 8". The groupings
        belong to a DIFFERENT order that this one merely recites. Extraction is
        headline-scoped precisely so a recital cannot be attributed to the
        reciting document, which would report an order as curtailing groupings
        it never curtailed.
        """
        text = (
            "State Water Resources Control Board\n"
            "June 5, 2026\n"
            "ORDER IMPOSING WATER RIGHT CURTAILMENT AND REPORTING\n"
            "\n"
            "7. On July 23, 2024, the Deputy Director of the Division of Water Rights\n"
            "   issued State Water Board Order 2024-0024-DWR, curtailing Priority\n"
            "   Groups 1 – 8, which includes all the known surface water rights in\n"
            "   the Scott River watershed.\n"
        )
        result = extract(text)
        assert result.action is OrderAction.IMPOSE
        assert result.priority_groups == ()


class TestValuesComeFromTheDocument:
    def test_shasta_addendum_6_yields_the_figures_the_document_states(self) -> None:
        """The retired 39.3 regression test, run against the document's own words.

        45.3 and 46.5 are printed in the Addendum. The corrected record shows
        that 39.3 appears nowhere in it.
        """
        cfs = extract(SHASTA_ADDENDUM_6).cfs_values
        assert 45.3 in cfs
        assert 46.5 in cfs
        assert 39.3 not in cfs

    def test_the_1912_priority_cutoff_is_read_as_november_25(self) -> None:
        """Several research hauls carried November 1. The document says the 25th."""
        from datetime import date

        assert date(1912, 11, 25) in extract(SHASTA_ADDENDUM_6).priority_dates
        assert date(1912, 11, 1) not in extract(SHASTA_ADDENDUM_6).priority_dates

    def test_a_document_stating_no_discharge_yields_no_discharge(self) -> None:
        text = "Subject: Rescission of Curtailment Orders\n" + "filler. " * 40
        assert extract(text).cfs_values == ()


class TestTheFixtureSetIsNotVacuous:
    """Guards the tests themselves.

    A parametrised suite that silently stopped exercising a branch would keep
    passing while coverage of that branch vanished.
    """

    ALL_FIXTURES = (
        SCOTT_BASE_ORDER,
        SHASTA_BASE_ORDER,
        SCOTT_ADDENDUM_1,
        SCOTT_ADDENDUM_5,
        SCOTT_ADDENDUM_6,
        SCOTT_ADDENDUM_10,
        SCOTT_ADDENDUM_11,
        SCOTT_ADDENDUM_12,
        SHASTA_ADDENDUM_2,
        SHASTA_ADDENDUM_3,
        SHASTA_ADDENDUM_6,
    )

    def test_every_fixture_carries_a_real_text_layer(self) -> None:
        for text in self.ALL_FIXTURES:
            assert extract(text).method is ExtractionMethod.TEXT_LAYER

    def test_every_fixture_classifies(self) -> None:
        """If any fixture stops classifying, the suite must fail rather than
        quietly test fewer branches."""
        for text in self.ALL_FIXTURES:
            assert extract(text).action is not OrderAction.UNDETERMINED

    def test_the_fixtures_exercise_every_verb_the_corpus_contains(self) -> None:
        seen = {extract(t).action for t in self.ALL_FIXTURES}
        assert seen == {
            OrderAction.IMPOSE,
            OrderAction.REINSTATE,
            OrderAction.SUSPEND,
            OrderAction.AMEND,
        }

    def test_the_fixtures_exercise_every_suspension_qualifier(self) -> None:
        seen = {
            extract(t).qualifier
            for t in self.ALL_FIXTURES
            if extract(t).action is OrderAction.SUSPEND
        }
        assert seen == {
            SuspensionQualifier.FULL,
            SuspensionQualifier.CONDITIONAL,
            SuspensionQualifier.LIMITED_CONDITIONAL,
            SuspensionQualifier.TEMPORARY,
            SuspensionQualifier.TEMPORARY_CONDITIONAL,
            SuspensionQualifier.UNQUALIFIED,
        }

    def test_both_scope_states_are_represented(self) -> None:
        scopes = {extract(t).affects_all for t in self.ALL_FIXTURES}
        assert scopes == {True, False}


class TestScorability:
    def test_a_readable_classified_document_is_scorable(self) -> None:
        assert extract(SCOTT_ADDENDUM_12).scorable is True

    def test_a_readable_but_unclassified_document_is_not_scorable(self) -> None:
        """An extraction gap must not be scored as an engine disagreement.

        Conflating the two corrupts the headline metric in the flattering
        direction: every document the parser failed to read would be counted
        against the engine, or worse, silently counted in its favour.
        """
        text = "State Water Resources Control Board\n" + "unremarkable prose. " * 30
        result = extract(text)
        assert result.is_readable is True
        assert result.action is OrderAction.UNDETERMINED
        assert result.scorable is False

    def test_extraction_is_frozen(self) -> None:
        """A parsed result feeding a legal recommendation must not be mutable."""
        result = extract(SCOTT_ADDENDUM_12)
        with pytest.raises((AttributeError, TypeError)):
            result.action = OrderAction.RESCIND  # type: ignore[misc]

    def test_extraction_can_be_constructed_for_a_refusal_without_other_fields(self) -> None:
        bare = Extraction(method=ExtractionMethod.REQUIRES_OCR)
        assert bare.scorable is False
        assert bare.qualifier is SuspensionQualifier.NOT_APPLICABLE


#: Scott Addendum 12, verbatim. The sentence that broke the first body reader.
ADDENDUM_12_BODY = """Subject: Scott River Watershed: Reinstatement of Curtailments for All Surface
Water Rights (Groups 1-8) - Addendum 12
To: Scott River Water Right Holders

This Addendum to the Orders ends the conditional suspension of curtailment
provided by Addendum 11 and reinstates curtailment for all surface water rights
in Priority Groups 1-8 in the Scott River watershed. Flows have fallen to 119 cfs
against the 125 cfs minimum for December.
"""

#: Scott Addendum 3, verbatim from the rendered page. A grant, not a termination.
ADDENDUM_3_BODY = """Addendum 3: Update to Scott River Surface Water Curtailments for Farmer's Ditch
Company
To: Scott River Water Right Holders

Based on consideration of the generally increasing flow trend above the minimum
flow requirement at the USGS Fort Jones gage, this Addendum 3 provides for the
limited, conditional suspension of curtailment for Farmer's Ditch Company, under
the conditions noted below.

This addendum expires at 11:59 pm on September 30, 2024, unless amended or
superseded prior to that date.
"""


class TestTheVerbBetweenCarriesTheLegalEffect:
    """The polarity bug, caught on Scott Addendum 12 and locked here.

    A first version allowed any 120 characters between "this Addendum" and
    "conditional suspension". It matched "This Addendum to the Orders ENDS the
    conditional suspension" and concluded the addendum WAS one. It terminates
    one. Reading a reinstatement as a suspension tells diverters they may divert
    at the moment the Board ordered them to stop.
    """

    def test_ending_a_suspension_reads_as_a_reinstatement(self) -> None:
        assert extract(ADDENDUM_12_BODY).body_action is OrderAction.REINSTATE

    def test_ending_a_suspension_is_never_read_as_a_suspension(self) -> None:
        """The specific inversion. Stated separately so it cannot be lost in a
        refactor of the assertion above."""
        assert extract(ADDENDUM_12_BODY).body_action is not OrderAction.SUSPEND

    def test_the_title_and_body_agree_so_nothing_is_flagged(self) -> None:
        result = extract(ADDENDUM_12_BODY)
        assert result.action is OrderAction.REINSTATE
        assert result.title_body_conflict is False
        assert result.scorable is True

    def test_granting_a_limited_conditional_suspension_reads_as_a_suspension(self) -> None:
        result = extract(ADDENDUM_3_BODY)
        assert result.body_action is OrderAction.SUSPEND

    def test_a_recital_of_another_document_is_not_read_as_this_ones_act(self) -> None:
        """Body reading is anchored on "this Addendum" for exactly this reason.

        Addenda routinely recite what neighbouring documents did. Without the
        self-reference, a reinstatement reciting a neighbour's conditional
        suspension would read as a suspension of its own.

        The recital below is phrased EXACTLY as a matching sentence would be,
        "provides for the conditional suspension", so the self-reference is the
        only thing standing between this text and a false match. An earlier
        version said "provided the conditional suspension", which cannot match
        the affirmative verb pattern in any case, so it passed whether or not the
        anchor existed. Mutation testing caught that vacuity; this assertion now
        fails when the anchor is removed.
        """
        text = (
            "Subject: Reinstatement of Curtailments for All Surface Water Rights\n"
            "\n"
            "On November 1, 2024, Addendum 11 provides for the conditional suspension\n"
            "of curtailment for all surface water rights. " + "Further text. " * 20
        )
        assert extract(text).body_action is not OrderAction.SUSPEND


class TestATitleBodyDisagreementIsRecordedNotResolved:
    def test_a_conflict_makes_a_document_unscorable(self) -> None:
        """Scoring a document whose title and body disagree means picking a side
        silently, which is the failure this project exists to prevent."""
        text = (
            "Subject: Full Suspension of Curtailments in Scott River Watershed\n"
            "\n"
            "This Addendum to the Orders ends the conditional suspension of\n"
            "curtailment provided by the previous addendum. " + "Body text. " * 20
        )
        result = extract(text)
        assert result.action is OrderAction.SUSPEND
        assert result.body_action is OrderAction.REINSTATE
        assert result.title_body_conflict is True
        assert result.scorable is False

    def test_a_conflict_explains_itself_in_the_notes(self) -> None:
        text = (
            "Subject: Full Suspension of Curtailments in Scott River Watershed\n"
            "\n"
            "This Addendum to the Orders ends the conditional suspension of\n"
            "curtailment provided by the previous addendum. " + "Body text. " * 20
        )
        notes = " ".join(extract(text).notes).lower()
        assert "title and body disagree" in notes

    def test_a_silent_body_is_not_a_conflict(self) -> None:
        """Most documents make no self-referential claim, which is normal.

        Treating silence as disagreement would make almost the whole corpus
        unscorable and destroy the metric.
        """
        result = extract(SHASTA_ADDENDUM_6)
        assert result.body_action is OrderAction.UNDETERMINED
        assert result.title_body_conflict is False
        assert result.scorable is True


class TestExpiryIsCaptured:
    """A lapsed suspension reverts rights to curtailment with no further order."""

    def test_an_expiry_is_read_from_the_document(self) -> None:
        from datetime import date

        assert extract(ADDENDUM_3_BODY).expires_on == date(2024, 9, 30)

    def test_a_document_without_an_expiry_records_none(self) -> None:
        assert extract(SHASTA_ADDENDUM_6).expires_on is None

    def test_an_expiry_is_never_invented_for_a_base_order(self) -> None:
        """Base orders run until suspended or rescinded. Inventing an end date
        would show a live curtailment as lapsed."""
        assert extract(SCOTT_BASE_ORDER).expires_on is None


#: Shasta 2021 Addendum 7, verbatim. The sentence that broke the terminating
#: pattern through a missing word boundary.
ADDENDUM_EXTENDING_RELIEF = """State Water Resources Control Board
Subject: Order WR 2021-0082-DWR, Addendum 7

This Addendum amends Order WR 2021-0082-DWR to extend the temporary suspension
of all curtailments of water rights in the Shasta River watershed through the
end of the month, as long as flows remain above the required minimum.
"""


class TestAWordBoundaryDecidesTheLegalEffect:
    """`ends?` with no word boundary matches inside "extend".

    Shasta 2021 Addenda 7 and 8 both say "this Addendum amends Order WR
    2021-0082-DWR to EXTEND the temporary suspension". The terminating pattern
    matched that, reading an EXTENSION OF RELIEF as a REINSTATEMENT OF
    CURTAILMENT: telling diverters to stop at the exact moment the Board
    extended their permission to divert.

    It is the same inversion the terminating patterns were written to prevent,
    arriving through the other door, and it was introduced by the fix for the
    first one. Found by an adversarial review of that fix.
    """

    def test_extending_a_suspension_is_not_read_as_ending_one(self) -> None:
        assert extract(ADDENDUM_EXTENDING_RELIEF).body_action is not OrderAction.REINSTATE

    def test_extending_a_suspension_continues_relief(self) -> None:
        assert extract(ADDENDUM_EXTENDING_RELIEF).body_action is OrderAction.SUSPEND

    def test_ending_a_suspension_still_reads_as_reinstatement(self) -> None:
        """The original fix must survive the fix to the fix."""
        assert extract(ADDENDUM_12_BODY).body_action is OrderAction.REINSTATE

    def test_granting_a_suspension_still_reads_as_suspension(self) -> None:
        assert extract(ADDENDUM_3_BODY).body_action is OrderAction.SUSPEND

    @pytest.mark.parametrize("verb", ["amends", "suspends", "appends", "recommends", "depends on"])
    def test_no_other_word_containing_end_triggers_a_termination(self, verb: str) -> None:
        """The bug class, swept. Every one of these contains the letters "end"."""
        text = (
            f"Subject: Notice\n\nThis Addendum {verb} the conditional suspension "
            "described elsewhere. " + "Body text. " * 30
        )
        assert extract(text).body_action is not OrderAction.REINSTATE


class TestGroupRangesWithMoreThanOneDigit:
    def test_a_two_digit_range_expands_fully(self) -> None:
        """A single-digit capture read "Groups 1-10" as 1 to 1, silently dropping
        groups 2 through 10. An empty or truncated grouping is indistinguishable
        from a document that named no grouping at all."""
        text = "Subject: Reinstatement of Curtailments for Groups 1-10\n" + "body. " * 40
        assert extract(text).priority_groups == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)

    def test_a_two_digit_single_group_is_read(self) -> None:
        text = "Subject: Limited Conditional Suspension for Group 12\n" + "body. " * 40
        assert extract(text).priority_groups == (12,)

    def test_the_scott_ladder_range_still_reads_correctly(self) -> None:
        assert extract(SCOTT_ADDENDUM_12).priority_groups == (1, 2, 3, 4, 5, 6, 7, 8)


class TestDatesInCapitalsAreNotInvisible:
    def test_an_all_caps_priority_date_is_read(self) -> None:
        """These documents print decree tier boundaries in capitals, for example
        "(PRIORITY DATES APRIL 1, 1912 TO MARCH 1, 1885)". A case-sensitive
        pattern skipped them while the record still read as parsed, so a missing
        priority date was indistinguishable from a document naming none."""
        from datetime import date as _date

        text = "Subject: Notice\n\n(PRIORITY DATES APRIL 1, 1912 TO MARCH 1, 1885)\n" + "x " * 90
        dates = extract(text).priority_dates
        assert _date(1912, 4, 1) in dates
        assert _date(1885, 3, 1) in dates

    def test_mixed_case_dates_still_read(self) -> None:
        from datetime import date as _date

        assert _date(1912, 11, 25) in extract(SHASTA_ADDENDUM_6).priority_dates


#: Shasta 2021 Addendum 7, verbatim. The action is on the CONTINUATION lines.
SHASTA_2021_TITLE_BLOCK = """State Water Resources Control Board
January 26, 2022
              ADDENDUM 7 TO ORDER WR 2021-0082-DWR:
     EXTENSION OF CONDITIONAL TEMPORARY SUSPENSION OF ALL
CURTAILMENTS IN SHASTA RIVER WATERSHED THROUGH FEBRUARY 28, 2022
To:   Scott River Watershed and Shasta River Watershed Water Right Holders
"""

#: Shasta 2021 Addendum 9, verbatim. Note the lower-case "and" in the header
#: line and the trailing footnote marker, which the all-caps form rejects.
SHASTA_2021_MIXED_CASE_HEADER = """State Water Resources Control Board
March 15, 2022
      ADDENDUM 9 TO ORDERS WR 2021-0082-DWR and WR 2021-0085-DWR 1:
    REINSTATEMENT OF CURTAILMENTS AND MODIFICATION OF MARCH FLOW
                            REQUIREMENTS
To:    Scott River Watershed and Shasta River Watershed Water Right Holders
"""

#: Shasta 2021 Addendum 14, verbatim. "Imposition", not "imposing".
SHASTA_2021_IMPOSITION = """State Water Resources Control Board
June 29, 2023
Subject: Order WR 2021-0082-DWR, Addendum 14: Imposition of Full Curtailment for
         Junior Water Rights in Shasta River Watershed
To:   Scott River Watershed and Shasta River Watershed Water Right Holders
"""

#: Scott 2021 Addendum 32, verbatim. A status notice announcing no new decision.
SCOTT_2021_CONTINUATION_NOTICE = """Subject: Scott River Curtailments in Effect for All Surface Water Diverters
(List A2) as of 12:00 pm (noon) on July 2, 2022
Date: Friday, July 1, 2022
The State Water Board will continue to monitor flow forecasts and watershed
conditions regularly to determine if further adjustments are appropriate.
"""


class TestTheActionCanLiveOnAContinuationLine:
    """The Shasta 2021 series names the document, then states the act beneath it.

    "ADDENDUM 7 TO ORDER WR 2021-0082-DWR:" is the document's NAME. Matching only
    that line finds the name and none of the meaning, which is why five documents
    in that series were unclassifiable.
    """

    def test_an_all_caps_continuation_supplies_the_action(self) -> None:
        assert extract(SHASTA_2021_TITLE_BLOCK).action is OrderAction.SUSPEND

    def test_a_header_with_a_lower_case_connector_is_still_a_header(self) -> None:
        """ "ADDENDUM 9 TO ORDERS ... and WR 2021-0085-DWR 1:" contains "and".

        The trailing colon is what makes loosening the character class safe: it
        marks a header whose continuation follows, so the looser match cannot
        wander into prose.
        """
        assert extract(SHASTA_2021_MIXED_CASE_HEADER).action is OrderAction.REINSTATE

    def test_a_base_order_title_still_reads_correctly(self) -> None:
        """No regression from widening the header match."""
        assert extract(SCOTT_BASE_ORDER).action is OrderAction.IMPOSE
        assert extract(SHASTA_BASE_ORDER).action is OrderAction.IMPOSE

    def test_body_prose_is_not_absorbed_as_a_title(self) -> None:
        """The continuation is bounded and all-caps only, so ordinary sentences
        beneath a header cannot be pulled into the action window."""
        text = (
            "ORDER IMPOSING WATER RIGHT CURTAILMENT:\n"
            "This order suspends nothing and grants no conditional suspension.\n"
            + "Body text. "
            * 40
        )
        assert extract(text).action is OrderAction.IMPOSE


class TestImpositionIsAnImposition:
    def test_the_noun_form_is_read(self) -> None:
        """Shasta 2021 Addendum 14 says "Imposition of Full Curtailment", and a
        pattern matching only "impose" and "imposing" missed it."""
        assert extract(SHASTA_2021_IMPOSITION).action is OrderAction.IMPOSE


class TestAStatusNoticeIsNotADecision:
    """Scott 2021 Addenda 32 and 35 restate what is already in force.

    They announce no new decision, so scoring them as though a threshold had just
    been crossed would inflate the metric with non-events. CONTINUE is a real
    category, not a parse failure, and it maps to no direction in the backtest.
    """

    def test_a_continuation_notice_is_its_own_category(self) -> None:
        assert extract(SCOTT_2021_CONTINUATION_NOTICE).action is OrderAction.CONTINUE

    def test_a_continuation_notice_is_not_read_as_a_fresh_imposition(self) -> None:
        assert extract(SCOTT_2021_CONTINUATION_NOTICE).action is not OrderAction.IMPOSE

    def test_a_reimposition_in_the_body_reads_as_a_reinstatement(self) -> None:
        """Scott 2021 Addendum 35: "This addendum reimposes all curtailments
        identified in the Curtailment Orders above."

        Its title says "Curtailments in Effect", a status. Its body says it
        reimposes them, a fresh act that starts new clocks. Those are different
        legal characterisations, so the conflict guard refuses rather than
        choosing, which is the behaviour we want.
        """
        text = (
            "Subject: Scott River Watershed: Curtailments in Effect for All Surface\n"
            "Water and Groundwater Diversions\n"
            "\n"
            "This addendum reimposes all curtailments identified in the Curtailment\n"
            "Orders above. " + "Body text. " * 30
        )
        result = extract(text)
        assert result.body_action is OrderAction.REINSTATE
        assert result.action is OrderAction.CONTINUE
        assert result.title_body_conflict is True
        assert result.scorable is False
