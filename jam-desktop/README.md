# jam-desktop — Jamn for macOS

Native SwiftUI replacement for the web jam UI (`backend/static/jam.js`).
Full parity: Intake, Band Room, Rehearsal, Perform, plus in-process
audio (stems, tempo-stretch, monitor/tone chains) and Launchpad Pro
MK3 support. The Python backend stays authoritative for analysis,
history and song bundles.

## Architecture

Three-layer SwiftPM package (macOS 14+):

| Target | Contents | Frameworks |
|---|---|---|
| `JamDesktopCore` | View models, WS bridge frames/client, transport state, chord/tab math, Launchpad controller + MIDI seam protocol | Foundation only — headless `swift test` |
| `JamDesktopAudio` | Stem player, transport clock, click track, monitor/tone DSP, chop player, CoreMIDI | AVFoundation, CoreMIDI |
| `JamDesktop` | SwiftUI app, SessionController glue | SwiftUI |

Path dependencies: `../connect` (ConnectCore audio engine),
`../mobile-ios` (ToneForgeEngine: HTTP clients, bundle types, chord
theory, Launchpad protocol bytes, quantizer).

Transport authority is hybrid local-first: this app owns audio and
the transport clock, mirrors state over `/ws/connect-bridge` exactly
like the web client, and applies inbound peer frames
last-writer-wins. A co-open browser jam pairs automatically (same
session id).

## Build & run

```sh
# Dev loop (backend on localhost:8000)
cd backend && uvicorn tone_forge_api:app --port 8000   # terminal 1
cd jam-desktop && swift run JamDesktop                  # terminal 2

# Tests (headless; needs full Xcode for XCTest)
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
swift test
```

Backend URL and session id are changeable in-app (Settings) —
defaults to `http://localhost:8000`; point at `https://jamn.app` for
the hosted backend.

## Packaging

```sh
./build_app.sh                 # dist/Jamn.app, ad-hoc signed dev build
./build_app.sh --run           # …and launch it
./build_app.sh --universal     # arm64 + x86_64 (needs full Xcode.app)
./build_app.sh --release       # Developer ID + hardened runtime + notarize + DMG
```

`--release` env vars: `DEVELOPER_ID`, `APPLE_TEAM_ID`, and either
API-key notary auth (`NOTARY_KEY_ID`/`NOTARY_ISSUER_ID`/`NOTARY_KEY_PATH`)
or `APPLE_ID`/`APPLE_APP_PASSWORD`. See `./build_app.sh --help`.

Note: `swift run` lacks a bundle identity, so the macOS mic-permission
prompt (monitor/tone features) only works from a built .app — use
`./build_app.sh --run` when testing those.

## Launchpad Pro MK3

Plug in over USB — hot-plug handled, Programmer Mode entered
automatically, Live Mode restored on release. The Launchpad panel
(toolbar button) is an interchangeable on-screen 8x8 mirror: chop
pads with quantized triggers (off/1-8/1-4/1-2/bar/phrase), stem and
slice-mode switching. Unpowered USB hubs can brown the device out;
the panel shows a banner when that's suspected.

## Relationship to jam-app/

`jam-app/` (the WKWebView wrapper) is deprecated in favor of this
package; it remains in-tree until parity sign-off. See
`jam-app/README.md`.
