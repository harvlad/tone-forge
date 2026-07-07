# Vocoder Reference (P5)

Capture-only vocoder + harmonizer: the mic is recorded for up to 8 s
against a pre-built carrier, previewed live through
`VocoderMonitor → vocoderBus`, then the whole take is re-rendered
offline at full quality and saved as a local pad sample
(`source: .vocoded`, purple `0x9B4DFF`, `neverUpload` enforced by
`PadSampleMetadata`). There is no persistent live-vocoder path.

## Kernels

### SpectralVocoder (`Sources/ToneForgeEngine/DSP/SpectralVocoder.swift`)

`process(modulator:carrier:config:sampleRate:) -> [Float]`, offline
block API. FFT 2048 / hop 512 / Hann via vDSP_DFT. 64 logarithmic
bands 80 Hz–12 kHz; modulator band energies drive carrier band gains
with 30 ms attack / 80 ms release envelope smoothing. Sibilance above
6 kHz passes the modulator through at 0.3. ±6 dB/oct pre-emphasis on
analysis, de-emphasis on resynthesis. Output length equals modulator
length; the carrier loops internally when shorter; empty carrier →
silence. Deterministic — same (modulator, carrier, config) is
bit-identical.

### PSOLAHarmonizer (`Sources/ToneForgeEngine/DSP/PSOLAHarmonizer.swift`)

`harmonize(_:sampleRate:chordAt:settings:)`. Autocorrelation F0
60–500 Hz with a 0.6 confidence gate (unvoiced regions pass dry),
epoch-synchronous OLA shifting, LPC-20 formant flatten→shift→re-apply
(`FormantEstimator`), chord-aware voice leading (nearest chord tone,
clamped ±2 st around the nominal interval). Voice gains: dry 1.0,
+3rd 0.7, +5th 0.6, +8ve 0.4; `choir` adds 3× ±7-cent copies delayed
0/15/30 ms. Deterministic.

## Modes (`VocoderMode`, persisted in `PadSampleMetadata.vocoderMode`)

Raw values are frozen wire values — renumbering is a schema break.

| # | Mode | Kernel | Carrier |
|---|------|--------|---------|
| 1 | classic | SpectralVocoder | `VocoderCarriers.sawStack` — polyBLEP saw stack of the sounding chord (C3 octave), drone `[36,48,55,60]` without one |
| 2 | song | SpectralVocoder | `VocoderCarriers.chordGrid` — saw stacks following the song's chord timeline from the current transport position, 40 ms equal-power crossfades at boundaries |
| 3 | stem | SpectralVocoder | `VocoderCarriers.loopedStem` — the most-pitched ~1 s window (by `trackF0` confidence) of the song's preferred stem (vocals > lead > first), looped with 10 ms linear seam crossfades |
| 4 | harmony | PSOLAHarmonizer | none — chord spans (middle-C octave) drive voice leading |
| 5 | texture | SpectralVocoder | `VocoderCarriers.texture` — the active pack's most texture-like pad audio, loop-crossfaded to 8 s |

Degradation ladder (`ModeCoordinator.vocoderProgram`): every missing
audio source steps down one rung — stem → chord grid → drone — so a
capture always sounds. All carriers are exactly
`round(8 s × 48 000)` samples, peak-normalized to 0.9, and built with
no RNG (bit-deterministic per app state at arm time).

## Capture flow (R10 — zero dropouts)

```
mic tap (1024 frames) → mono-ize → serial worker queue
worker: resample→48 k, accumulate modulator (8 s hard cap),
        vocode 4096-sample blocks with 2048 samples of history
        (harmony previews DRY) → VocoderPreviewRing
render thread: ring.read — copies what's there, zero-fills shortage,
        shortage-while-active bumps the underrun counter
```

The underrun counter is the P7 "vocoder capture zero dropouts" gate,
surfaced live in the capture meter. On the built-in speaker route the
preview ring is MUTED (still consumed at pace, so the counter stays
meaningful) to keep vocoded audio out of the mic. On stop, the full
take is re-rendered in one pass — preview block-seam ripple never
persists — then conditioned (`RecordingProcessor`), classified, and
saved.

## Gates

Behavioral contracts are asserted in:

- `SpectralVocoderTests` — OLA unity, band tracking, sibilance
  passthrough, determinism/correlation gates.
- `PSOLAHarmonizerTests` — shift accuracy ±3 cents, formant
  preservation ±5%, confidence gate, choir.
- `FormantEstimatorTests` — synthetic vowel F1/F2 ±5%.
- `VocoderCarriersTests` — exact lengths, bit-identical re-render,
  pitch content via `trackF0`, crossfade level continuity, loop
  coverage, frozen `VocoderMode` raw values.
- `VocoderCaptureSessionTests` — ring warm-up/underrun/mute
  accounting, worker block pipeline, 8 s cap, harmony dry preview,
  full-take processing switch.
- `VocoderCoordinatorTests` — program degradation ladder ("always
  sounds"), chord-symbol → pitch-class expansion.

No WAV fixtures — same policy as `docs/transform-reference`: the
kernels' own suites gate the DSP and these tests gate the semantics.
Live mic + render-thread behavior is on the device checklist
(`docs/mobile-testing.md`, P7).
