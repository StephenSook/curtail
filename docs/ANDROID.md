# Shipping the field surface as an installable Android app

The field route is a PWA, so it installs on **both** platforms from the URL with no
store involved. This document is about the extra step for Android: wrapping the same
PWA in a **Trusted Web Activity** to produce a real signed `.apk` you can sideload or
upload to Play.

## Why a TWA and not a native app

There is no React Native app here to wrap. The server is FastAPI serving one page. A
TWA renders the identical PWA inside an Android app shell, so the app and the website
cannot drift: there is one codebase, one deployment, one auth path, one governance
boundary, which is exactly the argument Addendum M makes for the PWA in the first place.

**iOS gets the PWA, not TestFlight.** Add to Home Screen produces a standalone app with
no review queue and no 90-day expiry. A thin native wrapper around a website is the
shape App Review rejects as a repackaged web page, and that is not a rejection worth
risking on a deadline.

## What is already done

- The PWA is installable: manifest with `start_url: /field`, `display: standalone`, and
  192, 512 and **maskable** icons, all served and asserted by tests.
- The service worker is served **from the root** with `Service-Worker-Allowed: /`, so
  its scope covers the whole origin rather than a subdirectory.
- `GET /.well-known/assetlinks.json` is implemented and **refuses with a 503 naming the
  missing variable** until a fingerprint is configured. That refusal is deliberate: an
  empty asset links file is valid JSON that fails verification silently, and the only
  symptom is an installed app showing a browser URL bar with nothing anywhere to
  explain it.

## What is left, and why you run it rather than me

Signing requires generating a **keystore**, which is a credential with a password.
That is yours to create and hold. Everything below runs on your machine.

```bash
# 1. Generate the wrapper from the live PWA. It will ask you to create a keystore
#    and choose a password. Keep both: losing the keystore means you can never ship
#    an update that Android accepts as the same app.
npx @bubblewrap/cli init \
  --manifest https://curtail-console-api-672785135387.us-central1.run.app/manifest.webmanifest

# 2. Build the signed apk.
npx @bubblewrap/cli build

# 3. Read the fingerprint out of the keystore you just made.
npx @bubblewrap/cli fingerprint list
```

Then set the fingerprint on the service so Android will trust the origin:

```bash
gcloud run services update curtail-console-api \
  --project curtail-505118 --region us-central1 \
  --update-env-vars TWA_SHA256_FINGERPRINT="AA:BB:CC:..."
```

Use `--update-env-vars`, never `--set-env-vars`. The second one REPLACES the whole
environment, and doing that on this service once wiped the project id, the signing key
and the demo passphrase while every route kept answering.

Verify it took:

```bash
curl -s https://curtail-console-api-672785135387.us-central1.run.app/.well-known/assetlinks.json
```

A 200 with your fingerprint means an installed app will run without a URL bar. A 503
means the variable did not land, and the message says so.

## Notes

- Bubblewrap wants **JDK 17**. This machine has 24, so pass `--jdkPath` if it complains,
  or let bubblewrap download its own.
- `*.keystore`, `*.jks`, `android/` and `twa-manifest.json` are gitignored. A leaked
  keystore lets somebody ship an update that Android trusts as us, so it never enters
  the repository, and a test asserts the ignore rules exist.
- **The package id is decided at build time, not here.** Bubblewrap derives its default
  from the host, so the real one came out as
  `app.run.us_central1.curtail_console_api_672785135387.twa`, not the
  `app.curtail.field` this document first assumed. The endpoint reads `TWA_PACKAGE_NAME`
  and falls back to the generated id. If you accept a different id at the prompt, set
  that variable to match, because a mismatch fails verification SILENTLY and the only
  symptom is an installed app showing a browser URL bar.

- **Move the keystore out of `/tmp` immediately.** macOS purges it. Losing the keystore
  means never shipping an update Android accepts as the same app:
  `mkdir -p ~/keys/curtail && mv /private/tmp/twa/android.keystore ~/keys/curtail/`
  Then update `signingKey.path` in `twa-manifest.json` to the new location.

---

# iOS

The field surface installs from Safari with **Add to Home Screen** and that is the
supported path: standalone, offline, its own icon, no review queue, no expiry. WebKit
exempts home-screen web apps from the seven-day script-writable storage cap, so the
offline queue is not evicted.

`ios/` additionally contains a **native shell** for TestFlight distribution. Be honest
about what it is for: **distribution, not capability.** It renders the same route in a
`WKWebView`, so there is no second implementation of the evidence path to drift, and it
holds no credentials or signing material. Hard rule 14 is unchanged by a native shell.

It is generated from `ios/project.yml` with [XcodeGen](https://github.com/yonaskolb/XcodeGen),
so the committed thing is a readable declaration rather than a pbxproj:

```bash
brew install xcodegen
cd ios && xcodegen generate
open CurtailField.xcodeproj
```

Verified to build and run: `xcodebuild -scheme CurtailField -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build`
succeeds, and the app launches in a simulator and loads the live field surface.

To ship it to TestFlight you need an Apple Developer account, because signing identities
and the App Store Connect record belong to a person and not to this repository:

1. Open the project in Xcode, select the `CurtailField` target, and set your Team under
   Signing & Capabilities. Bundle id is `app.curtail.field`.
2. Product > Archive.
3. Distribute App > TestFlight (Internal Only) if you only need your own devices, which
   requires **no Beta App Review**. External testers require review of the first build
   of a version.

The two guideline 4.2 mitigations are already in the code: navigation is confined to the
verified origin and anything else opens in Safari, and a failed load renders a real
message rather than a blank white screen.
