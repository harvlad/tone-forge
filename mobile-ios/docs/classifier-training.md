# Sample classifier — training notes (D-018 seam)

The shipping classifier is `HeuristicClassifier`
(`Sources/ToneForgeEngine/DSP/Classifier.swift`): hand-tuned features
feeding a small decision tree. This document specifies how to train a
Core ML replacement when heuristic accuracy stops being good enough.

## The seam

Everything downstream talks to the protocol:

```swift
public protocol SampleClassifying: Sendable {
    func classify(samples: [Float], sampleRate: Double) -> (SampleClass, Double)
}
```

A trained model ships as `CoreMLClassifier: SampleClassifying` loading
a compiled `.mlmodelc` from the app bundle. No caller changes: the
recording flow, pad-sheet badge, and `PadSampleMetadata.classification`
all consume the protocol. Keep `HeuristicClassifier` as the fallback
when the model asset is missing or the OS Core ML runtime fails.

## Label set (frozen)

The seven `SampleClass` cases are the wire format
(`PadSampleMetadata` sidecars, frozen v1 JSON):

| Label | rawValue | Canonical material |
|---|---|---|
| vocalChop | `vocal_chop` | single short sung/hummed note |
| percussion | `percussion` | claps, taps, beatbox hits, knocks |
| sustainedNote | `sustained_note` | held sung note, whistle, drone ≥1.5 s |
| texture | `texture` | breath noise, room tone beds, shakers, ≥2 s unpitched |
| phrase | `phrase` | multi-note melodic or rhythmic passage |
| speechWord | `speech_word` | one spoken word |
| unknown | `unknown` | reject bucket / low confidence |

New classes require a new rawValue string; old decoders degrade
unknown strings to `.unknown` (see `SampleClass.init(from:)`), so
additions are non-breaking.

## Input representation

Match the runtime exactly — the model sees what `MicRecorder` +
`RecordingProcessor` produce, nothing else:

- 48 kHz mono Float32, already trimmed / DC-blocked / normalised to
  −1 dBFS by `RecordingProcessor.process`.
- Duration 0.1–8.0 s (8 s is the hard compliance cap).
- Suggested model input: log-mel spectrogram, 26 bands, 1024-sample
  window, 256 hop (identical layout to
  `HeuristicClassifier.melLayout` — 80 Hz–12 kHz HTK mel), padded or
  pooled to fixed length.

## Data collection

- Record on actual iPhone mics through the app's own capture path
  (speaker-of-truth: the processing chain colors the data).
- Per class: ≥500 clips, ≥20 speakers/players, quiet room + noisy
  room + outdoors. Include deliberately marginal takes.
- **Compliance: training clips are consented recordings collected for
  this purpose. Never harvest user recordings — `neverUpload` samples
  cannot leave the device, so there is no telemetry pipeline to
  harvest from, by design.**
- Store user overrides locally (`userClassOverride` in the sidecar)
  as personal ground truth ONLY; they never upload.

## Training recipe (suggested)

1. Create ML sound-classification template or a small CNN
   (2–4 conv layers) over the log-mel input; ~100k parameters is
   plenty at this label granularity.
2. Augment: gain jitter ±6 dB, background-noise mix at 10–30 dB SNR,
   time-shift within the trim slack, mild EQ tilt.
3. Split by SPEAKER, not by clip, or validation lies.
4. Calibrate the softmax (temperature scaling) so `confidence` means
   the same thing it does for the heuristic: values below ~0.5 make
   the pad sheet surface the override UI more prominently.
5. Export Core ML (`.mlmodel` → compile to `.mlmodelc` at build);
   target ANE-friendly ops; verify cold-start inference <50 ms on the
   oldest supported device — classification sits inside the
   mic→playable ≤5 s budget.

## Acceptance gates before swapping the default

- ≥90% top-1 on the held-out speaker split (heuristic benches far
  below this on marginal material; that's the point).
- Confusion pairs that matter most: vocalChop↔speechWord,
  texture↔percussion(brushy), phrase↔sustainedNote(vibrato). Report
  per-pair.
- `ClassifierHeuristicTests`' synthetic exemplars must also pass
  through `CoreMLClassifier` (same XCTest suite, parameterised over
  implementations).
- Deterministic: same buffer → same (class, confidence) across runs
  and devices (fixed compute units, no sampling).
