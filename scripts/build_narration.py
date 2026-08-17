"""Build the demo narration with Chirp 3 HD, and refuse to hand back a file it has not measured.

**`docs/video/script.md` is the only source of spoken words.** The beat texts are parsed
out of it rather than kept here, because two copies of a narration drift and a published
video cannot be corrected afterwards. Editing the shot list edits the film.

Three gates, each of which exists because the failure it catches is invisible:

1. **Every figure spoken must appear in `docs/FACTS.md`.** A number in narration is a
   claim, the video is immutable once uploaded, and a previous project shipped a spoken
   figure that had come from a memory note with its qualifier stripped.
2. **The concatenated duration must equal the sum of the parts.** ffmpeg's concat
   demuxer has silently dropped about a fifth of a narration on this machine before,
   and the result plays fine, just short. Nothing errors.
3. **The final file is measured with ebur128 and must land between -16 and -14 LUFS.**
   Chirp 3 HD returns about -23.6 LUFS, which is eight units quieter than a video
   deliverable wants. This project has already published narration at -37 LUFS after a
   check that verified frames and never listened.

    uv run python scripts/build_narration.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "agents" / "src"))

SCRIPT = REPO / "docs" / "video" / "script.md"
FACTS = REPO / "docs" / "FACTS.md"
OUT = REPO / "docs" / "video" / "audio"

#: Silence between beats. Long enough that the beats read as separate thoughts, short
#: enough that a four minute cap survives eight of them.
GAP_SECONDS = 0.55

#: Loudness the shipped file is normalised to, and the window it is then verified inside.
#: The window is checked on the OUTPUT, because loudnorm's single-pass mode is an
#: estimate and an estimate is not a measurement.
TARGET_LUFS = -15.0
LUFS_WINDOW = (-16.0, -14.0)

#: Applied to the spoken text ONLY. The written script keeps the real identifier because
#: that is what a reader needs to see; a speech engine reading "001" aloud as digits in
#: the middle of a sentence sounds like a fault.
SAY_AS = {
    "gemini-embedding-001": "gemini embedding, the zero zero one model",
    "Gemini\n> 3.5": "Gemini three point five",
    "Gemini 3.5": "Gemini three point five",
    "Gemma 3": "Gemma three",
    "Chirp 3": "Chirp three",
    "23 CCR": "title twenty three of the California Code of Regulations, section",
}

#: Figures that are ordinary English rather than claims, so the fact-sheet gate does not
#: demand a source for them.
NOT_A_CLAIM = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "one", "two"}


class NarrationError(RuntimeError):
    """The narration is not usable. Distinct from the builder being broken."""


def run(*args: str) -> str:
    done = subprocess.run(args, capture_output=True, text=True)
    if done.returncode != 0:
        raise NarrationError(f"{args[0]} failed: {done.stderr.strip()[:400]}")
    return done.stdout


def duration(path: Path) -> float:
    return float(
        run(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ).strip()
    )


def parse_beats(text: str) -> list[tuple[str, str]]:
    """Pull (title, spoken text) out of the shot list.

    Only blockquote lines are spoken. Everything else in the file, the screen directions
    and the source attributions, is for a human building the film.
    """
    beats: list[tuple[str, str]] = []
    title = ""
    lines: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^## Beat (\d+)(?:, (.+?))? \(", line)
        if heading:
            if title and lines:
                beats.append((title, " ".join(lines)))
            title, lines = f"beat{heading.group(1)}", []
            continue
        if line.startswith("## ") and title:
            beats.append((title, " ".join(lines)))
            title, lines = "", []
            continue
        if title and line.startswith(">"):
            spoken = line.lstrip(">").strip()
            if spoken:
                lines.append(spoken)
    if title and lines:
        beats.append((title, " ".join(lines)))
    return beats


def check_figures(beats: list[tuple[str, str]], facts: str) -> list[str]:
    """Every number spoken must be findable in the fact sheet.

    Deliberately crude: it compares digit strings, so "$80,000" is checked as "80,000"
    and "80000". A gate that is easy to reason about is one I will still trust at 2am,
    and the cost of a false positive is reading one line of the fact sheet.
    """
    missing: list[str] = []
    normal = facts.replace(",", "")
    for title, spoken in beats:
        for raw in re.findall(r"\d[\d,\.]*", spoken):
            figure = raw.rstrip(".")
            if figure in NOT_A_CLAIM:
                continue
            if figure in facts or figure.replace(",", "") in normal:
                continue
            missing.append(f"{title}: {figure}")
    return missing


def say(text: str) -> str:
    for written, spoken in SAY_AS.items():
        text = text.replace(written, spoken)
    return text


def build() -> int:
    from curtail_agents.speech import DEFAULT_VOICE, synthesise

    beats = parse_beats(SCRIPT.read_text())
    if len(beats) < 6:
        raise NarrationError(
            f"only {len(beats)} beats parsed out of the shot list, which means the "
            "headings changed shape and the film would be missing narration"
        )

    missing = check_figures(beats, FACTS.read_text())
    if missing:
        raise NarrationError(
            "these spoken figures are not in docs/FACTS.md, and a number said on camera "
            f"cannot be corrected afterwards: {missing}"
        )

    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*"):
        stale.unlink()

    words = sum(len(spoken.split()) for _, spoken in beats)
    print(f"  {len(beats)} beats, {words} words, voice {DEFAULT_VOICE}")

    wavs: list[Path] = []
    manifest: list[dict[str, object]] = []
    for title, spoken in beats:
        mp3 = OUT / f"{title}.mp3"
        wav = OUT / f"{title}.wav"
        mp3.write_bytes(synthesise(say(spoken)).audio_mp3)
        # Decode to WAV before concatenating. The concat demuxer over mp3s lost about a
        # fifth of a narration on this machine, and re-encoding did not fix it.
        run("ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
            "-ar", "48000", "-ac", "1", str(wav))
        seconds = duration(wav)
        print(f"    {title:8} {seconds:6.1f}s  {len(spoken.split()):3d} words")
        wavs.append(wav)
        manifest.append({"beat": title, "spoken": spoken, "seconds": round(seconds, 3)})

    silence = OUT / "gap.wav"
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", "anullsrc=r=48000:cl=mono", "-t", str(GAP_SECONDS), str(silence))

    listing = OUT / "concat.txt"
    parts: list[Path] = []
    for index, wav in enumerate(wavs):
        if index:
            parts.append(silence)
        parts.append(wav)
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))

    joined = OUT / "narration_raw.wav"
    run("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(listing), "-c", "copy", str(joined))

    expected = sum(m["seconds"] for m in manifest) + GAP_SECONDS * (len(wavs) - 1)  # type: ignore[operator]
    actual = duration(joined)
    if abs(actual - expected) > 0.25:
        raise NarrationError(
            f"the concatenation is {actual:.1f}s but its parts total {expected:.1f}s. "
            "ffmpeg has dropped audio here before, silently, and the result plays fine."
        )

    final = REPO / "docs" / "video" / "narration.wav"
    # **Two passes, not one.** Single-pass loudnorm is a live estimator: asked for -15 it
    # landed this narration at exactly -16.0, sitting on the edge of the acceptable
    # window where any later re-encode tips it out. The first pass MEASURES the file and
    # the second applies those measurements, which is the difference between a target
    # and a result.
    analysis = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(joined), "-af",
         f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    block = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", analysis, re.DOTALL)
    if not block:
        raise NarrationError("loudnorm's measurement pass reported nothing to apply")
    stats = json.loads(block.group(0))
    run(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-af",
        f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11"
        f":measured_I={stats['input_i']}:measured_TP={stats['input_tp']}"
        f":measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}"
        f":offset={stats['target_offset']}:linear=true",
        "-ar", "48000", str(final),
    )

    # ebur128 writes to stderr, so the verdict is read from there and not from stdout.
    report = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(final), "-af", "ebur128",
         "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    found = re.findall(r"I:\s+(-?\d+\.\d+) LUFS", report)
    if not found:
        raise NarrationError("ebur128 reported no integrated loudness, so nothing was measured")
    integrated = float(found[-1])
    low, high = LUFS_WINDOW
    if not low <= integrated <= high:
        raise NarrationError(
            f"the narration measures {integrated} LUFS, outside {low} to {high}. "
            "Chirp 3 HD ships near -23.6 and a quiet film is an unwatchable one."
        )

    total = duration(final)
    start = 0.0
    for entry in manifest:
        entry["start"] = round(start, 3)
        start += float(entry["seconds"]) + GAP_SECONDS
    (REPO / "docs" / "video" / "beats.json").write_text(
        json.dumps(
            {
                "voice": DEFAULT_VOICE,
                "gap_seconds": GAP_SECONDS,
                "integrated_lufs": integrated,
                "total_seconds": round(total, 3),
                "beats": manifest,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\n  narration {total:.1f}s at {integrated} LUFS -> docs/video/narration.wav")
    if total > 232:
        print(
            f"  WARNING: {total:.1f}s of speech leaves under 8s for the close inside a "
            "240s cap. Trim the shot list."
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(build())
    except NarrationError as exc:
        print(f"\n  narration refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
