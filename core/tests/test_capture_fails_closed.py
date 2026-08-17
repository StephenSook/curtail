"""The demo capture must refuse a bad take, not report one.

Found in review: the script recorded page errors, printed them to stderr, and then
RETURNED THE VIDEO PATH and exited 0. A take where the console threw while being filmed
would have shipped as a good one.

**This is the fifth time this project has met the same defect.** The chaos drill printed a
partial result and exited 0. An offline switch printed "these prove nothing" and returned
success. The deployment record printed the served commit beside the repository commit and
passed. A `--watch` command crashed mid-poll and reported 0. Every one of them printed
something honest and then said fine. The exit code is what a pipeline and a tired person
both read, and everything else is decoration.

Asserted on the source rather than by driving a browser, because the failure is structural:
the refusal has to come BEFORE the return, and no amount of runtime exercising proves that
as directly as reading the order.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "capture_demo.py"


def tail() -> str:
    """The part of `capture()` that decides whether the take is usable."""
    source = SCRIPT.read_text()
    return source[source.index("videos = sorted") :]


class TestABadTakeIsRefused:
    def test_a_page_error_raises_rather_than_printing(self) -> None:
        body = tail()
        assert "raise CaptureFailedError" in body, (
            "the capture no longer refuses a take that errored. It would exit 0 and the "
            "video of a broken product would ship."
        )
        assert 'print(f"\\n  PAGE ERRORS' not in body, (
            "the errors branch is printing again instead of raising"
        )

    def test_both_failure_branches_raise(self) -> None:
        """Two ways a take is unusable: the page threw, or an API request failed. A card
        that silently 503s films as an empty panel, which reads to a judge as a product
        that does not work."""
        assert tail().count("raise CaptureFailedError") == 2

    def test_the_refusal_comes_before_the_return(self) -> None:
        """Ordering IS the guarantee. A raise placed after the return is unreachable, and
        it would read as a fix while changing nothing."""
        body = tail()
        assert body.index("raise CaptureFailedError") < body.index("return videos[0]")

    def test_both_transport_failure_and_http_status_are_watched(self) -> None:
        """Two events, because they catch two different things, and the second is the one
        that actually matters here.

        `requestfailed` fires only when a request never completed: DNS, refused connection,
        abort. **A 500 is a perfectly successful HTTP transaction** and never fires it. So
        a version watching only `requestfailed` leaves the likeliest failure in a filmed
        demo entirely invisible: a card that 503s renders as an empty panel, and the take
        passes.
        """
        source = SCRIPT.read_text()
        assert "requestfailed" in source, "transport failures are not watched"
        assert '"response"' in source, (
            "HTTP status is not watched, so a 500 would film as an empty panel and the "
            "take would be reported as good"
        )
        assert "r.status >= 400" in source, "responses are observed but their status is not checked"

    def test_the_whole_origin_is_watched_not_just_the_api(self) -> None:
        """A 404 on the vendored chart bundle is a completed HTTP transaction outside
        /api/, and the console renders the failure INSIDE the card, so a take could
        film "the charting bundle did not load" over narration about the season and
        pass every check scoped to /api/."""
        source = SCRIPT.read_text()
        assert "r.url.startswith(origin)" in source, (
            "the failure watch is scoped narrower than the origin again, so an asset "
            "404 films as a calm broken card"
        )
        assert '"/api/" in r.url' not in source, (
            "an /api/-only filter is back beside the origin watch, which suggests one "
            "of the two listeners regressed"
        )

    def test_a_settled_card_must_hold_a_result(self) -> None:
        """settle() proves the card re-rendered; it cannot prove what rendered. The
        console renders Unavailable and Refused inside the card with a fresh stamp,
        so a take could film a calm refusal over narration describing a working
        product and pass every network check."""
        source = SCRIPT.read_text()
        assert "def result_state" in source
        assert "s-system" in source, (
            "the result check no longer looks for the console's system-state class"
        )
        assert source.count("result_state(page,") >= 4, (
            "fewer than four settles assert a result, so at least one filmed card can "
            "settle on a refusal unnoticed"
        )

    def test_a_refused_take_leaves_no_video_behind(self) -> None:
        """The raise refuses the exit code; the .webm it left behind was still a file,
        and a file that exists is a file something downstream eventually globs."""
        body = tail()
        assert "video.unlink()" in body, (
            "a refused take stays on disk for a future assembler to mux"
        )
        assert body.index("video.unlink()") < body.index("raise CaptureFailedError"), (
            "the cleanup happens after the raise, which is to say never"
        )

    def test_partial_coverage_is_written_into_the_artifact(self) -> None:
        """The capture films the agent-execution beats and leaves the evidence beats to
        separate captures. That split is a design decision, but a marks file listing five
        marks without saying eight were expected hands the assembler a film quietly
        missing its final minute. The omission must live in the ARTIFACT: a print line
        scrolls away, and the assembler reads the file, not the terminal."""
        body = tail()
        assert '"beats_captured"' in body, (
            "marks.json no longer names the beats this take covers, so a consumer must "
            "guess coverage from the marks themselves"
        )
        assert '"beats_absent"' in body, (
            "marks.json no longer names the beats this take does NOT cover, so a mux "
            "trusting it would assemble a film missing its final beats with nothing "
            "saying so"
        )
        assert 'beats["beats"]' in body, (
            "the absent list is not computed against beats.json, so a beat added to the "
            "narration would not appear in either list and the omission returns"
        )

    def test_the_traversal_has_a_floor(self) -> None:
        """The first take reported a 64.5 second Gemini call as finishing in 0.0 seconds,
        because the wait matched the pending state. A floor makes that unrepresentable
        rather than merely unlikely.

        **This assertion used to read `assert "elapsed < 5" in source` and it broke on a
        rename.** The floor was fully intact, the variable was simply called something
        else, and the suite went red over a spelling. A test that asserts source TEXT is
        testing how the code is written; asserting a NAMED CONSTANT tests that the thing
        exists, which is what was actually meant. The floor now has a name.
        """
        source = SCRIPT.read_text()
        assert "MINIMUM_TRAVERSAL_SECONDS = " in source, (
            "the traversal floor is not a named constant, so nothing here can check it "
            "survives a refactor"
        )
        assert "< MINIMUM_TRAVERSAL_SECONDS" in source, (
            "the floor is defined but never compared against, which is a constant "
            "documenting a guard rather than a guard"
        )
