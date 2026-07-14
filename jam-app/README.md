# JamApp — minimal native macOS app for Jam

> **DEPRECATED** — superseded by [`../jam-desktop/`](../jam-desktop/),
> a fully native SwiftUI rewrite with in-process audio (stems,
> tempo-stretch, monitor/tone) and Launchpad Pro MK3 support. This
> WKWebView wrapper stays in-tree until jam-desktop parity sign-off,
> then gets removed. No new features land here.

A tiny SwiftUI app that opens the existing Jam web UI in a `WKWebView`.
A native window around the same frontend you'd get at
<http://localhost:8000/jam> — no Electron, no Chromium, just Safari's
engine plus a window.

Entirely self-contained inside `jam-app/`. Does not embed the Python
backend or the Connect audio engine — both still run as separate
processes.

## Requirements

- macOS 12 (Monterey) or newer
- Xcode CLT (`xcode-select --install`) for `swift` + `plutil`
- The ToneForge backend running locally (default `http://localhost:8000`)

## Two ways to run

### A) Dev mode — `swift run`

Fastest iteration loop, no bundle:

```bash
cd backend
uvicorn tone_forge_api:app --port 8000

# in another terminal
cd jam-app
swift run JamApp
```

Override the URL with the `JAM_URL` env var:

```bash
JAM_URL=http://192.168.1.10:8000/jam swift run JamApp
```

### B) Real macOS app — `JamApp.app`

For a proper double-clickable `.app` bundle:

```bash
cd jam-app
./build_app.sh           # produces dist/JamApp.app
./build_app.sh --run     # build + open it
```

That gives you `dist/JamApp.app`. Double-click it from Finder, or drag
it into `/Applications`. It registers as `Jam` with bundle id
`com.toneforge.jam`, gets its own Dock icon, and shows up in
Cmd-Tab / Mission Control like any other Mac app.

Notes:
- The bundle is ad-hoc-signed, not Developer-ID-signed. On the machine
  that built it: double-clicks just work. On a fresh Mac: Gatekeeper
  will require right-click → Open the first time. For a real
  distribution-grade build (Developer ID sign + Apple notarize + DMG),
  adapt `../connect/build_release.sh`.
- The bundle's `Info.plist` enables `NSAllowsLocalNetworking` so
  WKWebView can load `http://localhost:8000` from inside an app
  bundle (App Transport Security blocks plaintext HTTP by default).

#### Flags

| Flag | Effect |
|------|--------|
| `--host-only` | (default) build only the host arch — fast |
| `--universal` | build arm64 + x86_64 — requires full Xcode.app |
| `--run`       | after assembly, `open dist/JamApp.app` |

## What's here

```
jam-app/
├── Package.swift                       # SwiftPM manifest, no external deps
├── README.md                           # this file
├── build_app.sh                        # compile release + assemble .app
├── Resources/
│   └── Info.plist                      # bundle metadata, ATS, principal class
└── Sources/JamApp/JamAppMain.swift     # SwiftUI app + WKWebView
```

Source breakdown:
- `JamWebView` — `NSViewRepresentable` wrapper around `WKWebView`
- `ContentView` — sets minimum window size (1100 × 720)
- `JamApp` — `@main` SwiftUI app entry, with explicit `.regular`
  activation policy so `swift run` mode still puts the window in front

## What's deliberately not here

- No bundled Python runtime — backend is a separate process
- No WebSocket bridge to Connect — lives in `../connect/`
- No Sparkle auto-update — adds dependency weight, not needed for v0
- No native onboarding / preferences UI — the web UI already handles it
- No custom app icon — drop `Resources/AppIcon.icns` and rebuild to add one

When/if this graduates beyond a prototype, the natural move is to fold
it into `../connect/Package.swift` as a second executable target so the
shell can talk directly to `ConnectCore` for low-latency audio, and
adapt `../connect/build_release.sh` for signed/notarized distribution.
