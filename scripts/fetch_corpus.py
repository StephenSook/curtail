"""Fetch the order corpus from the State Water Board, with verification.

Every document in `data/corpus_manifest.json` that carries a URL is downloaded
to `data/corpus/` (gitignored: the PDFs are public and reproducible, and a repo
is not a document archive).

Verification is the point. A 200 response is not a PDF. The Board's site returns
HTML error pages with a 200 status for some missing paths, and a corpus quietly
containing three error pages would produce a backtest that silently skipped
three documents while reporting a complete run. So every fetch is checked for
the %PDF- magic bytes and a plausible size before it is recorded as fetched.

Run:  uv run python scripts/fetch_corpus.py [--series scott_2024]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data" / "corpus_manifest.json"
CORPUS_DIR = REPO / "data" / "corpus"

#: Everything on the Board's drought pages hangs off this root.
SITE_ROOT = "https://www.waterboards.ca.gov"
DROUGHT_ROOT = f"{SITE_ROOT}/drought/scott_shasta_rivers/"

#: A real curtailment order is never this small. Anything under it is an error
#: page or a truncated transfer, both of which must not enter the corpus.
MIN_PLAUSIBLE_BYTES = 20_000


@dataclass(frozen=True, slots=True)
class FetchResult:
    key: str
    url: str
    path: Path | None
    ok: bool
    reason: str
    size: int = 0


def _absolute(url: str) -> str:
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return SITE_ROOT + url
    return urljoin(DROUGHT_ROOT, url)


def _verify(body: bytes) -> tuple[bool, str]:
    """A downloaded blob is a usable document, or it is not. No middle state."""
    if not body:
        return False, "empty body"
    if not body.startswith(b"%PDF-"):
        head = body[:60].decode("utf-8", errors="replace").replace("\n", " ")
        return False, f"not a PDF (starts with {head!r})"
    if len(body) < MIN_PLAUSIBLE_BYTES:
        return False, (
            f"implausibly small for an order ({len(body)} bytes). A body of a few "
            "hundred bytes is usually the Imperva WAF challenge stub, which is "
            "served with HTTP 200. Slow down and retry rather than treating it as "
            "a missing document."
        )
    return True, "ok"


def _targets(manifest: dict, only: str | None) -> list[tuple[str, str]]:
    """Every (key, url) pair the manifest actually carries a URL for."""
    out: list[tuple[str, str]] = []
    for series in manifest["series"]:
        if only and series["id"] != only:
            continue
        sid = series["id"]
        for order in series["base_orders"]:
            if order.get("url"):
                out.append((f"{sid}/order/{order['order_number']}", order["url"]))
        for group in ("addenda", "addenda_to_2026_0008"):
            for add in series.get(group, []):
                if add.get("url"):
                    out.append((f"{sid}/{group}/{add['n']}", add["url"]))
    return out


#: Seconds between requests.
#:
#: waterboards.ca.gov sits behind an Imperva WAF that starts returning a
#: 212-byte HTML challenge stub WITH HTTP 200 after roughly ten to twelve PDF
#: fetches at speed, and a 403 under sustained load. That is precisely the
#: false-green this fetcher's magic-byte check exists to catch, and the check
#: does catch it, but a fetcher that trips the block simply fails to collect the
#: corpus. Measured working pace is one request per eight to eleven seconds.
#:
#: This is also a public government server, not a target.
POLITE_DELAY_SECONDS = 8.0


def fetch_all(only: str | None = None, *, delay: float = POLITE_DELAY_SECONDS) -> list[FetchResult]:
    manifest = json.loads(MANIFEST.read_text())
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    results: list[FetchResult] = []

    with httpx.Client(
        timeout=45.0,
        follow_redirects=True,
        headers={"User-Agent": "curtail-research/0.1 (public records retrieval)"},
        transport=httpx.HTTPTransport(retries=2),
    ) as client:
        for key, raw_url in _targets(manifest, only):
            url = _absolute(raw_url)
            dest = CORPUS_DIR / f"{key.replace('/', '__')}.pdf"

            if dest.exists() and dest.stat().st_size >= MIN_PLAUSIBLE_BYTES:
                results.append(FetchResult(key, url, dest, True, "cached", dest.stat().st_size))
                continue

            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                results.append(FetchResult(key, url, None, False, f"transport: {exc}"))
                continue

            if response.status_code != 200:
                results.append(FetchResult(key, url, None, False, f"HTTP {response.status_code}"))
                time.sleep(delay)
                continue

            ok, reason = _verify(response.content)
            if not ok:
                # Deliberately NOT written to disk. A file that exists is a file
                # something downstream will eventually try to parse.
                results.append(FetchResult(key, url, None, False, reason))
                time.sleep(delay)
                continue

            dest.write_bytes(response.content)
            results.append(FetchResult(key, url, dest, True, "fetched", len(response.content)))
            time.sleep(delay)  # the Board's site is a public resource, not a target

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", help="limit to one series id, e.g. scott_2024")
    args = parser.parse_args()

    results = fetch_all(args.series)
    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]

    for r in results:
        mark = "  ok  " if r.ok else " FAIL "
        size = f"{r.size:>9,}" if r.size else " " * 9
        print(f"[{mark}] {size}  {r.key:<44} {r.reason}")

    print()
    print(f"fetched or cached: {len(ok)}    failed: {len(bad)}    total: {len(results)}")

    if bad:
        print()
        print("Failures are NOT written to disk. A corpus containing error pages would")
        print("produce a backtest that silently skipped documents while reporting a")
        print("complete run. Resolve each URL before marking its record document_read.")

    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
