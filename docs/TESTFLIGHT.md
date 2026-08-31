# Curtail Field on iOS

**Curtail Field**, version 0.1.0 (1), on App Store Connect app id `6801928911`.

| | |
|---|---|
| Public link | https://testflight.apple.com/join/AqbsQ1J5 |
| Internal testing | live, group `Field Testers`, no Apple review involved |
| External testing | **approved by Beta App Review**, group `Judges`, link open to anyone |
| Watcher | `uv run --with cryptography python scripts/watch_testflight.py --once` |

## Read the state before you trust the link

The public link is permanent and was issued when the external group was created. Beta App
Review **approved** build 1 (`beta_review_state: APPROVED`, read from the App Store
Connect API on 2026-08-31), so the link serves the join flow to anyone. While review was
pending it answered `200` with **"This beta isn't accepting any new testers right now"**,
and it will read closed that way again if a build of a new version re-enters review.

That closed message is not a broken link and not an error. Apple runs two separate paths:

| | Beta App Review | who installs |
|---|---|---|
| **Internal** testers | none | up to 100 members of the team, immediately |
| **External** testers | required for the first build of a version | anyone with the public link |

So the build being installable and the link being open are different facts with different
clocks, and a page that shows only one of them is misleading whichever one it picks.

Ask the API rather than a person:

```
uv run --with cryptography python scripts/watch_testflight.py --once
```

```json
{
  "internal_state": "IN_BETA_TESTING",
  "external_state": "BETA_APPROVED",
  "beta_review_state": "APPROVED",
  "installable_internally": true
}
```

The `--with cryptography` matters: the watcher signs its App Store Connect assertion with
ES256 and `cryptography` is not a project dependency, so the bare `uv run` invocation dies
on the import before ever reaching Apple.

Exit `0` means installable, `1` means asked and not yet, and **`2` means UNKNOWN**: the
question could not be asked at all. Those last two are kept apart deliberately, because a
watcher that reports "not approved yet" when it means "I never reached Apple" is the
failure that costs a deadline.

## Watching it without watching it

While review was pending, a launchd agent ran the watcher on offset minutes (7, 22, 37,
52) and raised a desktop notification **on a change**, not on every run. It is retired
now that review has passed. An honest note for the next version: the agent's runs had
been crashing on the missing `cryptography` import described above, before ever reaching
Apple, which is exactly the UNKNOWN failure the exit codes below warn about. The approval
was confirmed by the on-demand command and by fetching the public link itself, not by the
dead agent.

```
~/Library/LaunchAgents/app.curtail.testflight-watch.plist
~/Library/Logs/curtail-testflight.log
```

Offset minutes rather than a round interval because schedulers deprioritise `*/N` and
`:00`: a `*/10` job on an earlier project actually fired every 65 to 80 minutes while
reporting success every time. Notify-on-change because a notifier that fires four times an
hour is a notifier somebody turns off, and then it is not watching anything.

The environment it needs, none of which is a secret except the key it points at:

```
ASC_KEY_ID       the App Store Connect API key id
ASC_ISSUER_ID    the team's issuer uuid
ASC_APP_ID       6801928911
ASC_KEY_PATH     defaults to ~/.appstoreconnect/private_keys/AuthKey_$ASC_KEY_ID.p8
```

**The `.p8` is a credential and is not in this repository.** Only its path is
configuration. The assertion is ES256 and the signature must be raw `r||s` rather than the
DER that `cryptography` returns by default, which is the one detail that otherwise
produces a `401` indistinguishable from a bad key.

## Building it

The Xcode project is generated, not committed as a binary blob nobody can diff:

```
brew install xcodegen
cd ios && xcodegen generate
open CurtailField.xcodeproj
```

[`ios/project.yml`](../ios/project.yml) is the declaration and
[`ios/Sources/CurtailFieldApp.swift`](../ios/Sources/CurtailFieldApp.swift) is the whole
app. Archive and upload through Xcode's Organizer, or `xcodebuild archive` followed by
`xcrun altool`.

## What the shell does, and what it refuses to do

It is a WKWebView over
[the field route](https://curtail-console-api-672785135387.us-central1.run.app/field),
deliberately. A second native implementation of an evidence path would be a second thing
to keep correct, and the two would diverge.

- Navigation is confined to that one origin. Anything else opens in Safari, because a
  wrapper that renders any URL is a browser, and a browser dressed as this app is a
  phishing surface aimed at people who sign legal orders.
- A failed load renders a page saying the service was unreachable and that nothing saved
  on the device has been lost. A blank white screen tells a watermaster standing in
  moving water nothing at all.
- The data store is explicitly persistent, so the offline queue survives app restarts.
  The default already is, and it is stated because a non-persistent store here would
  silently discard measurements somebody waded into a river to take.

**It holds no signing key and no agent identity.** The device authenticates a human and
submits append-only evidence; orders are generated and bound server side. That is hard
rule 14, and there is a test that proves the device cannot sign rather than a paragraph
promising it will not.

## Export compliance

Answered as **"None of the algorithms mentioned above"**. The target imports `SwiftUI` and
`WebKit` and nothing else, so it implements no encryption and uses only the TLS the
operating system provides. The one cryptographic call anywhere near the field surface is
`crypto.subtle.digest`, which hashes a photo: one-way, keyless, not encryption, and
provided by WebKit in any case.
