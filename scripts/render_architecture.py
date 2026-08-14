"""Render docs/architecture.html to the PNG the Devpost submission requires.

Devpost field 28092 is a REQUIRED file upload and accepts png/jpg/pdf/ppt/pptx. The
HTML is the source of truth and this produces the artifact from it, so the diagram is
regenerated rather than redrawn whenever the architecture changes.

Fails loudly on a blank or connectionless render. A diagram whose arrows silently did
not draw still looks like a diagram, which is the shape of every false green this
project has already been bitten by.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "docs" / "architecture.html"
TARGET = REPO / "docs" / "architecture.png"

#: Below this, the render is a blank page or a stylesheet failure rather than a diagram.
MIN_BYTES = 60_000


def main() -> int:
    if not SOURCE.exists():
        print(f"MISSING {SOURCE}", file=sys.stderr)
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 1200}, device_scale_factor=2)
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{SOURCE}", wait_until="networkidle")
        page.wait_for_function("document.body.dataset.connsRendered !== undefined", timeout=15_000)

        drawn = int(page.evaluate("document.body.dataset.connsRendered"))
        paths = page.locator("#connOverlay path.conn").count()
        page.screenshot(path=str(TARGET), full_page=True)
        browser.close()

    if errors:
        print("PAGE ERRORS:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    if drawn != paths:
        print(f"RESOLVED {drawn} connections but drew {paths} paths", file=sys.stderr)
        return 1

    size = TARGET.stat().st_size
    if size < MIN_BYTES:
        print(f"RENDER TOO SMALL: {size} bytes, expected at least {MIN_BYTES}", file=sys.stderr)
        return 1

    print(f"WROTE {TARGET.relative_to(REPO)} {size} bytes, {paths} connections drawn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
