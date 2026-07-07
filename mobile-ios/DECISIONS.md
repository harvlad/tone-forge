# Decision log — tone-forge-mobile

Chronological. Each entry captures a decision, the alternatives
considered, and the reason. Do not delete entries — supersede them
with a new entry that references the old one.

## D-001: Same-repo, `mobile-ios/` subdir

**Date:** 2026-07-05
**Decision:** iOS app lives inside the existing tone-forge monorepo.
**Alternatives:** separate `tone-forge-ios` repo.
**Why:** the engine port (chord parsing, palettes, pad-meaning
dispatcher, grid layouts) is the same algorithm in JS and Swift.
Keeping both under one git history means a change to
`backend/static/launchpad.js` and a change to
`mobile-ios/Sources/ToneForgeEngine/*.swift` land in the same PR,
so drift is visible.

## D-002: R2 for stem storage

**Date:** 2026-07-05
**Decision:** stems stored in Cloudflare R2. Zero egress fees.
S3-compatible API so `boto3` on the backend works unchanged.
**Alternatives:** AWS S3 (egress bill risk); Backblaze B2 (needs
Cloudflare CDN for cheap egress, extra config).
**Why:** app users download stem bundles. Egress is the dominant
cost. R2 eliminates it.
**Env vars the backend reads:**
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET` (default: `tone-forge-stems`)
- `R2_PUBLIC_HOST` (optional — custom domain for public reads)

## D-003: Bundle format = AAC stems + JSON + peaks

**Date:** 2026-07-05
**Decision:** `/api/song/{id}/bundle` returns a manifest with signed
URLs pointing at R2 objects. Objects are:
- `analysis.json` — the existing analysis result (trimmed)
- `stems/{drums,bass,other,vocals}.m4a` — AAC-LC 256 kbps
- `peaks.bin` — waveform peaks (uint8 pairs, 500 samples/sec)

AAC at 256 kbps ≈ 8 MB per 4-minute stem. Four stems = ~32 MB per
song, downloadable on 4G in under a minute.

**Alternatives:** ship WAVs (60+ MB per song, dead on cellular);
Opus (better quality per bit but iOS AVAudioEngine has flakier Opus
support); FLAC (lossless but overkill for this use case).

## D-004: iOS-first, Android later

**Date:** 2026-07-05
**Decision:** v1 is iOS only.
**Why:** mockup is iOS; SwiftUI + AVAudioEngine give the best
touch-to-audio latency; the reused algorithmic code is small enough
(~800 LOC) that a later Kotlin port is a few weeks, not a rewrite.

## D-005: Transport is the master clock

**Date:** 2026-07-05
**Decision:** all scheduling (song stems, chop triggers, countdown
paints, verifier presses) keys off a single `TransportClock` backed
by `AVAudioEngine.outputNode.lastRenderTime`.
**Why:** the web version uses Web Audio's `currentTime` as the
transport. Translating that shape naively to iOS causes drift.
Committing to AVAudioTime from day one avoids the rewrite.

## D-006: Engine extraction happens in place, not upfront

**Date:** 2026-07-05
**Decision:** the pure-logic slice of `launchpad.js` is ported to
Swift directly. The web module is NOT refactored into a separate
`launchpad-engine.mjs` right now.
**Alternatives:** extract first, then port.
**Why:** the extraction was recommended for the case where we ship
Swift and Kotlin in parallel. Since we're iOS-only for v1 (D-004),
the Swift port itself becomes the canonical second implementation.
Web can be refactored later if a third target appears.

## D-007: R2 upload is lazy, per-bundle-request, and best-effort

**Date:** 2026-07-05
**Decision:** the backend does not push stems to R2 during analysis.
It uploads on the first `/api/song/{id}/bundle` fetch, rewriting the
history entry's `stems_paths` in place so subsequent requests are free.
Uploads are best-effort: if boto3 isn't installed, if `R2_*` env vars
are missing, or if a single stem PUT fails, the endpoint falls back to
the existing local `/api/admin/serve-file` URL. See
`backend/tone_forge/r2_storage.py` and `_maybe_upload_stems_to_r2` in
`tone_forge_api.py`.

**Alternatives:**
- Upload during analysis. Rejected: analyses happen locally at a
  developer's machine or from CLI; forcing R2 config on the analysis
  path breaks the "just try tone-forge on your laptop" story.
- Upload via a background task queue (Celery/RQ). Rejected: the
  backend has no worker infrastructure today. Introducing one just for
  R2 uploads is disproportionate; the sync upload finishes in seconds
  on gigabit and the deterministic object key layout means retries are
  free.

**Why:** the surface area of "R2 wired" and "R2 not wired" should be
the same code path. Lazy + idempotent + fallback = you can develop
without creds and ship with them.

## D-008: ffmpeg AAC re-encode is inline, cached, and best-effort

**Date:** 2026-07-05
**Decision:** the R2 upload path transcodes each WAV stem to AAC-LC
M4A on the fly, using `ffmpeg -c:a aac -b:a 256k -f ipod -movflags
+faststart`. Results are cached in `$TMPDIR/toneforge_m4a/` keyed by
source path + mtime + size, so repeat requests skip the transcode.
See `backend/tone_forge/audio_transcode.py`.

**Alternatives:**
- Transcode during analysis (would bloat the analyze endpoint's latency
  for anyone not using the mobile app).
- Ship WAVs to R2 (rejected in D-003 — 5× the cellular download).
- Move to a background worker (rejected in D-007 — no worker infra).

**Why:** AAC-LC at 256 kbps is ~6× smaller than PCM WAV, iOS decodes
it in hardware, and ffmpeg is nearly universal on developer machines
and Linux hosts. When ffmpeg isn't available the pipeline degrades
silently to WAV upload — the mobile client's `codec` field is sniffed
from the URL extension so both formats round-trip through the same
code path.

**Bundle `codec` field values now possible:**
- `"wav"` — pre-R2 (local pass-through), or R2 upload on a host
  without ffmpeg.
- `"m4a"` — R2 upload with ffmpeg on PATH.
- `"mp3"` / `"flac"` / `"ogg"` — sniffed but not currently produced
  by the pipeline; reserved for future source-format support.

## D-009: 8-second chop cap enforced at StemSlice construction

**Date:** 2026-07-06
**Decision:** the compliance chop-duration cap (≤ 8.0 s) is enforced
by `StemSlice.clamped(maxDuration:)` applied at the single production
construction site, `SampleBank.songDerived`
(`Sources/ToneForgeMobile/SampleBank.swift`).
`StemSlice.maxChopDurationSec = 8.0` lives in ToneForgeEngine so the
cap is testable with plain `swift test` (`ChopCapTests`).
**Alternatives:** clamp in the scheduler at trigger time (too late —
the buffer is already loaded); clamp server-side (client must not
trust the server for its own compliance guarantee); drop over-long
chops entirely (worse UX — a 12 s chop still yields a usable 8 s pad).
**Why:** every chop source — bundle presets, ad-hoc
`/api/song/{id}/chops` results, scheduler preload, offline layer
renders — funnels through `songDerived`, so one choke point covers
them all. Clamping trims `endSec` and never moves `startSec`, so the
musical onset is preserved. A DEBUG `assert` in
`SampleScheduler.loadBuffer` acts as a tripwire if a second
construction path ever appears.

## D-010: Voice/chop gain sliders and their mappings

**Date:** 2026-07-06
**Note (2026-07-07):** the mapping *targets* changed with the D-013
bus topology — `voiceGainLinear` now drives `voiceBus.outputVolume`
(PadSynth.masterGain is fixed at 0.311) and `chopGainLinear` drives
`chopBus.outputVolume` (SampleBus.volume is fixed at 1.0). Defaults
and the audible result are unchanged (loudness-neutral migration).
**Decision:** two user-facing gain sliders persisted in the
`SampleSettingsStore` JSON blob:
- `voiceGainLinear` (default **0.9**) maps to
  `padSynth.params.masterGain = Float(v) * 0.311`, so the default is
  ≈ 0.28 — exactly the previous fixed value (loudness-neutral
  migration). Replaces the old "Master" slider row as the single
  writer of `masterGain`.
- `chopGainLinear` (default **0.55**) maps directly to
  `sampleBus.volume`. This is an audible change from the previous
  hard-coded 1.0: chops were consistently overpowering the stems, and
  0.55 sits them in the mix by default.
Old persisted blobs decode via `decodeIfPresent`-with-default, so
existing installs keep their other settings.
**Why:** one linear 0–1 slider each, with the scaling constant hidden
in the mapping, keeps the UI dead simple while preserving the tuned
synth headroom (0.311 ceiling ≈ the level the pad synth was voiced
at).

## D-011: Ingestion uploads via SSE `POST /api/analyze-stream`

**Date:** 2026-07-06
**Decision:** the import pipeline uploads the transcoded WAV to
`POST /api/analyze-stream` and consumes single-line
`data: {"type": "progress"|"result"|"error", …}` frames via
`URLSession.bytes(for:).lines` (`AnalyzeClient` in ToneForgeEngine).
The multipart body is built in memory (`multipartBody`, pure and
golden-tested); ~21 MB for a 4-minute mono analysis WAV is fine. The
transport is injectable through the `AnalyzeStreaming` protocol so UI
tests stub the network entirely.
**Alternatives:** plain `POST /api/analyze` (analyses take minutes —
a single long request with no feedback risks timeouts and shows the
user nothing); polling a job endpoint (backend has no job store);
temp-file multipart composition (escape hatch if inputs ever get much
larger).
**Why:** the backend already emits SSE progress for the web UI; the
phone reuses it for a live progress bar and gets the terminal
`result` frame's `history_id` to load the bundle.

## D-012: Hand-rolled golden-PNG snapshot tests

**Date:** 2026-07-06
**Decision:** the JAM-screen snapshot tests
(`Tests/ToneForgeMobileTests/Snapshot/`) use a hand-rolled harness:
SwiftUI `ImageRenderer` at scale 2 / dark scheme / fixed frame, both
the fresh render and the golden PNG decoded through the same
fixed-sRGB RGBA8 `CGContext`, compared with a per-channel tolerance
of ±2 and an allowed differing-pixel fraction of 0.5%. Goldens are
recorded on a pinned simulator via
`TEST_RUNNER_TONEFORGE_SNAPSHOT_RECORD=1` +
`TEST_RUNNER_TONEFORGE_SNAPSHOT_DIR=…` (record mode writes the PNG
then fails the test so it can't silently pass in CI). Reference
sizes: iPad Pro 12.9" (1024×1366) and iPhone 15 Pro (393×852).
**Alternatives:** pointfree swift-snapshot-testing (rejected — the
project has a no-third-party-deps rule); XCUITest screenshots
(needs the full app host and is far slower/flakier).
**Known limitation:** `ImageRenderer` cannot flatten UIKit-backed
controls (segmented `Picker`, `Slider`, some `Button` styles) and
draws them as yellow "prohibited" placeholder stripes. These are
deterministic across runs, so layout regressions are still caught;
they just aren't pixel-faithful to the on-device look. Never compare
PNG bytes directly — encoders differ across OS releases.

## D-013: One shared contribution bus tree with a single reverb

**Date:** 2026-07-07
**Decision:** the contribution graph is built explicitly by
`AudioEngine.buildContributionGraph()` (called from `bootAudio`
before `engine.start`):

```
PadSynth.voiceMixer + WavetableSynthNode [+ MicMonitor P3] → voiceBus (0.9)
SampleVoicePool → SampleBus.voiceMixer  → chopBus  (0.55)
[VocoderMonitor P5]                     → vocoderBus (0.4, silent until P5)
voiceBus + chopBus + vocoderBus → sharedBus (volume = layerFaderDb)
sharedBus → dryMixer → mainMixer
sharedBus → sharedReverb (wet 100) → wetMixer → mainMixer
StemPlayer.mixer → mainMixer (bypasses sharedBus, as v1)
```

PadSynth and SampleBus lost their private dry/wet reverb branches;
reverb controls drive `AudioEngine.reverbParams` (one
`presetForSeconds` implementation, one AVAudioUnitReverb). All gains
were chosen loudness-neutral against v1 (see the D-010 note).
`vocoderGainLinear` (default 0.4) is persisted from day one so the
P5 wiring is a connect, not a schema change.
**Alternatives:** per-source reverbs (v1 shape — duplicated DSP,
double CPU, and the "Your Layer" fader couldn't include reverb
tails); reverb on mainMixer (would wet the original stems).
**Why:** the v2 brief's fader/bounce semantics need one place where
"everything the user contributed" sums — sharedBus — and effects
below that point are shared by construction.

## D-014: Stems stay at source sample rate (48 kHz exception)

**Date:** 2026-07-07
**Decision:** the D-017 48 kHz canonical format applies to every
node the app owns EXCEPT the stem playback path. Stem files are
scheduled via `scheduleSegment` streaming at their source rate
(44.1 kHz AAC today); `StemPlayer.mixer → mainMixer` is connected at
the canonical format so the engine's converter SRCs the stem branch
only.
**Alternatives:** re-encode stems to 48 kHz on download (doubles
Caches usage and breaks the ≤2 s session-load gate); resample at
buffer load (stems stream from disk — there is no full-buffer load).
**Why:** the contribution paths (synth, pads, mic, vocoder) are the
latency- and fidelity-critical ones; a single SRC on the passive
stem branch is inaudible and free.

## D-015: SessionCapture supersedes LayerRecorder

**Date:** 2026-07-07
**Decision:** the Record pill arms `SessionCaptureRecorder` (P6) —
a `ContributionEventBus` subscriber that captures `ContributionEvent`s
directly, replacing the `LayerRecorder` bridges
(`SampleScheduler.onEvent → append` and the ModeCoordinator
synth-note append hook, both removed). Sessions persist as
`Documents/sessions/<sessionId>.json` (`SessionStore`): schemaVersion
1, event stream + pad-reference mapping, NO audio. Replay is
`SessionPlayer` re-firing events through the bus with
`isReplay: true` (the recorder skips replays, closing the
self-re-record loop); the coordinator applies the session's
padMapping as a transient overlay consulted only for replay events.
Bounce is `SessionBounceRenderer` — a deterministic offline 48 kHz
mixdown gated by attestation for original-song inclusion.
`AVAudioUnitReverb` was measured NON-deterministic across identical
offline renders (2/5 byte-mismatches in a scripted experiment), so
the bounce path renders with pure-Swift deterministic DSP; the 10×
bit-identical test gates the phase. The legacy Layer* stack
(LayerRecorder/Player/Store/OfflineRenderer) is frozen read-only:
saved layers stay listable, replayable (via the documented
`LayerPlayer.triggerRaw` exception) and exportable, but no new
layers can be recorded.
**Alternatives:** extend LayerEvent with the new event kinds
(gap markers, MIDI sources, replay flags would have forced a
LayerTimeline schema bump and broken frozen 44.1 k renders); record
both stacks in parallel (two sources of truth for one take).
**Why:** the v2 brief's definition of done makes the bus the single
input path — recording must therefore capture bus events, not
scheduler callbacks, or hardware/future sources would never be
captured. One recorder, one schema, one replay path.

## D-016: Sketch tab folded into the Play surface

**Date:** 2026-07-07
**Decision:** the standalone Sketch tab is deleted. "Sketch" is now
simply the Play tab with no song loaded: same 8×8 ModeGridView, same
AppMode, but the quantize context degrades to a synthetic tempo grid
(`SampleScheduler.updateSyntheticContext(tempoBpm:)`) driven by the
unmigrated `SketchSettingsStore` (BPM / time-sig / metronome /
sketch quantize). The context switch keys off `currentBundle != nil`
(see `ModeCoordinator.applyGridContext`), not tab state —
`setSketchTabActive` is gone. RootView is 4 tabs
(Library/Play/Search/Profile).
**Alternatives:** keep the tab and route it to the same grid
(duplicate chrome, two places to maintain quantize context); migrate
SketchSettingsStore into SampleSettingsStore (needless churn — the
store is fine, only its *trigger* changed).
**Why:** after the restructure the two tabs would have been the same
view minus the song header. One surface, one input path
(ContributionEventBus), one mental model.

## D-017: 48 kHz canonical format; analysis upload stays 44.1 kHz

**Date:** 2026-07-07
**Decision:** `AudioEngine.canonicalFormat` = 48 kHz stereo Float32.
The session requests `setPreferredSampleRate(48_000)` (256-frame IO
≈ 5.3 ms); every explicit connect the app makes uses the canonical
format; the single resample point for pad content is SampleScheduler
ingest (`AVAudioConverter`, `.max` quality) so no SRC sits anywhere
downstream of a loaded buffer. New DSP goldens are recorded at 48 k.
Exceptions: stems (D-014); `LayerOfflineRenderer` stays 44.1 k
(frozen legacy renders must stay bit-comparable); the
`AudioTranscoder` analysis upload stays 44.1 k mono (backend
contract — zero backend modifications).
**Alternatives:** stay at 44.1 k (fights iOS hardware, which runs
48 k natively — every render would SRC); 48 k everywhere including
the upload (breaks the backend contract for no analysis benefit).
**Why:** iPhone audio hardware is 48 k; matching it removes a
permanent SRC from the touch→attack path, which the ≤8 ms gate
budget can't spare.

## D-018: Heuristic sample classifier (Core ML seam kept)

**Date:** 2026-07-07
**Decision:** mic captures are classified by `HeuristicClassifier`
(`DSP/Classifier.swift`) — a hand-tuned decision tree over vDSP
features (26 mel-band energies → spectral centroid/flux, ZCR,
envelope shape, pitchedness, duration) producing a `SampleClass` +
confidence. No trained model ships. The seam is the
`SampleClassifying` protocol; a `// FUTURE: CoreMLClassifier`
drop-in is documented in `docs/classifier-training.md` (feature
spec, label taxonomy, training-data collection plan). The user can
always override the verdict (`PadSampleMetadata.userClassOverride`,
exposed in PadSourceSheet), so a wrong guess costs one tap.
**Alternatives:** train a Core ML model now (no labelled corpus
exists, and shipping a model without one means shipping guesses with
extra steps); skip classification entirely (the grid then can't
color/badge local pads by kind, and future modes lose the
vocal/percussion routing hint).
**Why:** classification here is a UX hint, not a correctness
boundary — a transparent heuristic with a user override beats an
opaque model of unknown provenance, and the protocol seam makes the
upgrade purely additive.
