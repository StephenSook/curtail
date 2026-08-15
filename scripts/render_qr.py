"""Generate the install QR code from the canonical service URL.

**A QR code is the one artifact a reviewer cannot proofread.** Printed in a README or
held up in a video it is an opaque square, so a wrong URL, a stale host, or a swapped
image is invisible to every human who looks at it. That is why this is generated rather
than pasted, and why `--check` re-generates and compares bytes: encoding is
deterministic, so a byte-identical result is proof the committed PNG encodes exactly
this URL and nothing else.

It points at the FIELD route, because that is the thing worth scanning from a phone.
Scanning it opens the installable surface directly, and the phone offers to add it to
the home screen from there.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import segno

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "docs" / "field-qr.png"

#: The one URL this code may encode. Named here so the check has something to compare
#: against, and so changing the host is a code change that CI notices.
FIELD_URL = "https://curtail-console-api-672785135387.us-central1.run.app/field"


def build() -> bytes:
    """The PNG bytes for the field URL.

    Error correction M: enough redundancy to survive a phone camera at an angle in a
    lit room, without inflating the module count so far that it stops scanning from a
    laptop screen during a demo.
    """
    from io import BytesIO

    code = segno.make(FIELD_URL, error="m")
    buffer = BytesIO()
    code.save(buffer, kind="png", scale=10, border=3, dark="#0A0A0B", light="#FFFFFF")
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if the committed code has drifted"
    )
    args = parser.parse_args(argv)

    fresh = build()
    if not args.check:
        TARGET.write_bytes(fresh)
        print(f"wrote {TARGET.relative_to(REPO)}  {len(fresh):,} bytes  -> {FIELD_URL}")
        return 0

    if not TARGET.exists():
        print(f"{TARGET.relative_to(REPO)} does not exist. Run without --check.", file=sys.stderr)
        return 1
    if TARGET.read_bytes() != fresh:
        print(
            f"{TARGET.relative_to(REPO)} does not encode {FIELD_URL}. A QR code nobody "
            "can read is a link nobody can check, so this is a hard failure.",
            file=sys.stderr,
        )
        return 1
    print(f"{TARGET.relative_to(REPO)} encodes {FIELD_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
