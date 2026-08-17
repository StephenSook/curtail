"""Record the demo capture: a real browser, driving the real deployed service.

**No screen-recording permission is involved.** Playwright records the browser context
itself, which is why this can run unattended, and it is arguably stronger evidence than a
screen capture: the frames are a browser genuinely hitting the production URL, not a window
on somebody's desktop that could be showing anything.

ONE CONTINUOUS SESSION for the whole run. The console holds state in memory across cards,
and the rules ask for "an unedited, live execution", so cutting between sessions would be
both technically wrong and a worse answer to the criterion.

**The fleet traversal takes about 63 seconds and is not cut.** It calls Gemini for real. The
narration talks through the wait, which is what an operator watching this actually
experiences, and the grand-prize video studied for this build does exactly the same thing,
dead air included.

    uv run python scripts/capture_demo.py            # record against production
    uv run python scripts/capture_demo.py --url ...  # or somewhere else
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "video" / "capture"
LIVE = "https://curtail-console-api-672785135387.us-central1.run.app"

#: Wide enough that the console's three-column cards are not stacked, and exactly the
#: dimension the shipped file is checked against later.
VIEWPORT = {"width": 1920, "height": 1080}


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


def beat(page: Any, label: str, seconds: float) -> None:
    """Hold on a beat, and say which one on stdout so a failed run is diagnosable."""
    print(f"    {label:38} hold {seconds:>5.1f}s", flush=True)
    page.wait_for_timeout(int(seconds * 1000))


def capture(url: str) -> Path:
    from playwright.sync_api import sync_playwright

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
        page.on("pageerror", lambda e: errors.append(str(e)))

        print("  beat 1, the console loads")
        page.goto(url, wait_until="domcontentloaded")
        beat(page, "hook, console visible", 19.4)

        print("  beat 2, the river, live")
        page.select_option("#basin", "shasta")
        page.fill("#cfs", "45.3")
        page.fill("#at", "2026-06-16")
        before = page.locator("#out").get_attribute("data-render") or ""
        page.click("#go")
        before = settle(page, "#out", before, timeout=90_000)
        beat(page, "live classification on screen", 10.0)
        page.evaluate("document.querySelector('#rec').scrollIntoView({block:'center'})")
        beat(page, "allocation core and its ledger", 14.2)

        print("  beat 3, the refusal")
        # 54.5 against a 50 cfs minimum sits inside the near-threshold band, so the
        # engine declines to order and asks for a field measurement instead. This is the
        # single most important thing on screen: the system NOT acting, and saying why.
        page.fill("#cfs", "54.5")
        page.fill("#at", "2026-08-16")
        page.click("#go")
        settle(page, "#out", before, timeout=90_000)
        beat(page, "near-threshold, declines to order", 34.2)

        print("  beat 4, the fleet, uncut")
        page.evaluate("document.querySelector('#fleet').scrollIntoView({block:'center'})")
        page.fill("#cfs", "45.3")
        page.fill("#at", "2026-06-16")
        fleet_before = page.locator("#fleetout").get_attribute("data-render") or ""
        started = time.monotonic()
        page.click("#fleet")
        # The RESULT, not the pending state. See settle(): the first take matched the
        # spinner and reported this 63-second traversal as finishing in 0.0 seconds.
        settle(page, "#fleetout", fleet_before, timeout=300_000)
        elapsed = time.monotonic() - started
        print(f"    traversal completed in {elapsed:.1f}s")
        if elapsed < 5:
            raise RuntimeError(
                f"the traversal returned in {elapsed:.1f}s, which is not a real Gemini "
                "call. The capture would show a spinner rather than the product."
            )
        beat(page, "every node attributed", 8.0)

        print("  beat 5, what it proves and refuses")
        page.evaluate(
            "const el = document.querySelector('#bt');if (el) el.scrollIntoView({block: 'center'});"
        )
        beat(page, "backtest", 12.0)
        page.fill("#q", "when was curtailment lifted after a gage revision")
        search_before = page.locator("#searchout").get_attribute("data-render") or ""
        page.click("#ask")
        settle(page, "#searchout", search_before, timeout=120_000)
        beat(page, "corpus search answered", 21.6)

        print("  beat 6 and 7 are captured separately")
        beat(page, "tail", 4.0)

        context.close()
        browser.close()

    videos = sorted(OUT.glob("*.webm"))
    if not videos:
        raise RuntimeError("no video was written")
    if errors:
        # A page error during the take means the film would show a broken product. Better
        # to know now than to discover it in the shipped file.
        print(f"\n  PAGE ERRORS DURING CAPTURE: {errors}", file=sys.stderr)
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
