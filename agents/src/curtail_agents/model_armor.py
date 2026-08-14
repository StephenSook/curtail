"""Model Armor: the SECOND injection layer on the Scribe path.

**Layer 1 is ours and runs offline** (`sanitize.py`): it normalises, matches injection
patterns, fences and strips untrusted order text before it can reach a prompt. Google's
own guidance is never to rely on a single defensive measure, so this is the second, and
the governance table named it for weeks as provisioned-but-not-called.

**Why it was believed unreachable, and why that was wrong.** `gcloud model-armor
templates list` returns `PERMISSION_DENIED: Read access to project ... was denied` on this
account, and that was recorded as the API being unavailable. It is not. The REGIONAL REST
endpoint, `modelarmor.us-central1.rep.googleapis.com`, answers **200** for the same
project with the same credentials, lists the template, and `sanitizeUserPrompt` returns
`pi_and_jailbreak: MATCH_FOUND, confidenceLevel: HIGH` on a real injection payload.

The lesson is worth more than the fix: **a CLI refusal is a claim about one client, not
about the API.** The spike itself recorded the regional-endpoint fact and a later
diagnosis reached for the CLI anyway.

**Never fatal.** A screening failure must not stop the surface that serves the curtailment
recommendation. Every path returns a verdict carrying its own reason, including the
reasons for not having screened at all, and the caller decides. Silence would be the worst
outcome: it would let a reader assume the layer ran.

**Chunked, because the injection window is small.** Model Armor's prompt-injection check
has historically been bounded near 512 tokens, so a single 110,000 character order (the
measured maximum in this corpus) would be screened only at its head. Text is split and
every chunk is screened, so a payload buried on page 40 is still seen.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

import structlog

log = structlog.get_logger(__name__)

#: Regional host. **Not the global one**, which is the trap the M0 spike recorded and a
#: later diagnosis walked into anyway.
HOST_TEMPLATE: Final = "https://modelarmor.{location}.rep.googleapis.com/v1"

DEFAULT_LOCATION: Final = "us-central1"

#: Template provisioned during the M0 spike: prompt-injection and jailbreak filtering,
#: malicious URI filtering, and RAI filters at MEDIUM_AND_ABOVE.
DEFAULT_TEMPLATE: Final = "curtail-scribe-spike"

#: Set to any non-empty value to skip screening. The test suite sets it: a suite that
#: calls a real screening API is a suite that fails offline and bills for opinions.
DISABLE_ENV: Final = "CURTAIL_DISABLE_MODEL_ARMOR"

#: Characters per screened chunk.
#:
#: The documented prompt-injection window has historically been about 512 TOKENS. At the
#: usual English ratio near four characters per token that is roughly 2,000 characters,
#: and this stays under it deliberately: screening a chunk the filter silently truncates
#: is the same defect as not screening it, and harder to notice.
CHUNK_CHARS: Final = 1500

#: Cap on chunks screened per document. The largest order measured in this corpus is
#: 110,878 characters, which is 74 chunks. The cap exists so a pathological input cannot
#: turn one draft into hundreds of network calls, and when it bites it is REPORTED rather
#: than silently dropping the tail.
MAX_CHUNKS: Final = 80

TIMEOUT_SECONDS: Final = 20


class ArmorState(StrEnum):
    """What screening actually established. `UNAVAILABLE` is not `CLEAN`."""

    CLEAN = "clean"
    MATCH_FOUND = "match_found"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ArmorVerdict:
    """One screening result, honest about what it did and did not check.

    `reason` is populated on every state, not only failures. A verdict a caller cannot
    explain is a verdict a caller will present as a pass.
    """

    state: ArmorState
    reason: str
    #: Filter names that matched, e.g. `pi_and_jailbreak`, `malicious_uris`, `rai`.
    matched_filters: tuple[str, ...] = ()
    chunks_screened: int = 0
    chunks_total: int = 0

    @property
    def screened(self) -> bool:
        """Whether the layer actually ran. **Never conflate with `clean`.**"""
        return self.state is not ArmorState.UNAVAILABLE

    @property
    def blocked(self) -> bool:
        return self.state is ArmorState.MATCH_FOUND


def _chunks(text: str) -> list[str]:
    return [text[i : i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)] or [""]


def _token() -> str | None:
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default()
        # Untyped in google-auth, like ADK's session services elsewhere here.
        credentials.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call]
        token: str | None = credentials.token
        return token
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("model armor credentials unavailable", error=str(exc))
        return None


def _screen_chunk(chunk: str, *, url: str, token: str) -> tuple[bool, list[str], str | None]:
    """Screen one chunk. Returns (matched, filter names, error)."""
    body = json.dumps({"user_prompt_data": {"text": chunk}}).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, [], f"{type(exc).__name__}: {exc}"

    result = payload.get("sanitizationResult") or {}
    if result.get("filterMatchState") != "MATCH_FOUND":
        return False, [], None
    matched = [
        name for name, detail in (result.get("filterResults") or {}).items() if _match_in(detail)
    ]
    return True, matched, None


def _match_in(detail: Any) -> bool:
    """Whether one filter's result block reports a match.

    The API nests the verdict one level deeper than the filter name, and the inner key
    differs per filter, so this looks for the state rather than assuming a shape. A
    hard-coded path would silently report every filter as clean the first time Google
    added one.
    """
    if not isinstance(detail, dict):
        return False
    for value in detail.values():
        if isinstance(value, dict) and value.get("matchState") == "MATCH_FOUND":
            return True
    return False


def screen_document(
    text: str,
    *,
    project_id: str | None = None,
    location: str = DEFAULT_LOCATION,
    template: str = DEFAULT_TEMPLATE,
) -> ArmorVerdict:
    """Screen untrusted document text, chunk by chunk.

    Returns `UNAVAILABLE` with a reason rather than raising, for every environment
    problem. The Scribe path treats an unavailable second layer as exactly that: layer 1
    still ran, and the response says which layers were applied.
    """
    if os.environ.get(DISABLE_ENV):
        return ArmorVerdict(ArmorState.UNAVAILABLE, f"{DISABLE_ENV} is set, so no screening ran")

    project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return ArmorVerdict(
            ArmorState.UNAVAILABLE, "GOOGLE_CLOUD_PROJECT is not set, so no screening ran"
        )

    token = _token()
    if not token:
        return ArmorVerdict(
            ArmorState.UNAVAILABLE, "no Google credentials are available, so no screening ran"
        )

    host = HOST_TEMPLATE.format(location=location)
    url = f"{host}/projects/{project}/locations/{location}/templates/{template}:sanitizeUserPrompt"

    pieces = _chunks(text)
    total = len(pieces)
    screened = 0
    matched_filters: list[str] = []
    for chunk in pieces[:MAX_CHUNKS]:
        hit, names, error = _screen_chunk(chunk, url=url, token=token)
        if error is not None:
            # A partial screen is not a clean screen. Say how far it got.
            return ArmorVerdict(
                ArmorState.UNAVAILABLE,
                f"screening failed after {screened} of {total} chunks: {error}",
                tuple(dict.fromkeys(matched_filters)),
                screened,
                total,
            )
        screened += 1
        if hit:
            matched_filters.extend(names)

    if matched_filters:
        return ArmorVerdict(
            ArmorState.MATCH_FOUND,
            "Model Armor matched " + ", ".join(sorted(set(matched_filters))),
            tuple(dict.fromkeys(matched_filters)),
            screened,
            total,
        )

    if total > MAX_CHUNKS:
        # Reported, never silent. A truncated screen that reads as clean is the shape
        # this project has a rule about: no silent caps.
        return ArmorVerdict(
            ArmorState.UNAVAILABLE,
            f"only the first {MAX_CHUNKS} of {total} chunks were screened, so the tail "
            "of this document was NOT checked by Model Armor",
            (),
            screened,
            total,
        )

    return ArmorVerdict(
        ArmorState.CLEAN,
        f"Model Armor screened {screened} of {total} chunks and matched nothing",
        (),
        screened,
        total,
    )
