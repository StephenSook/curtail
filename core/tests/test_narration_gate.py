"""The narration's figure gate must read spelled-out numbers, not only digits.

Found in review: the gate scanned `\\d[\\d,\\.]*`, and the narration speaks its most
material figures in words. "Five hundred dollars", "ten thousand", "eighty thousand",
the numbers carrying the entire pitch, passed without a single lookup while the gate
reported clean. A figure gate that reads only digits is a false green on exactly the
claims a published video cannot take back.

The same review found bare substring matching passing "500" on the strength of gage id
11517500. Matches must stand on their own as numbers.

These tests import the builder module directly. Its module level is safe (no network,
no synthesis; the Chirp call lives inside `build()` and is never touched here).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_narration", REPO / "scripts" / "build_narration.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSpelledFigures:
    def test_a_spelled_figure_with_no_source_is_reported(self) -> None:
        bn = load()
        missing = bn.check_figures([("beat1", "raised that to eighty thousand dollars")], "")
        assert missing == ["beat1: 'eighty thousand' (80000)"]

    def test_a_spelled_figure_backed_by_digits_passes(self) -> None:
        bn = load()
        facts = "Statutory exposure, 8 days of violation: **$80,000**"
        assert bn.check_figures([("beat1", "eighty thousand dollars")], facts) == []

    def test_a_spelled_figure_backed_by_the_word_itself_passes(self) -> None:
        bn = load()
        facts = "wrong by a factor of twenty"
        assert bn.check_figures([("beat1", "Twenty times wrong")], facts) == []

    def test_compound_phrases_parse_to_one_value(self) -> None:
        bn = load()
        assert bn.phrase_value("five hundred and seventy-seven") == 577
        assert bn.phrase_value("a hundred and one") == 101
        assert bn.phrase_value("ten thousand") == 10000
        assert bn.phrase_value("eighty-seven") == 87

    def test_small_counts_are_below_the_gate_not_claims(self) -> None:
        """ "Two state machines, not one" must not demand a fact-sheet line: a one-digit
        substring match against a sheet full of digits verifies nothing, so flagging it
        would be theatre. The floor is the same one NOT_A_CLAIM applies to digits."""
        bn = load()
        assert bn.check_figures([("beat4", "two state machines, not one")], "") == []


class TestDigitsStandAlone:
    def test_digits_inside_a_longer_number_are_not_a_source(self) -> None:
        """Substring matching passed "500" via gage id 11517500. That match verifies
        nothing: the figure must appear as a number of its own."""
        bn = load()
        missing = bn.check_figures([("beat2", "a penalty of 500 dollars")], "gage USGS-11517500")
        assert missing == ["beat2: 500"]

    def test_a_figure_followed_by_sentence_punctuation_still_matches(self) -> None:
        bn = load()
        assert bn.check_figures([("beat2", "flows read 45.3")], "the reading was 45.3.") == []


class TestTheDurationIsAVerdict:
    """A narration longer than the competition cap used to print a WARNING and exit 0,
    the sixth instance of the printed-caveat defect this project has recorded. The
    ceiling is now a refusal, and it comes before the timing file is written so a
    refused narration cannot leave a beats.json behind for the capture to consume."""

    SOURCE = (REPO / "scripts" / "build_narration.py").read_text()

    def test_the_ceiling_is_a_named_constant_that_raises(self) -> None:
        assert "NARRATION_CEILING = " in self.SOURCE
        after = self.SOURCE[self.SOURCE.index("total > NARRATION_CEILING") :]
        assert "raise NarrationError" in after.split("def ")[0], (
            "the duration ceiling no longer raises, so an over-cap narration would "
            "build green and the film's tail would simply never be judged"
        )

    def test_the_refusal_comes_before_the_timing_file(self) -> None:
        assert self.SOURCE.index("total > NARRATION_CEILING") < self.SOURCE.index('"beats.json"'), (
            "a refused narration would still write beats.json for the capture to consume"
        )

    def test_the_beat_count_is_exact_not_a_floor(self) -> None:
        assert "EXPECTED_BEATS = 8" in self.SOURCE
        assert "!= EXPECTED_BEATS" in self.SOURCE, (
            "the beat count is a floor again, so cutting the final beats would build a "
            "green narration missing a quarter of the film"
        )

    def test_measurement_passes_check_their_exit_code(self) -> None:
        """Both loudness measurements read stderr, and both used to read it bare off
        subprocess.run, so an ffmpeg that died produced an empty report and an error
        message blaming the measurement instead of the tool."""
        assert ").stderr" not in self.SOURCE, (
            "a measurement reads stderr without checking the returncode, so a dead "
            "ffmpeg reports as an empty measurement"
        )


class TestTheRealNarrationIsSourced:
    def test_every_figure_in_the_shot_list_is_in_the_fact_sheet(self) -> None:
        """The live gate, as a committed test. The narration and the fact sheet are both
        in the repository; if either drifts so that a spoken figure loses its source,
        this fails in CI rather than at 2am on synthesis night."""
        bn = load()
        beats = bn.parse_beats((REPO / "docs" / "video" / "script.md").read_text())
        assert len(beats) == 8, "the shot list no longer parses into eight beats"
        missing = bn.check_figures(beats, (REPO / "docs" / "FACTS.md").read_text())
        assert missing == [], (
            f"spoken figures with no fact-sheet source: {missing}. A number said on "
            "camera cannot be corrected afterwards."
        )
