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

### Measured results

2026-09-08 — Apple M1 Max MacBook Pro, macOS 26.3.1, built-in devices
(MacBook Pro Microphone in, MacBook Pro Speakers out), system default
sample rate.

- `connect monitor` driver report: input device latency 0.00 ms, output
  device latency 0.00 ms (built-in driver reports zero here), buffer
  5.80 ms, **estimated round-trip floor 11.61 ms**.
- `connect latency` (impulse loopback): first two runs reported
  `no_signal` (muted output / mic permission), one run reported a
  noise-triggered −130 ms at `confidence=low` (the CLI verdict gate now
  refuses those). With output audible: **measured round-trip 51.27 ms**
  (`peak=0.333, confidence=high`). Above the 15 ms target on this path —
  expected, since built-in devices add safety offsets and the
  speaker→mic hop is acoustic; treat as an upper bound, not the
  interface number.

Caveats: the built-in speaker→mic path is acoustic, so even a successful
probe on this setup would include air travel and mic/speaker transducer
delay and overstate the electrical round-trip. A wired loopback (or an
interface with software loopback) is the intended measurement path; no
external interface was attached for this run. The 11.61 ms figure is the
driver-derived floor (2× buffer + reported device latencies), not a
measured round-trip.

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
