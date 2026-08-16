"""Speak a recommendation aloud, and return the exact words that were spoken.

**Two reasons this is not decoration, and the second is the real one.**

A watermaster reads a drafted order on a screen, and this project's own evidence says
naive review of machine drafts fails: in the Biro study, 35 to 45 percent of erroneous
drafts were submitted entirely unedited while 80 percent of the same reviewers reported
the drafts reduced their workload. A second channel is a second chance to notice. Hearing
"seventy-one rights, none of which the ladder could not place" is a different cognitive
act from seeing it.

And the field surface is used outdoors by somebody holding an instrument in one hand,
possibly gloved, standing in moving water. Reading a screen there is the hard case.

**The script is composed from COMPUTED values and returned with the audio.** This is not a
text-to-speech proxy: a caller cannot hand it a sentence to read. It takes a basin and a
reading, runs the same Allocation Core the console runs, and speaks what the Core
produced. The script comes back in the response so a reader can check the spoken words
against the computed ones, which is the only way a spoken claim is auditable at all.

The disclaimer is spoken, not just displayed. An official who hears a recommendation and
not its limits has been told the wrong thing, and the limit is the point: 23 CCR 875(b)
vests the determination in a named human.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import google.auth
import google.auth.transport.requests
import httpx

from curtail_core.allocation import Recommendation

ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"

#: Chirp 3 HD, confirmed present by listing the API's own voices rather than trusting a
#: docs page: 30 `en-US-Chirp3-HD-*` voices exist. A neutral, unhurried one, because this
#: is read to somebody deciding whether to sign a legal order.
DEFAULT_VOICE = "en-US-Chirp3-HD-Achernar"

#: **Chirp 3 HD returns audio at about -23.6 LUFS integrated, measured, not assumed.**
#:
#: That is broadcast level and roughly 8 LU quieter than the -14 to -16 LUFS a web or
#: video deliverable wants. In a browser it is fine and no normalisation happens here,
#: because the Cloud Run image has no ffmpeg and a server that shells out to one it does
#: not have is a 500 waiting for the first caller.
#:
#: **Anything that renders this into a video MUST normalise and then re-measure the
#: file.** This project has already published narration at -37 LUFS after a check that
#: verified frames and never listened, and a loudness rule that exists in two places did
#: not stop it. Measured with:
#:     ffmpeg -i out.mp3 -af ebur128 -f null -
CHIRP_MEASURED_LUFS = -23.6

#: The API's own per-request ceiling. Bounded here so a long ledger is TRUNCATED
#: deliberately with the truncation stated, rather than rejected by the service or, worse,
#: silently cut mid-sentence somewhere a listener cannot tell.
MAX_CHARACTERS = 4500


class SpeechUnavailableError(RuntimeError):
    """Nothing could be synthesised, and silence must not be returned as audio.

    An empty audio payload plays as nothing, which a listener cannot distinguish from a
    briefing that had nothing to say. That is the same defect as a reading of zero
    standing in for a sensor that did not answer.
    """


@dataclass(frozen=True, slots=True)
class Spoken:
    """Audio, and the exact text it was made from."""

    script: str
    audio_mp3: bytes
    voice: str
    truncated: bool

    @property
    def audio_base64(self) -> str:
        return base64.b64encode(self.audio_mp3).decode()


def brief(recommendation: Recommendation, *, observed_cfs: float, minimum_cfs: float) -> str:
    """The spoken script, built only from what the Core computed.

    Numbers are spelled the way they should be heard. "46.5 cfs" read literally becomes
    "forty six point five see eff ess", so the unit is expanded to "cubic feet per
    second" once and the figures are left as digits, which the voice reads correctly.
    """
    action = recommendation.action.value.replace("_", " ")
    reached = len(recommendation.ledger)

    lines = [
        f"Curtail briefing for the {recommendation.basin.value} river.",
        f"The gage reads {observed_cfs} cubic feet per second "
        f"against a minimum of {minimum_cfs} in force today.",
        f"The recommended action is {action}.",
        f"The allocation core evaluated {reached} water rights.",
    ]

    if recommendation.judgment_inputs:
        lines.append(
            f"There are {len(recommendation.judgment_inputs)} judgment inputs "
            "the system does not resolve, and they are for the official to weigh."
        )
        # The first one, in full. A count alone tells a listener that something was
        # withheld without telling them what, which is worse than saying nothing.
        lines.append(str(recommendation.judgment_inputs[0]))

    lines.append(
        "This is a recommendation. Section 875 b of title 23 vests the determination "
        "in a named human official, and nothing this system produces takes effect on "
        "its own."
    )
    return " ".join(lines)


def synthesise(
    script: str,
    *,
    voice: str = DEFAULT_VOICE,
    client: httpx.Client | None = None,
    credentials: Any = None,
) -> Spoken:
    """Turn a script into audio, or refuse.

    Raises:
        SpeechUnavailableError: on any failure. It never returns empty audio, because a
            zero-length payload plays as silence and a listener cannot tell silence from
            a briefing with nothing in it.
    """
    if not script.strip():
        raise SpeechUnavailableError("there is no script to speak")

    truncated = len(script) > MAX_CHARACTERS
    if truncated:
        # Cut at a sentence boundary and SAY that it was cut. A briefing that stops
        # mid-clause sounds like a dropped connection.
        head = script[:MAX_CHARACTERS].rsplit(". ", 1)[0]
        script = f"{head}. The rest of this briefing was too long to speak and is on screen."

    try:
        creds = credentials
        project: str | None = None
        if creds is None:
            creds, project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        creds.refresh(google.auth.transport.requests.Request())
    except Exception as exc:
        raise SpeechUnavailableError(f"could not obtain credentials: {exc}") from exc

    # **A quota project is required and its absence is a 403, not a 401.** Under local
    # Application Default Credentials this API refuses without an `x-goog-user-project`
    # header, and the error reads like a permissions problem rather than a missing
    # header. Resolved here and REFUSED explicitly when unknown, because sending a
    # request that is certain to fail produces a confusing error somebody else has to
    # diagnose.
    quota_project = (
        getattr(creds, "quota_project_id", None)
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or project
    )
    if not quota_project:
        raise SpeechUnavailableError(
            "no quota project is set. Text-to-Speech refuses local Application Default "
            "Credentials without one. Set GOOGLE_CLOUD_PROJECT."
        )

    payload = {
        "input": {"text": script},
        "voice": {"languageCode": "en-US", "name": voice},
        # MP3 rather than LINEAR16: a browser plays it without a decoder and the payload
        # is small enough to inline in a JSON response.
        "audioConfig": {"audioEncoding": "MP3"},
    }
    owns = client is None
    http = client or httpx.Client(timeout=30.0)
    try:
        response = http.post(
            ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "x-goog-user-project": quota_project,
            },
        )
    except httpx.HTTPError as exc:
        raise SpeechUnavailableError(f"text-to-speech was unreachable: {exc}") from exc
    finally:
        if owns:
            http.close()

    if response.status_code != 200:
        raise SpeechUnavailableError(
            f"text-to-speech returned HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise SpeechUnavailableError("text-to-speech returned a non-JSON body") from exc

    encoded = body.get("audioContent")
    if not encoded:
        # A 200 with no audio. Same class as every other status-code-says-fine failure
        # this project has been bitten by.
        raise SpeechUnavailableError("text-to-speech returned no audio content")

    try:
        audio = base64.b64decode(encoded)
    except Exception as exc:
        raise SpeechUnavailableError(f"the audio payload was not base64: {exc}") from exc
    if len(audio) < 512:
        raise SpeechUnavailableError(
            f"the audio payload is {len(audio)} bytes, which is too short to be speech"
        )

    return Spoken(script=script, audio_mp3=audio, voice=voice, truncated=truncated)


__all__ = [
    "DEFAULT_VOICE",
    "MAX_CHARACTERS",
    "SpeechUnavailableError",
    "Spoken",
    "brief",
    "synthesise",
]
