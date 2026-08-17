"""Record the demo capture: a real browser, driving the real deployed service.

**No screen-recording permission is involved.** Playwright records the browser context
itself, which is why this can run unattended, and it is arguably stronger evidence than a
screen capture: the frames are a browser genuinely hitting the production URL, not a window
on somebody's desktop that could be showing anything.

ONE CONTINUOUS SESSION for the agent-execution beats, 1 through 5. The console holds
state in memory across cards, and the rules ask for "an unedited, live execution" of the
agent performing its task, so those beats are one browser, one session, no cuts. Beats 6
to 8 are a different kind of evidence and are captured separately, on purpose: the Google
Cloud consoles need an authenticated human session this headless context does not hold,
and the closing card is not a live execution of anything. marks.json records exactly
which beats this take covers, so the assembler can refuse to mux until every absent beat
has its own source.

**The fleet traversal takes about 63 seconds and is not cut.** It calls Gemini for real. The
narration talks through the wait, which is what an operator watching this actually
experiences, and the grand-prize video studied for this build does exactly the same thing,
dead air included.

    uv run python scripts/capture_demo.py            # record against production
    uv run python scripts/capture_demo.py --url ...  # or somewhere else
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any


class CaptureFailedError(RuntimeError):
    """The take is not usable. Distinct from the script itself being broken."""


REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "video" / "capture"
BEATS = REPO / "docs" / "video" / "beats.json"
LIVE = "https://curtail-console-api-672785135387.us-central1.run.app"

#: Wide enough that the console's three-column cards are not stacked, and exactly the
#: dimension the shipped file is checked against later.
VIEWPORT = {"width": 1920, "height": 1080}

#: **The shortest a real traversal can be.** The first take reported a 64.5 second Gemini
#: call as finishing in 0.0 seconds, because the wait matched the card's pending state
#: rather than its result. Anything under this is a spinner being filmed as a product.
#:
#: A named constant rather than a literal, because the guard's own test asserted the
#: string `elapsed < 5` and broke the moment the variable was renamed to `traversal`. The
#: floor was intact and the test failed anyway. A test should assert a symbol that has to
#: exist, not a spelling that happens to.
MINIMUM_TRAVERSAL_SECONDS = 5.0


def settle(page: Any, card: str, previous: str, timeout: int = 300_000) -> str:
    """Wait for a card to render a generation NEWER than the one already on screen.

    **Waiting for a selector to appear is the trap this exists to avoid.** Every card
    writes a pending state the instant it is clicked, so `wait_for_selector('.status')`
    matches in milliseconds and the capture films a spinner as though it were the result.
    The first take of this film did exactly that: it reported the 63-second fleet traversal
    as "completed in 0.0s".

    The console stamps `data-render` with a generation counter when a card finishes, which
    is the same signal the browser suite waits on. Comparing against the PREVIOUS value is
    what makes it a completion signal rather than a presence one.
    """
    page.wait_for_function(
        "([card, previous]) => {"
        "  const el = document.querySelector(card);"
        "  return el && el.dataset.render && el.dataset.render !== previous;"
        "}",
        arg=[card, previous],
        timeout=timeout,
    )
    return page.locator(card).get_attribute("data-render") or ""


def scroll(page: Any, selector: str) -> None:
    """Move the camera, and refuse to pretend when the target is not there.

    Playwright's own `scroll_into_view_if_needed` raises on a missing element, which is
    the behaviour this capture needs: a selector that silently matches nothing produces
    a take that narrates one thing while showing another, and nothing in the output says
    so.
    """
    page.locator(selector).first.scroll_into_view_if_needed(timeout=10_000)


class Clock:
    """Holds the capture to the narration's real timings, and records where it landed.

    **The holds used to be numbers typed into this file.** They drifted from the
    narration the moment a word was cut, and a beat whose picture ends before its
    sentence does is the most obvious kind of amateur cut there is. Now every hold is
    read from `beats.json`, which the narration builder writes by MEASURING the
    synthesised audio, so the two cannot disagree.

    `marks` is what makes the mux deterministic. A beat can legitimately overrun its
    narration (the fleet traversal calls Gemini for real and takes as long as it takes),
    so the assembler needs the offsets that ACTUALLY happened, not the ones intended.
    """

    def __init__(self, page: Any, beats: dict[str, Any]) -> None:
        self.page = page
        self.gap = float(beats["gap_seconds"])
        self.seconds = {b["beat"]: float(b["seconds"]) for b in beats["beats"]}
        self.start = time.monotonic()
        self.marks: list[dict[str, float | str]] = []

    def elapsed(self) -> float:
        return time.monotonic() - self.start

    def open(self, beat: str) -> float:
        """Record where a beat begins and return the wall-clock deadline for its end."""
        at = self.elapsed()
        self.marks.append({"beat": beat, "at": round(at, 3)})
        print(f"    {beat:8} opens at {at:6.1f}s, narration {self.seconds[beat]:5.1f}s", flush=True)
        return at + self.seconds[beat] + self.gap

    def hold(self, until: float) -> None:
        """Wait out the rest of a beat, or note that the product overran it.

        An overrun is not an error. The winning film studied for this build visibly
        waits on its own system, and a traversal that takes longer than its sentence is
        the honest thing to show.
        """
        remaining = until - self.elapsed()
        if remaining <= 0:
            print(f"      the product overran its narration by {-remaining:.1f}s", flush=True)
            return
        self.page.wait_for_timeout(int(remaining * 1000))


def capture(url: str) -> Path:
    from playwright.sync_api import sync_playwright

    if not BEATS.exists():
        raise CaptureFailedError(
            "docs/video/beats.json is missing, so the holds would have to be guessed. "
            "Run scripts/build_narration.py first: the narration sets the timing."
        )
    beats = json.loads(BEATS.read_text())

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb"])
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(OUT),
            record_video_size=VIEWPORT,
            device_scale_factor=1,
        )
        page = context.new_page()
        failed: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # **Two different events, because they catch two different things.**
        #
        # `requestfailed` fires only when a request never completed: DNS, refused
        # connection, abort. **A 500 is a perfectly successful HTTP transaction** and never
        # fires it, so watching only that one leaves the likeliest failure in a filmed demo
        # completely invisible: a card that 503s renders as an empty panel and the take
        # passes.
        page.on(
            "requestfailed",
            lambda r: (
                failed.append(f"{r.url.split('?')[0]} did not complete: {r.failure}")
                if "/api/" in r.url
                else None
            ),
        )
        page.on(
            "response",
            lambda r: (
                failed.append(f"{r.url.split('?')[0]} returned HTTP {r.status}")
                if "/api/" in r.url and r.status >= 400
                else None
            ),
        )

        clock = Clock(page, beats)

        print("  beat 1, the console loads")
        until = clock.open("beat1")
        page.goto(url, wait_until="domcontentloaded")
        clock.hold(until)

        print("  beat 2, the river, live")
        until = clock.open("beat2")
        page.select_option("#basin", "shasta")
        page.fill("#cfs", "45.3")
        page.fill("#at", "2026-06-16")
        before = page.locator("#out").get_attribute("data-render") or ""
        page.click("#go")
        before = settle(page, "#out", before, timeout=90_000)
        # Halfway through the sentence the narration moves from the reading to the Core,
        # so the picture moves with it.
        page.wait_for_timeout(int(clock.seconds["beat2"] * 400))
        scroll(page, "#rec")
        clock.hold(until)

        print("  beat 3, the refusal")
        until = clock.open("beat3")
        # 54.5 against a 50 cfs minimum sits inside the near-threshold band, so the
        # engine declines to order and asks for a field measurement instead. This is the
        # single most important thing on screen: the system NOT acting, and saying why.
        page.fill("#cfs", "54.5")
        page.fill("#at", "2026-08-16")
        page.click("#go")
        settle(page, "#out", before, timeout=90_000)
        clock.hold(until)

        print("  beat 4, the fleet, uncut")
        until = clock.open("beat4")
        scroll(page, "#fleet")
        page.fill("#cfs", "45.3")
        page.fill("#at", "2026-06-16")
        fleet_before = page.locator("#fleetout").get_attribute("data-render") or ""
        started = time.monotonic()
        page.click("#fleet")
        # The RESULT, not the pending state. See settle(): the first take matched the
        # spinner and reported this 63-second traversal as finishing in 0.0 seconds.
        settle(page, "#fleetout", fleet_before, timeout=300_000)
        traversal = time.monotonic() - started
        print(f"      traversal completed in {traversal:.1f}s")
        if traversal < MINIMUM_TRAVERSAL_SECONDS:
            raise CaptureFailedError(
                f"the traversal returned in {traversal:.1f}s, which is not a real Gemini "
                "call. The capture would show a spinner rather than the product."
            )
        # Let the finished traversal sit on screen long enough to read the attribution,
        # even when it overran its own sentence getting there.
        page.wait_for_timeout(6_000)
        clock.hold(until)

        print("  beat 5, what it proves and refuses")
        until = clock.open("beat5")
        # **`scroll` raises on a missing id, deliberately.** This used to read
        # `const el = ...; if (el) el.scrollIntoView(...)` against `#bt`, an id that does
        # not exist in the console at all. The guard made a broken selector look like a
        # successful scroll, so beat 5 would have narrated the backtest over whatever
        # happened to be on screen. A capture step that cannot fail cannot be trusted.
        share = clock.seconds["beat5"] / 3
        scroll(page, "#backtestcard")
        page.wait_for_timeout(int(share * 1000))
        scroll(page, "#ledgercard")
        page.wait_for_timeout(int(share * 1000))
        scroll(page, "#q")
        page.fill("#q", "when was curtailment lifted after a gage revision")
        search_before = page.locator("#searchout").get_attribute("data-render") or ""
        page.click("#ask")
        settle(page, "#searchout", search_before, timeout=120_000)
        clock.hold(until)

        print("  beats 6 to 8 are separate evidence captures; marks.json records them as absent")
        total = clock.elapsed()
        context.close()
        browser.close()

    videos = sorted(OUT.glob("*.webm"))
    if not videos:
        raise RuntimeError("no video was written")
    if failed:
        # **Raise, do not print.** The first version printed this to stderr and then
        # returned the path, so a take where the console threw would have exited 0 and
        # shipped. That is the same defect this project has now recorded five times: a
        # printed caveat is not a verdict, because the exit code is what a pipeline and a
        # tired person both read. A capture is either usable or it is not.
        raise CaptureFailedError(
            f"{len(failed)} API call(s) failed during the take, so the film would show a "
            f"broken product: {failed[:4]}"
        )
    if errors:
        raise CaptureFailedError(
            f"{len(errors)} page error(s) during the take: {errors[:4]}. The console threw "
            "while being filmed, and a video of a product erroring is worse than no video."
        )
    # **The omission is written into the artifact, not left in a print line.** This take
    # covers the agent-execution beats only, and a marks file that listed five marks
    # without saying eight were expected would hand the assembler a film quietly missing
    # its final minute. `beats_absent` is a refusal list: the mux may not run until every
    # beat named there has its own separately captured source.
    captured = [str(m["beat"]) for m in clock.marks]
    absent = [b["beat"] for b in beats["beats"] if b["beat"] not in captured]
    (OUT / "marks.json").write_text(
        json.dumps(
            {
                "url": url,
                "seconds": round(total, 3),
                "traversal_seconds": round(traversal, 3),
                "beats_captured": captured,
                "beats_absent": absent,
                "marks": clock.marks,
            },
            indent=2,
        )
        + "\n"
    )
    return videos[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=LIVE)
    args = parser.parse_args()
    print(f"  capturing {args.url}")
    path = capture(args.url)
    size = path.stat().st_size
    print(f"\n  wrote {path.relative_to(REPO)} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
