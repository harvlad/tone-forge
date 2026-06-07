# ToneForge Connect (prototype)

Native macOS audio companion for the ToneForge Jam experience. Connect is
the piece that captures the user's guitar input, plays separated stems
alongside it, and (later) hosts the tone-matched amp model so the user
hears themselves at studio-quality with playable latency.

## Why this exists

The Jam product requires sub-15 ms input → output round-trip. Browsers
cannot deliver this reliably with arbitrary user audio interfaces. Connect
takes over the audio I/O path while the browser keeps owning the UI,
session state, and analysis pipeline. They communicate over a local
WebSocket (not implemented in this slice — comes after latency is
validated).

## Status

Prototype. The current goal is to **measure** whether <15 ms round-trip is
achievable across a representative range of audio interfaces. Everything
else is scaffolding for that measurement.

## Build & run

Requires Swift 5.9+ (macOS 13+ recommended). No third-party dependencies.

```
cd connect
swift build
swift run Connect devices
swift run Connect latency
swift run Connect monitor
swift run Connect jam ../backend/preset_catalog_output/audio_v2_collision_electric
```

## Subcommands

| Command                         | Purpose                                                     |
|---------------------------------|-------------------------------------------------------------|
| `connect devices`               | List CoreAudio I/O devices and channel counts               |
| `connect latency`               | Impulse-loopback round-trip latency measurement             |
| `connect monitor`               | Live input → output passthrough; reports driver latency     |
| `connect jam <dir>`             | Load every .wav in a directory and play under monitoring    |

For the latency probe to work, output must be audible to input. Easiest
setup: a loopback cable from interface out → interface in. Built-in mic +
laptop speakers also works but reports an inflated value due to acoustic
path delay.

## Next slices

1. WebSocket bridge — accept session-init, stem URLs, and tone-preset
   payloads from the web app.
2. Static amp-sim node in the monitoring path (NAM runtime or a simple
   WDF prototype) so the user hears themselves through a tone, not dry.
3. Per-stem mute/solo/gain control from the web app.
4. Real-time tone-match scoring (downstream of fingerprint pipeline).
