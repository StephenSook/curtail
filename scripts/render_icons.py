"""Derive the launcher icons from the committed source art.

The source is `data/icons/source.png`, generated once with an image model and cropped to
its artwork, then committed. Everything the manifest names is derived from it here, so
the three files cannot drift from each other or from the art.

**The maskable variant scales the CONTENT, not the canvas.** The first attempt resized a
flex container whose children had fixed sizes, which changed nothing: it produced two
byte-identical files, one of them claiming to be maskable. Android crops a maskable icon
to a circle and keeps roughly the inner 80 percent, so the artwork is scaled to 72
percent on a full-bleed background and the corners it throws away are background.

Run:  uv run python scripts/render_icons.py
Check: uv run python scripts/render_icons.py --check
"""

from __future__ import annotations

import argparse
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
ICONS = REPO / "agents" / "src" / "curtail_agents" / "data" / "icons"
SOURCE = ICONS / "source.png"

#: The app background. Any padding the maskable crop discards must be this, not white.
BACKGROUND = (10, 10, 11)

#: (filename, pixel size, fraction of the canvas the artwork occupies)
VARIANTS: tuple[tuple[str, int, float], ...] = (
    ("icon-192.png", 192, 1.0),
    ("icon-512.png", 512, 1.0),
    ("icon-maskable-512.png", 512, 0.72),
)


def render(size: int, scale: float) -> bytes:
    art = Image.open(SOURCE).convert("RGB")
    canvas = Image.new("RGB", (size, size), BACKGROUND)
    inner = max(1, int(size * scale))
    canvas.paste(art.resize((inner, inner), Image.LANCZOS), ((size - inner) // 2,) * 2)
    buffer = BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if an icon has drifted")
    args = parser.parse_args(argv)

    if not SOURCE.exists():
        print(f"MISSING {SOURCE}", file=sys.stderr)
        return 1

    drifted: list[str] = []
    for name, size, scale in VARIANTS:
        fresh = render(size, scale)
        target = ICONS / name
        if args.check:
            if not target.exists() or target.read_bytes() != fresh:
                drifted.append(name)
            continue
        target.write_bytes(fresh)
        print(f"wrote {name}  {size}x{size}  artwork at {scale:.0%}  {len(fresh):,} bytes")

    if args.check:
        if drifted:
            print(f"these icons do not match the source art: {drifted}", file=sys.stderr)
            return 1
        print("every icon matches the source art")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
