"""Capture the Devpost gallery from the LIVE service, not from a mockup.

**Every image here is a screenshot of the deployed console doing the thing it claims.**
The alternative was designed art, and designed art is the gallery equivalent of a frozen
UI state: it shows what the product would look like rather than what it does.

Two things this learned the hard way, both from LOOKING at the output rather than
trusting that the capture succeeded:

1. **Screenshot the SECTION, not the viewport.** A viewport capture gave 40 percent
   empty background down one side, and sliced a sticky table header across the top edge
   of another, so the first thing in frame was a rendering artifact.
2. **Never capture an empty state.** The approval queue read "Nothing is awaiting
   signature", which is the one state a judge must not be shown, so this drafts an order
   first and REFUSES to write the image if the queue is still empty. A gallery image of
   an empty table is worse than no gallery image at all.

Run:  uv run python scripts/capture_gallery.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "gallery"
SERVICE = "https://curtail-console-api-672785135387.us-central1.run.app"

#: Tall and wide enough that a whole card is laid out before the element crop runs. The
#: element screenshot decides the real dimensions, so this is a layout budget, not a size.
VIEWPORT = {"width": 1080, "height": 1400}

#: Each console panel is a `section.card`, so this finds the card that owns a given id.
CARD = "xpath=ancestor::section[contains(@class,'card')][1]"


async def main() -> int:
    from playwright.async_api import async_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    captured: list[tuple[str, str]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        await page.goto(SERVICE, wait_until="networkidle")

        def card(anchor: str):
            return page.locator(anchor).locator(CARD)

        # 1. Classify: the reading behind a real Board order, and its own provenance label.
        await page.fill("#cfs", "48.7")
        await page.click("#go")
        await page.wait_for_function(
            "() => { const e = document.querySelector('#out');"
            " return e && e.textContent.length > 40; }",
            timeout=30_000,
        )
        await card("#out").screenshot(path=OUT / "01-classify.png")
        captured.append(
            (
                "01-classify.png",
                "48.7 cfs at Fort Jones on 20 July 2025, the reading behind Scott "
                "Addendum 7, against a 50 cfs operative minimum.",
            )
        )

        # 2. The per-right ledger, which is the answer to measured automation bias.
        await card("#rec").screenshot(path=OUT / "02-ledger.png")
        captured.append(
            (
                "02-ledger.png",
                "The per-right ledger: each right with its grouping, the subdivision "
                "that put it there, and its disposition. Priority reads 'not stated' "
                "where the record states none.",
            )
        )

        # 3. One real ADK traversal.
        await page.click("#fleet")
        await page.wait_for_function(
            "() => { const e = document.querySelector('#fleetout');"
            " return e && e.textContent.length > 120; }",
            timeout=120_000,
        )
        await card("#fleetout").screenshot(path=OUT / "03-fleet.png")
        captured.append(
            (
                "03-fleet.png",
                "One ADK traversal on the live service. Four agents named by ADK's own "
                "attribution, every node error none, and the draft checked against the "
                "computed ledger before it may go anywhere.",
            )
        )

        # 4. The approval queue, WITH something in it.
        await page.click("#draft")
        # **Wait for the ROW, not for the absence of the empty message.** The first
        # version waited until the panel stopped saying "Nothing is awaiting signature",
        # and the loading state satisfies that too, so it captured a spinner reading
        # "DRAFTING, WHICH CALLS A MODEL AND RUNS ITS GUARDS" and reported success.
        # Absence of one string is not arrival at a state. Drafting calls a model and
        # runs both guards, which measured about 80 seconds, hence the timeout.
        try:
            await page.wait_for_function(
                "() => document.querySelectorAll('#queue tbody tr').length > 0",
                timeout=180_000,
            )
        except Exception as exc:
            raise SystemExit(
                "REFUSING to capture the approval queue: no draft row appeared "
                f"({str(exc)[:80]}). An empty or half-loaded table is the one state a "
                "gallery must never show, so no image is written rather than a "
                "misleading one."
            ) from exc
        await card("#queue").screenshot(path=OUT / "04-queue.png")
        captured.append(
            (
                "04-queue.png",
                "Two drafts queued. One clean and awaiting signature, one flagged "
                "UNVERIFIED with a finding that must be named to override it.",
            )
        )

        await browser.close()

    for name, caption in captured:
        size = (OUT / name).stat().st_size
        print(f"{name}  {size:,} bytes  {caption}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
