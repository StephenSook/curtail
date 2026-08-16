"""Watch the TestFlight build until Apple has done whatever Apple is going to do.

**Two states, and conflating them is the trap.** Internal testing needs NO Beta App
Review: once export compliance is answered the build goes straight to Ready to Test.
Beta App Review only applies to EXTERNAL testers, and only for the first build of a
version. So "has Apple approved it" is the wrong single question, and this reports both:
whether the build is installable by internal testers, and whether a beta review exists
and where it got to.

**UNKNOWN is never reported as NOT APPROVED.** Every failure path here, an unreachable
API, a bad key, an expired token, a build that does not exist, exits non-zero and says
so in those words. This project has already shipped one bug where a failed query became
a confident absence, and a watcher that reads "no approval yet" when it actually means
"I could not ask" is the same defect pointed at a deadline.

Credentials come from the environment and never from this file. The private key is a
credential; its PATH is configuration.

    export ASC_KEY_ID=...           # the key's id, e.g. D697MU6QUF
    export ASC_ISSUER_ID=...        # the team's issuer uuid
    export ASC_APP_ID=6801928911    # Curtail Field
    # ASC_KEY_PATH defaults to ~/.appstoreconnect/private_keys/AuthKey_$ASC_KEY_ID.p8

    uv run python scripts/watch_testflight.py --once      # print state, exit
    uv run python scripts/watch_testflight.py --notify    # print, and notify ON CHANGE
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

API = "https://api.appstoreconnect.apple.com/v1"

#: Where the last observed state is remembered, so `--notify` can fire on a CHANGE
#: rather than on every run. A notifier that fires every ten minutes is a notifier
#: somebody turns off, and then it is not watching anything.
STATE_FILE = Path.home() / ".curtail-testflight-state.json"


#: Internal build states in which the build INSTALLS from TestFlight right now.
#:
#: **Two values, not one, and getting this wrong cost the watcher its whole job.** The
#: first version compared for equality against `READY_FOR_BETA_TESTING` alone. The real
#: build went straight to `IN_BETA_TESTING`, because export compliance was answered on a
#: build already attached to an internal group, so the watcher reported
#: `installable_internally: false` and would have exited 1 meaning "asked, and not yet"
#: at the exact moment the answer became yes.
INSTALLABLE_INTERNAL_STATES = frozenset({"READY_FOR_BETA_TESTING", "IN_BETA_TESTING"})

#: Every internal state Apple documents. A value outside this set is UNKNOWN and says so.
#:
#: The defect above was an equality test against a multi-valued enum, where the omitted
#: value was the GOOD one, so the failure was silent and pointed the wrong way. Listing
#: the domain is what makes a new value loud instead: `main()` exits 2 rather than
#: quietly folding it into "not installable", which is the same rule this file already
#: applies to an unreadable payload and an unreachable API.
KNOWN_INTERNAL_STATES = INSTALLABLE_INTERNAL_STATES | {
    "PROCESSING",
    "PROCESSING_EXCEPTION",
    "MISSING_EXPORT_COMPLIANCE",
    "IN_EXPORT_COMPLIANCE_REVIEW",
    "EXPIRED",
}


class WatchError(RuntimeError):
    """The question could not be asked. Distinct from an answer of 'not yet'."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token(key_id: str, issuer: str, key_path: Path) -> str:
    """A short-lived ES256 assertion, built here rather than pulling in a JWT library.

    Apple caps the lifetime at 20 minutes. The signature must be raw r||s, not the DER
    encoding `cryptography` returns by default, which is the one detail that silently
    produces a 401 that looks like a bad key.
    """
    if not key_path.is_file():
        raise WatchError(f"no private key at {key_path}. Set ASC_KEY_PATH.")
    try:
        private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except Exception as exc:  # not PEM, encrypted, truncated
        raise WatchError(f"{key_path} could not be read as an unencrypted PEM key: {exc}") from exc
    if not isinstance(private, ec.EllipticCurvePrivateKey):
        raise WatchError(f"{key_path} is not an EC private key")
    # ES256 IS P-256. A P-384 key would overflow the 32-byte halves below with an
    # OverflowError rather than a diagnosis, and the whole contract here is that a
    # credential problem exits 2 saying UNKNOWN instead of producing a traceback.
    if not isinstance(private.curve, ec.SECP256R1):
        raise WatchError(
            f"{key_path} is on curve {private.curve.name}. App Store Connect signs with "
            "ES256, which requires P-256 (secp256r1)."
        )

    now = int(time.time())
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    payload = {"iss": issuer, "iat": now, "exp": now + 900, "aud": "appstoreconnect-v1"}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"

    try:
        der = private.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    except Exception as exc:
        raise WatchError(f"could not sign the assertion with {key_path}: {exc}") from exc
    return f"{signing_input}.{_b64(raw)}"


