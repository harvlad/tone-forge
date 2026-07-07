# Mobile testing checklist (P7)

The ship gate for the iOS app. Two halves: the **automated suites**
(run on every change) and the **on-device checklist** (run on real
hardware before a build goes out). A build ships when every automated
suite is green AND every checklist row passes on the primary test
device.

## Automated suites

Commands and toolchain caveats live in [SETUP.md](../SETUP.md); this
is the run order.

| Suite | Command | Green means |
| --- | --- | --- |
| Package tests (Engine + Mobile) | `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift test` | Stores, DSP, schedulers, transports, compliance tripwires |
| Full simulator suite (adds UIKit-dependent + UI tests) | `xcodebuild test -project ToneForgeMobile.xcodeproj -scheme ToneForgeMobileApp -destination 'platform=iOS Simulator,name=iPhone 17 Pro'` | Everything above + snapshots, transcoder, attestation UI flow |
| Compliance grep | `grep -riE "youtube\|yt-dlp\|spotify" Sources App UITests` | Must return nothing — no streaming-service ingestion |

Snapshot goldens are pinned to a simulator runtime — see SETUP.md
before re-recording.

## Diagnostics ship gates

Settings → Diagnostics → **Latency & gates**, on the device (not the
simulator — the budgets are hardware claims). Run all, screenshot the
result, attach it to the release notes. `LatencyProbe` measures:

| Gate | Budget | Notes |
| --- | --- | --- |
| Pad tap → attack | ≤ 8 ms | median of 20 taps |
| Launchpad → attack | ≤ 12 ms | USB Launchpad attached |
| Session load | ≤ 2000 ms | 1000-event take, disk → decoded |
| Mic → playable pad | ≤ 5000 ms | 2 s take: process → classify → save → assign |
| Vocoder capture dropouts | 0 underruns | see `docs/vocoder-reference/` |

Mic gate skips (not fails) when mic permission is denied — grant
permission first so nothing is skipped on the release run.

## On-device checklist

Run top to bottom on the primary device. Anything unchecked blocks
the build.

### Lifecycle & audio session

- [ ] Start playback, background the app, foreground it — audio state
      resumes cleanly, no stuck notes.
- [ ] Take a phone call (or trigger Siri) mid-performance — audio
      ducks/stops, recovers after the interruption ends.
- [ ] Plug/unplug headphones during playback — route change without a
      crash or permanent silence.
- [ ] Screen stays awake while performing or replaying; idle timer
      re-arms when the transport is parked (IdleTimerPolicy).
- [ ] Force-quit mid-recording, relaunch — the autosaved take is in
      Settings → Storage → Sessions (autosave contract).

### Launchpad

- [ ] USB Launchpad maps onto the grid, pad LEDs mirror the app.
- [ ] On an underpowered port the banner appears; on a powered hub it
      does not.

### Play surface

- [ ] Sample + Hybrid modes both play; Hybrid bottom rows track the
      loaded song's key, chord tones lit.
- [ ] Record a sketch (no song): count-in bar, then events capture;
      stop parks the transport.
- [ ] Record over a song: take lands in Sessions with the song tag.

### Samples (mic / vocoder)

- [ ] Mic capture auto-stops at 8 s; sample lands on a pad and in
      Settings → Storage → Samples.
- [ ] Vocoder capture (each mode you ship) produces a playable pad
      sample with zero underruns reported.
- [ ] Samples survive relaunch; deleting one clears its pad binding.

### Sessions, replay, bounce

- [ ] Replay a saved session — pads flash, audio matches the take,
      toggling again stops it.
- [ ] Bounce to WAV twice — files are bit-identical (D-015
      determinism; compare checksums).
- [ ] Bounce to M4A — plays in Files/Music apps.
- [ ] "Bounce with song" appears only with the session's song loaded
      AND attestation accepted; output mixes stems underneath.
- [ ] Bounced file shares via the share sheet (AirDrop or Save to
      Files).

### Storage browsers (Settings → Storage)

- [ ] Counts and sizes on the Storage section match reality.
- [ ] Samples: rows list source/class/duration; swipe-delete works;
      Delete All (confirmed) empties the list and the pads.
- [ ] Sessions: Delete All keeps bounced audio files.
- [ ] Bounces: share per row; Delete All leaves sessions intact.

### Help, legal, compliance

- [ ] Settings → Help → "How Tone Forge works" reads correctly on the
      device's text size.
- [ ] Terms / Privacy sheets open; takedown mailto link resolves.
- [ ] Import gate: attestation sheet blocks import until accepted.
- [ ] "Delete all analyses from server" round-trips (spinner, then
      Library empties).
- [ ] Mic/vocoded samples never upload: airplane-mode the device,
      confirm capture + playback still work end-to-end (device-local
      by construction; ComplianceTests enforce the code paths).

## Device matrix

Primary: the oldest device you claim the latency budgets on — gates
above must PASS there, not just on the newest hardware. Secondary
(smoke only): one current-generation device, one iPad if the layout
is enabled for it. Launchpad tests need the USB-C adapter and a
powered hub for the underpower row.
