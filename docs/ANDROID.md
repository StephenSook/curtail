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
- The package name is `app.curtail.field`, which is also compiled into the asset links
  statement. Changing one without the other breaks verification.
