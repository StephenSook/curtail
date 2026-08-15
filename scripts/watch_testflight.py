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
    private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(private, ec.EllipticCurvePrivateKey):
        raise WatchError(f"{key_path} is not an EC private key")

    now = int(time.time())
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    payload = {"iss": issuer, "iat": now, "exp": now + 900, "aud": "appstoreconnect-v1"}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"

    der = private.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
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

    build = data[0]
    attrs = build.get("attributes", {})
    included = {item["id"]: item for item in builds.get("included", [])}

    def related(kind: str) -> dict[str, Any]:
        ref = (build.get("relationships", {}).get(kind, {}) or {}).get("data")
        return included.get(ref["id"], {}) if ref else {}

    beta_detail = related("buildBetaDetail").get("attributes", {})
    review = related("betaAppReviewSubmission").get("attributes", {})

    internal = beta_detail.get("internalBuildState")
    external = beta_detail.get("externalBuildState")
    return {
        "version": attrs.get("version"),
        "processing": attrs.get("processingState"),
        "expired": attrs.get("expired"),
        "internal_state": internal,
        "external_state": external,
        "beta_review_state": review.get("betaReviewState"),
        # The one line a human actually wants.
        "installable_internally": internal == "READY_FOR_BETA_TESTING",
        "uploaded_at": attrs.get("uploadedDate"),
    }


def summarise(current: dict[str, Any]) -> str:
    if current["installable_internally"]:
        return f"READY TO TEST. Build {current['version']} installs from TestFlight now."
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


def notify(message: str) -> None:
    for command in (
        ["terminal-notifier", "-title", "Curtail TestFlight", "-message", message],
        ["osascript", "-e", f'display notification {message!r} with title "Curtail TestFlight"'],
    ):
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=15)
            return
        except Exception:
            continue


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

    line = summarise(current)
    print(json.dumps(current, indent=1))
    print(line)

    if args.notify:
        previous = {}
        if STATE_FILE.exists():
            try:
                previous = json.loads(STATE_FILE.read_text())
            except ValueError:
                previous = {}
        changed = (
            previous.get("internal_state") != current["internal_state"]
            or previous.get("beta_review_state") != current["beta_review_state"]
        )
        STATE_FILE.write_text(json.dumps(current))
        if changed:
            notify(line)
            print("(notified: state changed)")
        else:
            print("(no change since last run, so no notification)")

    return 0 if current["installable_internally"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