def _get(path: str, bearer: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {bearer}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return dict(json.loads(response.read().decode()))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:300]
        raise WatchError(f"App Store Connect returned {exc.code}: {body}") from exc
    except Exception as exc:  # network, DNS, TLS
        raise WatchError(f"App Store Connect was unreachable: {exc}") from exc


def state() -> dict[str, Any]:
    """What Apple currently says about the newest build.

    Raises:
        WatchError: the question could not be asked. Never returns a shape that a
            caller could mistake for "asked, and the answer is no".
    """
    key_id = os.environ.get("ASC_KEY_ID", "").strip()
    issuer = os.environ.get("ASC_ISSUER_ID", "").strip()
    app_id = os.environ.get("ASC_APP_ID", "").strip()
    if not (key_id and issuer and app_id):
        raise WatchError("ASC_KEY_ID, ASC_ISSUER_ID and ASC_APP_ID must all be set")
    key_path = Path(
        os.environ.get("ASC_KEY_PATH")
        or Path.home() / ".appstoreconnect" / "private_keys" / f"AuthKey_{key_id}.p8"
    )

    bearer = token(key_id, issuer, key_path)
    builds = _get(
        f"/builds?filter[app]={app_id}&limit=1&sort=-version"
        "&include=betaAppReviewSubmission,buildBetaDetail",
        bearer,
    )
    data = builds.get("data") or []
    if not data:
        raise WatchError(
            f"app {app_id} reports no builds at all. That is not 'not approved', it is "
            "'nothing uploaded or the key cannot see this app'."
        )

    # From here down every shape problem raises rather than degrading, because a
    # DEGRADED read is the one outcome this script must never produce. Missing
    # `buildBetaDetail` used to leave `internal` as None, which made
    # `installable_internally` False, which made main() exit 1 and print "not yet". That
    # is UNKNOWN wearing the answer's clothes, and it is the same defect as an errored
    # query reading as a confident absence.
    build = data[0]
    if not isinstance(build, dict):
        raise WatchError(f"the build resource was {type(build).__name__}, not an object")
    attrs = build.get("attributes")
    if not isinstance(attrs, dict):
        raise WatchError("the build carried no attributes object")

    included: dict[str, Any] = {}
    for item in builds.get("included") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            included[item["id"]] = item

    def related(kind: str, *, may_be_absent: bool) -> dict[str, Any] | None:
        """The included resource for one relationship.

        **The thing that separates an answer from a failed read is the `data` VALUE, not
        whether the caller considers the relationship optional.** The first version keyed
        on the caller's flag, so for `betaAppReviewSubmission` a resource Apple said
        EXISTS but that we then failed to read came back as `{}` and was reported as "no
        beta review", which is a degraded read wearing an answer's clothes. That is the
        same defect this function was written to remove, reintroduced one line below the
        fix. Verified against the live envelope, where Apple sends both relationships with
        a `data` member, `data: null` meaning there genuinely is no submission.

        - `data` absent entirely: the linkage was not provided, so this is UNKNOWN.
        - `data` null: Apple says there is no such resource. An ANSWER, and permitted
          only where the caller says absence is meaningful.
        - `data` a reference: the resource EXISTS, so failing to read it is UNKNOWN
          regardless of whether the caller called this optional.
        """
        relationships = build.get("relationships")
        entry = (relationships or {}).get(kind)
        if not isinstance(entry, dict) or "data" not in entry:
            raise WatchError(f"the build carried no {kind} linkage to read")

        ref = entry["data"]
        if ref is None:
            if not may_be_absent:
                raise WatchError(f"the build reports no {kind}, which the verdict needs")
            # None, NOT an empty mapping. A resource that exists with empty attributes
            # is also `{}`, and `{}` is falsy, so returning it here made "Apple says
            # there is none" and "we read it and it was empty" the same value at the
            # call site. The conflation this whole function removes, one level in.
            return None

        if not isinstance(ref, dict) or not isinstance(ref.get("id"), str):
            raise WatchError(f"the {kind} linkage carried an unreadable reference")
        resource = included.get(ref["id"])
        if not isinstance(resource, dict):
            raise WatchError(
                f"{kind} {ref['id']} was referenced but not included, so whether it "
                "applies is UNKNOWN rather than no."
            )
        attributes = resource.get("attributes")
        if not isinstance(attributes, dict):
            raise WatchError(f"{kind} {ref['id']} carried no attributes object")
        return attributes

    beta_detail = related("buildBetaDetail", may_be_absent=False)
    review = related("betaAppReviewSubmission", may_be_absent=True)
    if beta_detail is None:  # unreachable while may_be_absent is False, and stated anyway
        raise WatchError("buildBetaDetail was reported absent, so the verdict is UNKNOWN")

    def field(attributes: dict[str, Any], name: str, resource: str) -> str:
        """One string attribute of a resource we successfully read.

        **A resource that was read but is missing a field is not a resource that says
        no.** `review.get("betaReviewState")` returned None for a real submission whose
        state we could not read, which is the same value this reports when there is no
        submission at all, so a submission in an unreadable state was indistinguishable
        from no submission. The `data: null` fix stopped one level too shallow: having
        the RESOURCE is not the same as having the VALUE.
        """
        value = attributes.get(name)
        if not isinstance(value, str):
            raise WatchError(
                f"{resource} was read but carried no {name}, so its state is UNKNOWN "
                "rather than absent."
            )
        return value

    internal = field(beta_detail, "internalBuildState", "buildBetaDetail")
    external = field(beta_detail, "externalBuildState", "buildBetaDetail")
    # `is None`, NOT truthiness. None means Apple said there is NO submission
    # (`data: null`), which is a fact. A submission that EXISTS with empty attributes is
    # `{}`, which is falsy, so a truthiness test sent it down the same branch and
    # reported "no review" for a submission whose state we simply could not read.
    review_state = (
        None if review is None else field(review, "betaReviewState", "betaAppReviewSubmission")
    )
    return {
        "version": attrs.get("version"),
        "processing": attrs.get("processingState"),
        "expired": attrs.get("expired"),
        "internal_state": internal,
        "external_state": external,
        "beta_review_state": review_state,
        # The one line a human actually wants.
        "installable_internally": internal in INSTALLABLE_INTERNAL_STATES,
        "uploaded_at": attrs.get("uploadedDate"),
    }


