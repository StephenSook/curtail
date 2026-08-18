"""The submission-day gate: everything checkable, checked, in one command.

**Why a gate and not a checklist.** A checklist is prose, and this project has spent a
lot of effort learning that prose about a requirement is not the requirement. Every item
below either runs a real check or reports honestly that it CANNOT, and the exit code is
the verdict.

**It refuses to say READY while anything a human must supply is missing**, because the
failure mode near a deadline is not forgetting to run the tests: it is a green suite
being read as "the submission is done" when the video does not exist.

Ordered so the cheap offline checks fail first and the ones needing credentials or a
network come last, so a broken repository is not diagnosed by a Cloud Run probe.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    command: list[str] | None
    #: True when this needs credentials or the network, so it can be skipped
    #: DELIBERATELY and reported as unrun rather than silently passing.
    needs_network: bool = False
    #: Set when a human must supply something no command can check.
    human: str | None = None


STEPS: tuple[Step, ...] = (
    Step("lint, format", ["make", "lint"]),
    Step("types", ["make", "types"]),
    Step("tests and coverage", ["make", "test"]),
    Step("AI tone and em-dash", ["make", "tone"]),
    Step("browser suite", ["make", "test-browser"]),
    Step("fact sheet is current", ["uv", "run", "python", "scripts/generate_facts.py", "--check"]),
    Step(
        "submission sheet is current",
        ["uv", "run", "python", "scripts/generate_submission.py", "--check"],
    ),
    Step("rights record matches its source", ["make", "rights-check"]),
    Step("Scott record matches its source", ["make", "scott-rights-check"]),
    Step(
        "the deployed service still serves what we claim",
        ["make", "deployed-check"],
        needs_network=True,
    ),
    Step("the chaos drill, every layer", ["make", "chaos-recording"], needs_network=True),
    # The human items are READ FROM A FILE, not hardcoded, so this gate can actually
    # reach READY. The first version listed them as permanent facts, which made it
    # exit non-zero forever: a gate that cannot pass is a gate people stop running,
    # and that is the failure this project keeps naming from the other direction.
)

#: What no command can derive, recorded as it becomes true.
LINKS = REPO / "docs" / "submission_links.json"


def _shown(path: Path) -> str:
    """A readable path, whether or not it lives under the repository.

    `relative_to` RAISES when it does not, and the only caller is the NOT READY message,
    so a crash would land on the failure path and nowhere else. Found by a test pointing
    LINKS at a temporary directory, which is exactly what a test should do.
    """
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _human_items(*, verify: bool = True) -> list[tuple[str, str, bool]]:
    """(name, detail, satisfied) for each thing a person must supply.

    The video is VERIFIED rather than trusted: YouTube's oembed endpoint answers 200
    for a public video and 400 for one that is missing or private, which is exactly
    the distinction the rules care about, since the content must be public rather than
    unlisted. A URL somebody typed proves only that they typed it.

    **`verify=False` makes no network call, and that is a contract not a preference.**
    `--offline` promised no network and then reached out to YouTube from here, because
    the offline switch guarded the STEP list and this function is not in it. A review
    found it; the tests could not, because they mocked this function away and a mock
    cannot violate the contract of the thing it replaced.
    """
    if not LINKS.exists():
        return [("submission links file", f"{LINKS.name} is missing entirely", False)]
    data = json.loads(LINKS.read_text())

    items: list[tuple[str, str, bool]] = []

    video = str(data.get("video_url", "")).strip()
    if not video:
        items.append(("demo video published", "a public YouTube or Vimeo URL, 4 min max", False))
    elif not verify:
        items.append(
            (
                f"demo video ({video[:48]})",
                "recorded but NOT verified: --offline makes no network call, so whether "
                "this URL is public is unknown",
                False,
            )
        )
    else:
        public, why = _video_is_public(video)
        items.append((f"demo video ({video[:48]})", why, public))

    org = str(data.get("organization_name", "")).strip()
    items.append(
        ("organization name", "Devpost field 28086 is required despite reading optional", bool(org))
    )

    uploaded = str(data.get("diagram_uploaded_at", "")).strip()
    # **Report the evidence actually held, which is not always the weakest kind.**
    # This read "SELF-CERTIFIED: no API can confirm it" unconditionally, and by the time
    # the upload happened that was false: Devpost returned an attachment id and a byte
    # count matching the local file. A gate that understates its own evidence is the
    # same drift as one that overstates it, and the README already shipped the
    # understating version of this mistake once.
    evidence = str(data.get("_diagram_evidence", "")).strip()
    items.append(
        (
            "architecture diagram uploaded",
            evidence or "SELF-CERTIFIED: no API can confirm it, so this is your word, dated",
            bool(uploaded),
        )
    )

    # The bonuses are optional, so an EMPTY record blocks nothing. A RECORDED one is a
    # claim standing on the Devpost form, so once present it must verify here or the
    # gate refuses: the generator shape-checks these URLs, and shape cannot see a dead
    # post, a private one, or one missing the language the rules require. Same
    # verify-or-refuse contract as the video, including the --offline half.
    content = str(data.get("content_bonus_url", "")).strip()
    if content:
        if not verify:
            items.append(
                (
                    f"content bonus ({content[:48]})",
                    "recorded but NOT verified: --offline makes no network call",
                    False,
                )
            )
        else:
            ok, why = _content_is_public_with_language(content)
            items.append((f"content bonus ({content[:48]})", why, ok))

    social = str(data.get("social_bonus_url", "")).strip()
    if social:
        if not verify:
            items.append(
                (
                    f"social bonus ({social[:48]})",
                    "recorded but NOT verified: --offline makes no network call",
                    False,
                )
            )
        else:
            ok, why = _social_post_is_public_with_hashtag(social)
            items.append((f"social bonus ({social[:48]})", why, ok))
    return items


#: The rules' own requirement for the content bonus, quoted at the substring every
#: honest phrasing must contain: "language that says you created the piece of content
#: for the purposes of entering this hackathon".
REQUIRED_CONTENT_LANGUAGE = "for the purposes of entering"

#: The hashtag the rules require on the social post.
REQUIRED_HASHTAG = "#AllThingsAgenticHackathon"


def _fetch(url: str) -> str:
    """The body a logged-out reader gets. Raises on anything but success."""
    request = urllib.request.Request(url, headers={"User-Agent": "curtail-pre-submit"})
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return str(response.read().decode("utf-8", errors="replace"))


def _visible_text(html: str) -> str:
    """The text a reader can see, whitespace-normalised.

    A raw-HTML substring search can be satisfied by an og:description attribute, an
    HTML comment, or a script payload, none of which a judge reads. The stdlib parser
    drops all three for free: attribute values and comments never reach
    `handle_data`, and script/style/noscript/template subtrees are skipped
    explicitly. Reader comments rendered as page text remain visible text, which is
    the honest boundary of what a fetch can distinguish.
    """
    from html.parser import HTMLParser

    class Collector(HTMLParser):
        SKIP = frozenset({"script", "style", "noscript", "template"})

        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: object) -> None:
            if tag in self.SKIP:
                self.depth += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in self.SKIP and self.depth:
                self.depth -= 1

        def handle_data(self, data: str) -> None:
            if not self.depth:
                self.parts.append(data)

    collector = Collector()
    collector.feed(html)
    return " ".join(" ".join(collector.parts).split())


def _content_is_public_with_language(url: str) -> tuple[bool, str]:
    """Public AND carrying the required created-for-this-hackathon language.

    Fetched cookie-less, so this is what a judge gets, not what the author sees while
    signed in. Both halves matter: a live post without the language fails the bonus
    exactly as hard as a dead link. The phrase must appear in the page's VISIBLE
    text, because a match inside metadata or an HTML comment is not language the
    piece of content actually says.
    """
    try:
        body = _fetch(url)
    except Exception as exc:
        return False, f"NOT reachable logged-out ({str(exc)[:60]})"
    if REQUIRED_CONTENT_LANGUAGE not in _visible_text(body):
        return False, (
            f"reachable, but the page's visible text lacks the required language "
            f"({REQUIRED_CONTENT_LANGUAGE!r})"
        )
    return True, "public, and the visible text carries the required language"


def _social_post_is_public_with_hashtag(url: str) -> tuple[bool, str]:
    """Public AND carrying the required hashtag, via the platform's oembed endpoint.

    X serves post pages through a login-walled SPA, so fetching the page proves
    nothing; the oembed endpoint answers anonymously for PUBLIC posts only and its
    payload carries the post text, which is where the hashtag must appear. Platforms
    without an anonymous probe are refused rather than waved through: record the
    verification by hand in the evidence note and this stays a conscious check.
    """
    if "x.com/" in url or "twitter.com/" in url:
        probe = "https://publish.twitter.com/oembed?url=" + urllib.parse.quote(
            url.replace("//x.com/", "//twitter.com/"), safe=""
        )
        try:
            payload = _fetch(probe)
        except Exception as exc:
            return False, f"NOT public per oembed ({str(exc)[:60]})"
        if REQUIRED_HASHTAG.lower() not in payload.lower():
            return False, f"public, but the post text lacks {REQUIRED_HASHTAG}"
        return True, f"public per oembed, and the post carries {REQUIRED_HASHTAG}"
    return False, ("no anonymous publicness probe for this platform; verify by hand and record it")


def _video_is_public(url: str) -> tuple[bool, str]:
    """Public, or merely typed. Verified through the provider, never assumed."""
    if "youtube.com" in url or "youtu.be" in url:
        probe = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url, safe='')}&format=json"
    elif "vimeo.com" in url:
        probe = f"https://vimeo.com/api/oembed.json?url={urllib.parse.quote(url, safe='')}"
    else:
        return False, "not a YouTube or Vimeo URL, and the rules name those two"
    try:
        with urllib.request.urlopen(probe, timeout=20) as response:
            title = json.loads(response.read().decode()).get("title", "")
        return True, f"public, titled {title[:48]!r}"
    except Exception as exc:
        return False, f"NOT reachable as public ({str(exc)[:60]}). Unlisted counts as not public."


def _run(step: Step) -> tuple[bool, str]:
    assert step.command is not None
    result = subprocess.run(step.command, cwd=REPO, capture_output=True, text=True)
    if result.returncode == 0:
        return True, "ok"
    tail = (result.stdout + result.stderr).strip().splitlines()
    return False, (tail[-1][:120] if tail else f"exit {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The submission-day gate.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "skip the steps needing credentials or a network. NEVER reports READY: a "
            "skipped check is not a passed one, so this always exits 3"
        ),
    )
    args = parser.parse_args(argv)

    print("CURTAIL pre-submission gate\n")
    failed: list[str] = []
    skipped: list[str] = []
    outstanding: list[str] = []

    for step in STEPS:
        if step.needs_network and args.offline:
            skipped.append(step.name)
            print(f"[SKIP]   {step.name}  (--offline)")
            continue
        ok, detail = _run(step)
        print(f"[{'PASS' if ok else 'FAIL'}]   {step.name}" + ("" if ok else f"  {detail}"))
        if not ok:
            failed.append(step.name)

    for name, detail, satisfied in _human_items(verify=not args.offline):
        if satisfied:
            print(f"[PASS]   {name}  {detail}")
        else:
            outstanding.append(f"{name}: {detail}")
            print(f"[TODO]   {name}")

    print()
    if failed:
        print(f"NOT READY. {len(failed)} check(s) failed: {', '.join(failed)}")
        return 1

    # **A SKIPPED check blocks READY, and this is the third time in one session that a
    # caveat was printed while the exit code said otherwise.** The drill did it, the
    # durability report did it, and here `--offline` printed "prove nothing" and then
    # returned 0 anyway once the human items were filled. Printing a warning is not
    # gating on it, and the exit code is what a pipeline and a tired human both read.
    if skipped:
        print(
            f"NOT READY. {len(skipped)} check(s) were SKIPPED and prove nothing: "
            f"{', '.join(skipped)}. Re-run without --offline before submitting."
        )
        return 3

    if outstanding:
        print("Everything checkable passes. Still outstanding:")
        for item in outstanding:
            print(f"  - {item}")
        print(
            f"\nNOT READY. Record these in {_shown(LINKS)} as they become true. A green "
            "suite is 'nothing automatable is broken', which is not the same as a "
            "submitted entry."
        )
        return 2

    print("READY. Every check ran, every check passed, every human item is verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
