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

    def test_failed_api_requests_are_watched_for(self) -> None:
        source = SCRIPT.read_text()
        assert "requestfailed" in source, (
            "nothing watches for failed API requests, so a card that 503s would be filmed "
            "as an empty panel and reported as a good take"
        )

    def test_the_traversal_has_a_floor(self) -> None:
        """The first take reported a 64.5 second Gemini call as finishing in 0.0 seconds,
        because the wait matched the pending state. A floor makes that unrepresentable
        rather than merely unlikely."""
        source = SCRIPT.read_text()
        assert "elapsed < 5" in source
