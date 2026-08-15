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

#: The URLs these codes may encode. Named here so the check has something to compare
#: against, and so changing a host is a code change that CI notices.
FIELD_URL = "https://curtail-console-api-672785135387.us-central1.run.app/field"

#: The signed Android build, published as a GitHub RELEASE asset rather than a CI
#: artifact. Release assets have no retention clock; a CI artifact expires in days and
#: the symptom is a dead download link on a judge-facing page while the repo, the
#: deploy and every check stay green.
APK_URL = "https://github.com/StephenSook/curtail/releases/download/v0.1.0-field/curtail-field.apk"

#: (target filename, url)
CODES: tuple[tuple[str, str], ...] = (
    ("field-qr.png", FIELD_URL),
    ("apk-qr.png", APK_URL),
)


def build(url: str = FIELD_URL) -> bytes:
    """The PNG bytes for the field URL.

    Error correction M: enough redundancy to survive a phone camera at an angle in a
    lit room, without inflating the module count so far that it stops scanning from a
    laptop screen during a demo.
    """
    from io import BytesIO

    code = segno.make(url, error="m")
    buffer = BytesIO()
    code.save(buffer, kind="png", scale=10, border=3, dark="#0A0A0B", light="#FFFFFF")
    return buffer.getvalue()


def _pixels(data: bytes) -> tuple[tuple[int, ...], ...]:
    """Decoded pixels, for the same reason the icon renderer uses them.

    PNG encoder output is not reproducible across platforms, so comparing bytes is a
    check that passes where it was written and fails where it runs. What must not drift
    is the CODE, and the code is the pixels.
    """
    from io import BytesIO

    from PIL import Image

    return tuple(Image.open(BytesIO(data)).convert("RGB").tobytes())  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if the committed code has drifted"
    )
    args = parser.parse_args(argv)

    drifted: list[str] = []
    for name, url in CODES:
        fresh = build(url)
        target = REPO / "docs" / name
        if args.check:
            if not target.exists() or _pixels(target.read_bytes()) != _pixels(fresh):
                drifted.append(name)
            continue
        target.write_bytes(fresh)
        print(f"wrote docs/{name}  {len(fresh):,} bytes  -> {url}")

    if args.check:
        if drifted:
            print(
                f"these codes do not encode their URL: {drifted}. A QR code nobody can "
                "read is a link nobody can check, so this is a hard failure.",
                file=sys.stderr,
            )
            return 1
        print("every QR code encodes its URL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