def changes(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Every observed field that moved, as `field -> (was, now)`.

    **Compares the whole state, not a hand-picked pair of fields.** The first version
    watched `internal_state` and `beta_review_state` only, so a build that expired, an
    external state that moved, a processing state that changed, or an entirely NEW build
    appearing all passed in silence. Worse, the omission was invisible: adding a field to
    `state()` would not have been noticed here, and the watcher would have gone quiet on
    it forever.

    Every field returned by `state()` is a discrete state or a per-build stamp, none of
    them a clock that ticks, so comparing all of them is quiet in the steady state and
    cannot go stale when a field is added.

    ABSENT is not None. `beta_review_state` is legitimately None on a real read, so a
    plain `.get(key)` would have made it indistinguishable from a field the previous run
    never recorded, and the first run after adding a field would have gone quiet on
    exactly the field that was just added.
    """
    absent = object()  # NOT None: `beta_review_state` is legitimately None on a real read
    return {
        key: (None if (was := previous.get(key, absent)) is absent else was, value)
        for key, value in current.items()
        if previous.get(key, absent) is absent or previous[key] != value
    }


def summarise(current: dict[str, Any]) -> str:
    if current["installable_internally"]:
        return f"READY TO TEST. Build {current['version']} installs from TestFlight now."
    if current["internal_state"] not in KNOWN_INTERNAL_STATES:
        # Loud, not folded into "not installable". An unmodelled value is the shape that
        # produced this whole fix, and next time it should say so rather than guess.
        return (
            f"UNKNOWN internal state {current['internal_state']!r} for build "
            f"{current['version']}. Whether it installs is not something this can say."
        )
    if current["processing"] == "PROCESSING":
        return f"Build {current['version']} is still processing."
    if current["internal_state"] == "MISSING_EXPORT_COMPLIANCE":
        return (
            f"Build {current['version']} needs the export compliance answer. That is a "
            "legal declaration and it is one click in App Store Connect."
        )
    if current["beta_review_state"]:
        return f"Beta App Review (external testers): {current['beta_review_state']}."
    return (
        f"Build {current['version']}: internal={current['internal_state']}, "
        f"external={current['external_state']}, processing={current['processing']}."
    )


def applescript_string(text: str) -> str:
    """`text` as an AppleScript string literal.

    **AppleScript has no single-quoted string.** The first version of this interpolated
    Python's `repr`, which emits single quotes, so the fallback was a syntax error every
    time it ran: `osascript` exits 1 with "Expected given, in, of, expression...".

    It was invisible here because `terminal-notifier` is installed on this machine and
    the fallback never ran. The fallback exists precisely FOR the machine where
    `terminal-notifier` is absent, which is the one machine that would only ever have
    executed the broken branch. Same shape as a guard whose precondition holds only where
    it was written.
    """
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(message: str) -> bool:
    """Raise a desktop notification. Returns whether one was actually delivered.

    It returns a verdict rather than None because the caller PRINTS what happened, and a
    caller that prints "notified" after a swallowed exception is claiming something it
    does not know. This project has shipped that defect before.
    """
    title = "Curtail TestFlight"
    failures: list[str] = []
    for command in (
        ["terminal-notifier", "-title", title, "-message", message],
        [
            "osascript",
            "-e",
            f"display notification {applescript_string(message)} "
            f"with title {applescript_string(title)}",
        ],
    ):
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=15)
            return True
        except Exception as exc:  # not installed, non-zero exit, timeout
            failures.append(f"{command[0]}: {exc}")
    print(f"could not raise a desktop notification ({'; '.join(failures)})", file=sys.stderr)
    return False


def read_baseline() -> dict[str, Any]:
    """The last observation, or an empty mapping if there is not a usable one.

    Anything unreadable degrades to "no history", which re-notifies once rather than
    going quiet. That is the safe direction for a watcher: a duplicate message is a
    nuisance, a missing one is the failure.

    It must be a JSON OBJECT. A bare `null` is valid JSON and would sail past a
    `ValueError` guard, then crash `changes()` on `previous.get`.
    """
    try:
        loaded = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_baseline(current: dict[str, Any]) -> None:
    """Replace the baseline atomically.

    `write_text` truncates first, so a crash or a kill mid-write leaves a half file, and
    a concurrent run can read it. Writing a sibling temp file and calling `os.replace`
    makes the swap atomic on POSIX, so a reader sees either the whole old file or the
    whole new one.
    """
    temp = STATE_FILE.with_suffix(f".{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(current))
        os.replace(temp, STATE_FILE)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        # Not fatal: the state was still printed and any notification was still sent.
        # It does mean the next run re-notifies, which is the harmless direction.
        print(f"could not persist the baseline: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="print the state and exit")
    parser.add_argument(
        "--notify", action="store_true", help="also raise a desktop notification ON CHANGE"
    )
    args = parser.parse_args(argv)

    try:
        current = state()
    except WatchError as exc:
        # UNKNOWN, and it says so. Not "not approved".
        print(f"UNKNOWN: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # the last line of defence, and it must not be silent
        print(f"UNKNOWN: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    line = summarise(current)
    print(json.dumps(current, indent=1))
    print(line)

    if args.notify:
        changed = changes(read_baseline(), current)
        if changed:
            delivered = notify(line)
            moved = ", ".join(f"{k}: {was} -> {now}" for k, (was, now) in sorted(changed.items()))
            if delivered:
                write_baseline(current)
                print(f"(notified: {moved})")
            else:
                # The baseline deliberately does NOT advance. Advancing it here would
                # make the next run see no change and never mention this again, so a
                # notifier that was merely unavailable for one run would swallow the
                # single message the whole watcher exists to deliver. Leaving it stale
                # costs a repeated alert; advancing it costs the alert entirely.
                print(f"(NOT notified, baseline held so the next run retries: {moved})")
        else:
            write_baseline(current)
            print("(no change since last run, so no notification)")

    if current["installable_internally"]:
        return 0
    # An internal state Apple has added since this was written is UNKNOWN, not "no".
    # Folding it into exit 1 is precisely how `IN_BETA_TESTING` came to be reported as
    # not-installable, and exit 1 is the one code a reader treats as a real answer.
    return 1 if current["internal_state"] in KNOWN_INTERNAL_STATES else 2


if __name__ == "__main__":
    raise SystemExit(main())
