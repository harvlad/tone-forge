# tone-forge-mobile — iOS spin-off of the Launchpad experience

The touchscreen IS the Launchpad. Load a song, tap chord/sample pads,
play along in the airport lounge. This is not a wrapper around the web
app — it's a native SwiftUI app that reuses the tone-forge backend and
a ported subset of the Launchpad engine logic.

## Scope (v1)

Three screens:

1. **Library** — songs previously analyzed by the backend, plus a
   "downloaded for offline" list.
2. **Perform** — waveform + transport + mode picker + 8×8 pad grid +
   octave/assist controls. This is 85% of the app.
3. **Contribute Review** — after a session, keep/discard/publish the
   chops you contributed.

Explicitly out of scope for v1:

- Chord ribbon, riff lane, stems mixer UI (perform view stays lean)
- Web MIDI / hardware Launchpad support (v2 nice-to-have; touchscreen
  is the primary interface)
- Chord verifier UX beyond what fits inside the pad grid
- Settings popover — modes live in the perform view, that's it

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     ToneForgeMobile                      │
│  SwiftUI views · ObservableObject state · touch handlers │
└─────────────────┬─────────────────┬──────────────────────┘
                  │                 │
      ┌───────────▼───┐   ┌─────────▼──────────┐
      │ ToneForge     │   │ AudioEngine        │
      │ Engine        │   │ (AVAudioEngine)    │
      │ (pure Swift)  │   │ · song stems       │
      │ · chord parse │   │ · pad synth        │
      │ · palettes    │   │ · chop scheduler   │
      │ · grid layout │   │ · reverb + mixer   │
      │ · padMeaning  │   └────────────────────┘
      └───────────────┘
                  ▲
                  │  identical algorithmic surface
                  │
      ┌───────────┴───────────────────┐
      │ backend /static/launchpad.js  │
      │ (web reference implementation)│
      └───────────────────────────────┘
```

- **ToneForgeEngine**: pure Swift port of the algorithmic slice of
  `backend/static/launchpad.js`. No AVFoundation, no SwiftUI. Unit
  tests use JSON fixtures from the same corpus the web version uses.
- **AudioEngine**: AVAudioEngine wrapper. Owns the pad synth graph,
  chop scheduling, per-stem mixer. Transport is the master clock.
- **ToneForgeMobile**: the app target. SwiftUI views, touch → engine.

## Backend contract

The app talks to the existing tone-forge backend over HTTPS:

- `GET  /api/history` — song list
- `POST /api/analyze-url-stream` — analyze a new URL (background)
- `GET  /api/song/{id}/bundle` — **new** — download offline bundle
  containing `analysis.json`, per-stem AAC audio, and waveform peaks.
  Stems live in R2 (durable object storage). Endpoint returns signed
  URLs the client fetches directly.

The `/bundle` endpoint is the single shared boundary. Everything
downstream in the app keys off its shape.

## Running

Requires Xcode 15+, iOS 17 SDK, device or simulator.

```
open mobile-ios/ToneForgeMobile.xcodeproj
```

Xcode project is generated on first `swift package generate-xcodeproj`
or opened directly via SwiftPM manifest (Package.swift).

## Repo layout

```
mobile-ios/
├── README.md               — this file
├── DECISIONS.md            — decision log
├── Package.swift           — SPM manifest for local dev
├── Sources/
│   ├── ToneForgeEngine/    — pure Swift, no UI/audio
│   └── ToneForgeMobile/    — SwiftUI app target
└── Tests/                  — engine + integration tests
```
