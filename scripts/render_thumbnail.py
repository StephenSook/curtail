"""Render docs/thumbnail.html to the PNG Devpost shows in its gallery grid.

Same shape as `render_architecture.py`, and for the same reason: the HTML is the source
and the PNG is generated from it, so the image cannot drift from what it is supposed to
say. A thumbnail is one of the few artifacts a judge sees before deciding whether to
read anything, and it is the one that cannot be corrected after they have seen it.

Fails loudly on a blank render. An image that is technically a PNG and visually empty
still uploads successfully, which is the exact false-green shape this project keeps
finding.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "docs" / "thumbnail.html"
TARGET = REPO / "docs" / "thumbnail.png"

#: Below this the render is a blank page or a stylesheet that never applied.
MIN_BYTES = 20_000


def main() -> int:
    if not SOURCE.exists():
        print(f"MISSING {SOURCE}", file=sys.stderr)
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 800}, device_scale_factor=2)
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{SOURCE}", wait_until="networkidle")
        page.screenshot(path=TARGET)
        browser.close()

    if errors:
        print("render produced console errors:\n  " + "\n  ".join(errors[:5]), file=sys.stderr)
        return 1

    size = TARGET.stat().st_size
    if size < MIN_BYTES:
        print(
            f"{TARGET.name} is {size:,} bytes, under the {MIN_BYTES:,} floor. That is a "
            "blank or unstyled render, not a thumbnail.",
            file=sys.stderr,
        )
        return 1

    print(f"wrote {TARGET.relative_to(REPO)}  {size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
