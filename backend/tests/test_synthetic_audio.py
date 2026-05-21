"""Generate synthetic guitar-like audio with known tonal character and
verify the analyzer's outputs respond sensibly to each variant.

Run from backend/:
    python tests/test_synthetic_audio.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge import analyzer, helix_translator  # noqa: E402

SR = 44100
DUR = 4.0
OUT = Path(__file__).parent / "_generated"
OUT.mkdir(exist_ok=True)


def _guitar_note(freq: float, dur: float, *, attack_ms: float = 8,
                 decay: float = 2.5, harmonic_decay: float = 2.0,
                 n_harmonics: int = 10, sr: int = SR) -> np.ndarray:
    """Pluck-like single note with realistic harmonic structure.

    `harmonic_decay` controls how fast amplitude falls off across the
    series (1/n^harmonic_decay): 2.0 = clean, 1.0 = bright/edgy.
    """
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    out = np.zeros_like(t)
    for k in range(1, n_harmonics + 1):
        if k * freq > sr / 2.2:
            break
        amp = 1.0 / (k ** harmonic_decay)
        # Higher harmonics decay faster (string physics).
        env_h = np.exp(-decay * (1 + k * 0.25) * t)
        out += amp * np.sin(2 * np.pi * k * freq * t) * env_h
    attack_samples = max(int(sr * attack_ms / 1000), 1)
    if attack_samples < len(out):
        out[:attack_samples] *= np.linspace(0, 1, attack_samples)
    return out * 0.5


def _palm_muted_riff(freqs: list[float], note_dur: float = 0.18) -> np.ndarray:
    """Choppy riff: short bright notes (palm-muted chug character)."""
    notes = []
    for fr in freqs:
        n = _guitar_note(fr, note_dur, attack_ms=3, decay=8, harmonic_decay=1.4, n_harmonics=8)
        notes.append(n)
        notes.append(np.zeros(int(SR * 0.04)))
    return np.concatenate(notes)


def _sustained_chord(freqs: list[float], dur: float = DUR) -> np.ndarray:
    """Open chord; longer notes, faster harmonic falloff (clean-leaning)."""
    parts = [_guitar_note(fr, dur, attack_ms=12, decay=1.2, harmonic_decay=2.2, n_harmonics=8)
             for fr in freqs]
    sig = sum(parts) / len(parts)
    return sig


def _soft_clip(x: np.ndarray, drive: float) -> np.ndarray:
    """Asymmetric tanh clipping; `drive` 0..1 controls aggression.

    Tuned so drive=0.3 ≈ crunch, drive=0.7 ≈ high gain, drive=1.0 ≈ brutal.
    """
    g = 1 + drive * 9
    return np.tanh(x * g)


def _eq(x: np.ndarray, bass_db: float, mid_db: float, treble_db: float) -> np.ndarray:
    """Crude 3-band EQ via FFT shelving. Good enough for synth test material."""
    X = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), d=1 / SR)
    gain = np.ones_like(freqs)
    gain[(freqs >= 80) & (freqs < 500)]  *= 10 ** (bass_db / 20)
    gain[(freqs >= 500) & (freqs < 2500)] *= 10 ** (mid_db / 20)
    gain[(freqs >= 2500)]                 *= 10 ** (treble_db / 20)
    return np.fft.irfft(X * gain, n=len(x))


def _add_delay(x: np.ndarray, time_ms: float, feedback: float, mix: float) -> np.ndarray:
    delay_samples = int(SR * time_ms / 1000)
    out = x.copy()
    buf = np.zeros(len(x) + delay_samples * 8)
    buf[: len(x)] = x
    tap = x.copy()
    for i in range(6):
        offset = delay_samples * (i + 1)
        if offset >= len(buf):
            break
        buf[offset: offset + len(tap)] += tap * (feedback ** (i + 1))
    out = (1 - mix) * np.pad(x, (0, len(buf) - len(x))) + mix * buf
    return out[: len(x) + delay_samples * 4]


def _add_reverb(x: np.ndarray, decay: float, mix: float) -> np.ndarray:
    """Lazy reverb: convolve with exponentially-decaying white noise."""
    ir_len = int(SR * decay)
    ir = np.random.randn(ir_len) * np.exp(-np.linspace(0, 5, ir_len))
    wet = np.convolve(x, ir, mode="full")  # length len(x) + ir_len - 1
    dry = np.zeros(len(wet))
    dry[: len(x)] = x
    return (1 - mix) * dry + mix * wet * 0.3


def _normalize(x: np.ndarray, peak: float = 0.95) -> np.ndarray:
    m = np.max(np.abs(x))
    if m < 1e-9:
        return x
    return x / m * peak


# ---------------------------------------------------------------------------
# Tone presets
# ---------------------------------------------------------------------------

def make_clean_strum() -> np.ndarray:
    # Open chord, slow attack, no distortion, bright EQ — Fender-clean territory.
    sig = _sustained_chord([82.4, 110.0, 146.8, 196.0, 246.9, 329.6], dur=DUR)  # Em-ish
    sig = _eq(sig, bass_db=0, mid_db=-1, treble_db=+3)
    sig = _add_reverb(sig, decay=0.9, mix=0.10)
    return _normalize(sig)


def make_crunch_riff() -> np.ndarray:
    # Power chord chugs, mid-forward, moderate drive — JCM-ish.
    riff_freqs = [82.4, 82.4, 110.0, 82.4, 82.4, 123.5, 82.4, 110.0] * 3
    sig = _palm_muted_riff(riff_freqs, note_dur=0.22)
    sig = _eq(sig, bass_db=+1, mid_db=+3, treble_db=-1)
    sig = _soft_clip(sig, drive=0.30)
    return _normalize(sig)


def make_high_gain_scooped() -> np.ndarray:
    # Tight palm-muted chugs, scooped mids, heavy drive — Mesa Recto-ish.
    riff_freqs = [82.4] * 12 + [73.4, 82.4, 73.4, 82.4]
    sig = _palm_muted_riff(riff_freqs, note_dur=0.16)
    sig = _eq(sig, bass_db=+3, mid_db=-6, treble_db=+2)
    sig = _soft_clip(sig, drive=0.80)
    sig = _eq(sig, bass_db=0, mid_db=0, treble_db=-2)  # post-cab roll-off
    return _normalize(sig)


def make_clean_with_delay() -> np.ndarray:
    # Lead-style single notes with prominent delay + plate reverb.
    notes = [329.6, 392.0, 440.0, 493.9, 440.0, 392.0]
    parts = []
    for fr in notes:
        n = _guitar_note(fr, 0.5, attack_ms=10, decay=2.0, harmonic_decay=2.0, n_harmonics=8)
        parts.append(n)
        parts.append(np.zeros(int(SR * 0.1)))
    sig = np.concatenate(parts)
    sig = _eq(sig, bass_db=-2, mid_db=0, treble_db=+2)
    sig = _add_delay(sig, time_ms=380, feedback=0.45, mix=0.4)
    sig = _add_reverb(sig, decay=1.2, mix=0.25)
    return _normalize(sig)


# ---------------------------------------------------------------------------

PRESETS = {
    "clean_strum.wav":      (make_clean_strum,         "expect: low gain, clean family"),
    "crunch_riff.wav":      (make_crunch_riff,         "expect: mid-forward, marshall-ish, mid gain"),
    "high_gain_scooped.wav":(make_high_gain_scooped,   "expect: high gain, scooped mid, 4x12 V30-ish"),
    "clean_with_delay.wav": (make_clean_with_delay,    "expect: clean + delay + reverb detected"),
}


def main():
    for fname, (maker, expectation) in PRESETS.items():
        path = OUT / fname
        sig = maker()
        sf.write(str(path), sig, SR)
        print(f"\n=== {fname} ===")
        print(f"   {expectation}")
        d = analyzer.analyze(str(path))
        print(f"   amp: {d.amp.family:<18}  gain: {d.amp.gain:.2f}  "
              f"mid_scoop: {d.amp.voicing.mid_scoop:.2f}  "
              f"conf: {d.confidence.amp_family:.0%}")
        print(f"   cab: {d.cab.configuration} / {d.cab.speaker_character}")
        fx = []
        if d.effects.delay:      fx.append(f"delay {d.effects.delay.time_ms:.0f}ms")
        if d.effects.reverb:     fx.append(f"reverb {d.effects.reverb.type}")
        if d.effects.modulation: fx.append(f"mod {d.effects.modulation.type}")
        if d.effects.compressor: fx.append(f"comp {d.effects.compressor.amount:.2f}")
        print(f"   effects: {', '.join(fx) if fx else '(none)'}")
        card = helix_translator.translate(d)
        chain = " → ".join(p.display for p in card.picks)
        print(f"   chain: {chain}")


if __name__ == "__main__":
    main()
