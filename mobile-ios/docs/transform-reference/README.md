# PadTransform Reference

Engine-side DSP transforms for pad samples (P4). Applied in sequence by
`TransformEngine`, which chains them and peak-normalises the final
result when |peak| > 1. All transforms except `loop` modify audio; tempo-
synced transforms (stutter, gate) read `tempoBpm` from the render call.

## reverse

Exact sample-by-sample reversal: `output[i] = input[count - 1 - i]`.
No tempo dependence. Deterministic (trivially).

## stutter(StutterRate)

Tempo-synced retrigger. Replays the first `segLen` samples of the input
repeatedly at the specified rate (r1_4 = 1 beat, r1_8 = 0.5, r1_16 =
0.25, r1_32 = 0.125). Output length equals input length. Each repetition
has a short (~5 ms, clamped to segLen/4) linear fade-in and fade-out to
avoid clicks. Tempo-dependent (uses `tempoBpm`). Deterministic.

If `segLen <= 0` or `segLen >= input.count`, returns input unchanged.

## granular(GranularParams)

Offline granular resynthesis via `GranularEngine`. Output length equals
input length (the render walks linearly through the source once). See
`GranularEngine.swift` for grain-length / density / jitter / pitch-spread
semantics. Deterministic per `params.seed`; different seeds produce
different grain scatter patterns.

## stretch(Double)

Time-stretch via WSOLA (pitch-preserving). Factor is clamped to 0.25…4.0
by `WSOLAStretch`; output length is exactly `round(input.count × factor)`.
Deterministic. No tempo dependence.

## octave(Int)

Pitch-shift by ±2 octaves (clamped to -24…+24 semitones) with duration
preserved. Step 1: linear-interpolation resample at rate 2^n (shifts
pitch). Step 2: WSOLA time-stretch by the same factor (restores original
duration). `octave(0)` is identity. Deterministic. No tempo dependence.

## harmony

Chord-aware harmony voices (3rd + 5th) via `PSOLAHarmonizer`. Dry voice
at unity gain plus optional third (0.7) and fifth (0.6) with chord-aware
voice leading (picks the nearest chord tone from `chordAt(t)`, clamped to
nominal ±2 st per voiced region). If `chordAt` returns empty or is nil,
uses nominal intervals (4 st / 7 st). Output length equals input. Unvoiced
material passes through unshifted. Deterministic (PSOLA epoch detection
is deterministic). No tempo dependence; chord context is time-dependent
via the `chordAt` callback.

## choir

Dry voice plus 3× detuned copies (±7 cents, delayed 0/15/30 ms) at
1/√3 gain each, yielding a wide stereo-ish choir effect (though this is
mono). Implemented via `PSOLAHarmonizer` with all interval flags off and
`choir: true` on a dummy octave voice so the choir logic activates.
Output length equals input. Deterministic. No tempo or chord dependence.

## gate(steps: [Bool])

Rhythmic 16th-note gate. Pattern is `steps.count` long (typically 16);
step length is `round(60/bpm × 0.25 × sampleRate)`. ON steps play ×1,
OFF steps play ×0, with ~5 ms linear ramps at every on↔off boundary
(clamped to stepLen/4). Tempo-dependent (uses `tempoBpm`). Deterministic.

Empty `steps` or `stepLen <= 0` returns input unchanged.

## loop

Playback flag only — render is identity. The app-layer pad-player maps
this to a loop-trigger mode (retrigger vs. gate-toggle). Deterministic.

## spectralFreeze(atSec: Double, seed: UInt64)

Captures the magnitude spectrum of a 2048-sample Hann frame at `atSample`
(= max(0, Int(atSec × sampleRate))) and resynthesizes the input's full
duration by pairing those frozen magnitudes with fresh random phases every
512-sample hop. Output length equals input length. Deterministic per
`seed` (phase randomness comes from a single `SplitMix64(seed:)` stream).
See `SpectralFreeze.swift` for DFT plumbing details.

---

## Chain Behavior

`TransformEngine.render` applies the chain in sequence: each transform
reads the prior stage's output. Empty input → empty output at any stage
terminates the chain early. After the full chain, the result is peak-
normalised only if |peak| > 1 (scaled to 0.999, matching
`GranularEngine`). Empty chain → identity.

## Determinism & Seeds

All seeded transforms (`granular`, `spectralFreeze`) are bit-deterministic
per seed: same (input, params, seed) → bit-identical output across runs
and platforms. Non-seeded transforms (`reverse`, `stutter`, `stretch`,
`octave`, `harmony`, `choir`, `gate`, `loop`) are deterministic given
their inputs (no randomness). Tempo-synced transforms (`stutter`, `gate`)
depend on `tempoBpm` but are otherwise deterministic.

## Golden Gates

The behavioral contracts are asserted in `TransformEngineTests.swift`:
exact reversal, stutter segment replay, granular seed identity, stretch
length/pitch-preservation, octave pitch/duration, harmony energy boost,
choir texture, gate silence/tone, loop identity, spectralFreeze
determinism, chain determinism, empty-chain identity, and peak-
normalisation guard. No WAV fixtures — the DSP kernels have their own
low-level test suites; these tests gate the transform-level semantics.
