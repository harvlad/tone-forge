"""Audio analyzer: clip -> ToneDescriptor.

V2 adds optional ML-based confidence scoring that supplements DSP heuristics.
When ML models are available, confidence is computed using trained XGBoost
classifiers. Otherwise, falls back to heuristic confidence scoring.

Pipeline:
  1. Load audio (librosa) — mono, 22050 Hz is enough for guitar.
  2. Compute a small bundle of base features once (`_compute_features`).
  3. Each domain extractor reads from that bundle:
       _estimate_gain        — spectral flatness + median crest factor
       _estimate_voicing     — band ratios with bass-dominance prior
       _classify_amp_family  — score table over voicing + gain
       _classify_cab         — 3-4kHz peak character vs broader mids
       _detect_delay         — envelope autocorrelation peak (find_peaks)
       _detect_reverb        — quiet-to-loud RMS ratio + HF persistence
       _detect_modulation    — LFO peak in envelope FFT
       _detect_compressor    — dynamic range of frame RMS
       _infer_guitar_context — spectral centroid + onset density
  4. (NEW) Optionally compute ML-based confidence using trained models.
  5. (NEW) Optionally generate audio embeddings for similarity search.

Known limitations:
  * Calibration thresholds are tunable; ML models provide better estimates
    when trained on labeled data.
  * Cab configuration (1x12 vs 4x12) is unrecoverable from audio alone;
    we use an amp-family prior instead.
  * Overdrive-pedal vs amp-distortion can't be separated from a mixed
    signal without stem separation (uses Demucs).
  * Reverb detection can false-positive on heavily compressed/sustained
    distortion; the quiet/loud heuristic helps but isn't perfect.

All thresholds are documented inline. They're meant to be tunable;
ML models can learn better thresholds from labeled data.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import librosa
import numpy as np

from .descriptor import (
    Amp, Cab, Compressor, Confidence, Delay, Effects, Guitar, Modulation,
    Reverb, Source, ToneDescriptor, Voicing,
)

# ML imports (optional, graceful degradation when not available)
_ML_AVAILABLE = False
try:
    from .ml.confidence import (
        extract_ml_features,
        compute_ml_confidence,
        is_ready as ml_is_ready,
    )
    from .ml.embeddings import (
        get_embedder,
        get_similarity_search,
        is_encoder_ready,
    )
    _ML_AVAILABLE = True
except ImportError:
    pass

# Reconstruction quality imports (optional)
_RECONSTRUCTION_AVAILABLE = False
try:
    from .reconstruction.stem_quality import StemQuality
    from .reconstruction.contamination import ContaminationAnalysis
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    StemQuality = None
    ContaminationAnalysis = None


_SR = 22050  # analysis sample rate; plenty for guitar
_N_FFT = 2048
_HOP = 512


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze(
    path: str | Path,
    source_kind: str = "isolated_guitar",
    display_name: str | None = None,
    use_ml_confidence: bool = True,
    index_for_similarity: bool = False,
    stem_quality: Optional["StemQuality"] = None,
    contamination: Optional["ContaminationAnalysis"] = None,
) -> ToneDescriptor:
    """Analyze an audio file and return a descriptor.

    Args:
        path: Path to the audio file on disk.
        source_kind: Whether the audio is isolated guitar, a separated stem,
            or a full mix. If "full_mix", stem separation is run first using
            Demucs to isolate the guitar.
        display_name: Optional override for the descriptor's filename field —
            useful when the on-disk path is a temp file but the original
            upload name should be surfaced (e.g. via the web upload endpoint).
        use_ml_confidence: Whether to use ML-based confidence scoring when
            available. Falls back to heuristics if ML models aren't loaded.
        index_for_similarity: Whether to index the tone for similarity search.
            Requires ML embeddings to be available.
        stem_quality: Optional StemQuality from reconstruction module. When
            provided, confidence scores are adjusted based on stem quality
            (contamination, artifacts, etc.). This helps rules_engine make
            better decisions about alternate picks and tweak hints.
        contamination: Optional ContaminationAnalysis from reconstruction
            module. Provides additional signal about cross-stem bleed.
    """
    path = Path(path)
    analysis_path = path  # May be overridden by stem separation

    # For full mix input, run stem separation first to isolate guitar
    if source_kind == "full_mix":
        from . import stem_separator
        if not stem_separator.is_available():
            raise RuntimeError(
                "Full mix analysis requires Demucs. "
                "Install with: pip install demucs torch torchaudio"
            )
        # Separate guitar stem and analyze that instead
        analysis_path = stem_separator.separate_guitar(path)
        # Update source_kind to reflect we're now analyzing a separated stem
        source_kind = "stem_separated"

    y, sr = librosa.load(str(analysis_path), sr=_SR, mono=True)
    feats = _compute_features(y, sr)

    gain, gain_conf = _estimate_gain(feats)
    voicing = _estimate_voicing(feats)
    amp_family, amp_conf, amp_alternates = _classify_amp_family(voicing, gain, feats)
    cab, cab_conf = _classify_cab(voicing, feats, amp_family)
    effects, fx_conf = _detect_effects(feats)
    guitar = _infer_guitar_context(feats)

    amp = Amp(family=amp_family, gain=gain, voicing=voicing, alternates=amp_alternates)  # type: ignore[arg-type]

    # Compute confidence: ML-based if available, otherwise heuristic
    if use_ml_confidence and _ML_AVAILABLE and ml_is_ready():
        confidence = _compute_ml_confidence(y, sr, amp_family, gain, cab.speaker_character, effects)
    else:
        confidence = Confidence(
            amp_family=amp_conf,
            gain=gain_conf,
            cab=cab_conf,
            effects=fx_conf,
        )

    # Adjust confidence based on stem quality if provided
    if stem_quality is not None or contamination is not None:
        confidence = _adjust_confidence_for_quality(
            confidence, stem_quality, contamination
        )

    descriptor = ToneDescriptor(
        source=Source(
            kind=source_kind,  # type: ignore[arg-type]
            duration_sec=float(len(y) / sr),
            sample_rate=int(sr),
            filename=display_name or path.name,
        ),
        guitar=guitar,
        amp=amp,
        cab=cab,
        effects=effects,
        confidence=confidence,
    )

    # Optionally index for similarity search
    if index_for_similarity and _ML_AVAILABLE and is_encoder_ready():
        try:
            search = get_similarity_search()
            search.index_tone(y, sr, descriptor.to_dict())
        except Exception as e:
            # Don't fail analysis if indexing fails
            import logging
            logging.getLogger(__name__).warning(f"Failed to index tone: {e}")

    return descriptor


def _compute_ml_confidence(
    y: np.ndarray,
    sr: int,
    amp_family: str,
    gain: float,
    speaker_char: str,
    effects: Effects,
) -> Confidence:
    """Compute ML-based confidence scores.

    Falls back to heuristic confidence if ML models fail.
    """
    try:
        # Extract ML features
        ml_features = extract_ml_features(y, sr)

        # Build effects dict
        detected_effects = {
            "delay": effects.delay is not None,
            "reverb": effects.reverb is not None,
            "modulation": effects.modulation is not None,
            "compression": effects.compressor is not None,
        }

        # Get ML confidence scores
        scores = compute_ml_confidence(
            features=ml_features,
            predicted_amp_family=amp_family,
            predicted_gain=gain,
            predicted_cab=speaker_char,
            detected_effects=detected_effects,
        )

        return Confidence(
            amp_family=scores.amp_family,
            gain=scores.gain,
            cab=scores.cab,
            effects=scores.effects,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"ML confidence failed, using heuristic: {e}")
        # Return default heuristic confidence
        return Confidence(
            amp_family=0.5,
            gain=0.5,
            cab=0.5,
            effects=0.5,
        )


def _adjust_confidence_for_quality(
    confidence: Confidence,
    stem_quality: Optional["StemQuality"],
    contamination: Optional["ContaminationAnalysis"],
) -> Confidence:
    """Adjust confidence scores based on stem quality analysis.

    When stems have quality issues (contamination, artifacts, low SNR),
    we reduce confidence scores proportionally. This causes rules_engine
    to:
    - Show more alternate amp picks (triggers at confidence < 0.7)
    - Add tweak hints about uncertainty (triggers at confidence < 0.6)

    The adjustment is multiplicative to preserve relative ordering while
    expressing uncertainty about the entire analysis.
    """
    amp_factor = 1.0
    gain_factor = 1.0
    cab_factor = 1.0
    effects_factor = 1.0

    if stem_quality is not None:
        # Overall quality affects all confidence scores
        quality = getattr(stem_quality, 'overall_quality', None)
        if quality is not None:
            # Map quality 0-1 to multiplier 0.5-1.0
            # Low quality (0.3) -> 0.65 multiplier
            # High quality (0.9) -> 0.95 multiplier
            base_factor = 0.5 + (quality * 0.5)
            amp_factor *= base_factor
            gain_factor *= base_factor
            cab_factor *= base_factor
            effects_factor *= base_factor

        # Harmonic purity affects amp and cab detection
        harmonic_purity = getattr(stem_quality, 'harmonic_purity', None)
        if harmonic_purity is not None and harmonic_purity < 0.5:
            purity_penalty = 0.7 + (harmonic_purity * 0.6)  # 0.7-1.0
            amp_factor *= purity_penalty
            cab_factor *= purity_penalty

        # Transient integrity affects gain estimation
        transient_integrity = getattr(stem_quality, 'transient_integrity', None)
        if transient_integrity is not None and transient_integrity < 0.5:
            transient_penalty = 0.8 + (transient_integrity * 0.4)  # 0.8-1.0
            gain_factor *= transient_penalty

        # Reverb density affects cab and effects detection
        reverb_density = getattr(stem_quality, 'reverb_density', None)
        if reverb_density is not None and reverb_density > 0.6:
            # Heavy reverb makes cab character hard to read
            reverb_penalty = 1.0 - ((reverb_density - 0.6) * 0.5)  # 0.8-1.0
            cab_factor *= reverb_penalty
            effects_factor *= reverb_penalty

    if contamination is not None:
        # Cross-stem contamination affects all confidence
        contamination_score = getattr(contamination, 'overall_contamination', None)
        if contamination_score is not None and contamination_score > 0.3:
            # Contaminated stems are unreliable
            contam_penalty = 1.0 - ((contamination_score - 0.3) * 0.7)  # 0.51-1.0
            contam_penalty = max(0.5, contam_penalty)  # Floor at 0.5
            amp_factor *= contam_penalty
            gain_factor *= contam_penalty
            cab_factor *= contam_penalty
            effects_factor *= contam_penalty

    # Apply factors and clamp to valid range
    return Confidence(
        amp_family=float(np.clip(confidence.amp_family * amp_factor, 0.1, 0.95)),
        gain=float(np.clip(confidence.gain * gain_factor, 0.1, 0.95)),
        cab=float(np.clip(confidence.cab * cab_factor, 0.1, 0.95)),
        effects=float(np.clip(confidence.effects * effects_factor, 0.1, 0.95)),
    )


# ---------------------------------------------------------------------------
# Feature bundle (computed once, read by everyone)
# ---------------------------------------------------------------------------

@dataclass
class _Features:
    y: np.ndarray
    sr: int
    rms: np.ndarray
    env: np.ndarray
    spec: np.ndarray
    freqs: np.ndarray
    band_energy: dict
    centroid: float
    rolloff_95: float
    crest_db: float            # full-clip peak-to-RMS
    crest_db_median: float     # median over 50ms windows (robust to sustain)
    flatness: float            # mean spectral flatness in 300–5000 Hz (distortion proxy)
    quiet_loud_ratio: float    # RMS p20 / p80 (high = lots of room signal)
    onsets_per_sec: float
    duration_sec: float


def _compute_features(y: np.ndarray, sr: int) -> _Features:
    if len(y) < _N_FFT:
        y = np.pad(y, (0, _N_FFT - len(y)))

    rms = librosa.feature.rms(y=y, frame_length=_N_FFT, hop_length=_HOP)[0]
    env = _smooth(librosa.feature.rms(y=y, frame_length=512, hop_length=_HOP)[0], 9)
    spec = np.abs(librosa.stft(y, n_fft=_N_FFT, hop_length=_HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=_N_FFT)

    band_energy = {
        "sub":       _band_mean(spec, freqs, 20, 80),
        "bass":      _band_mean(spec, freqs, 80, 250),
        "low_mid":   _band_mean(spec, freqs, 250, 500),
        "mid":       _band_mean(spec, freqs, 500, 2000),
        "upper_mid": _band_mean(spec, freqs, 2000, 4000),
        "treble":    _band_mean(spec, freqs, 4000, 6000),
        "presence":  _band_mean(spec, freqs, 6000, 10000),
        "air":       _band_mean(spec, freqs, 10000, 16000),
    }

    centroid = float(np.mean(librosa.feature.spectral_centroid(S=spec, sr=sr)))
    rolloff_95 = float(np.mean(librosa.feature.spectral_rolloff(S=spec, sr=sr, roll_percent=0.95)))

    # Peak-to-RMS over the whole clip.
    peak = float(np.max(np.abs(y))) + 1e-12
    rms_full = float(np.sqrt(np.mean(y ** 2))) + 1e-12
    crest_db = 20.0 * np.log10(peak / rms_full)

    # Robust crest factor: median over 50ms windows that actually contain signal.
    # Avoids sustained clean signals reading as "compressed".
    win = max(int(0.05 * sr), 64)
    n_win = len(y) // win
    if n_win >= 2:
        chunks = y[: n_win * win].reshape(n_win, win)
        chunk_peaks = np.max(np.abs(chunks), axis=1)
        chunk_rms = np.sqrt(np.mean(chunks ** 2, axis=1)) + 1e-12
        mask = chunk_peaks > 0.05 * peak
        if np.any(mask):
            crest_db_median = float(np.median(20 * np.log10(chunk_peaks[mask] / chunk_rms[mask])))
        else:
            crest_db_median = crest_db
    else:
        crest_db_median = crest_db

    # Spectral flatness: distortion fills the spectrum with harmonics, raising
    # flatness. Restrict to 300–5000 Hz where guitar distortion shows clearly.
    flat_mask = (freqs >= 300) & (freqs <= 5000)
    if np.any(flat_mask):
        flat_spec = spec[flat_mask, :]
        # Geometric mean / arithmetic mean, per frame, then averaged.
        log_spec = np.log(flat_spec + 1e-9)
        geo = np.exp(np.mean(log_spec, axis=0))
        ari = np.mean(flat_spec, axis=0) + 1e-9
        flatness = float(np.mean(geo / ari))
    else:
        flatness = 0.0

    # Quiet-to-loud ratio: if quiet frames still have appreciable energy,
    # there's room/reverb tail. Dry signals drop close to zero between notes.
    rms_sorted = np.sort(rms)
    if len(rms_sorted) >= 5:
        p20 = float(rms_sorted[len(rms_sorted) // 5])
        p80 = float(rms_sorted[len(rms_sorted) * 4 // 5]) + 1e-9
        quiet_loud_ratio = p20 / p80
    else:
        quiet_loud_ratio = 0.0

    onsets = librosa.onset.onset_detect(y=y, sr=sr, hop_length=_HOP, units="time")
    duration_sec = len(y) / sr
    onsets_per_sec = float(len(onsets) / max(duration_sec, 1e-6))

    return _Features(
        y=y, sr=sr, rms=rms, env=env, spec=spec, freqs=freqs,
        band_energy=band_energy, centroid=centroid, rolloff_95=rolloff_95,
        crest_db=crest_db, crest_db_median=crest_db_median,
        flatness=flatness, quiet_loud_ratio=quiet_loud_ratio,
        onsets_per_sec=onsets_per_sec, duration_sec=duration_sec,
    )


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def _estimate_gain(f: _Features) -> Tuple[float, float]:
    """Combine spectral flatness and crest factor → gain in [0,1].

    Reverb-aware: when the quiet/loud RMS ratio is high (lots of reverb
    tail), the median-windowed crest factor lies — every 50ms window
    is filled with sustained tail energy and reads as "compressed".
    The full-clip peak-to-RMS is more honest in that regime. Same idea
    for spectral flatness: reverb diffusion adds noise-like content
    that inflates flatness without any actual distortion.

    Flatness reference points:
      ~0.02-0.06    clean
      ~0.10-0.18    crunch
      ~0.22+        high gain

    Crest factor reference points:
      ~15-25 dB     clean
      ~8-14 dB      crunch
      ~3-8 dB       high gain
    """
    # When reverb is heavy, the windowed crest is dragged toward 3-8 dB
    # by sustained tails. Blend toward the full-clip crest as quiet/loud
    # ratio rises (0.10 = mostly dry, 0.35+ = wet).
    reverb_weight = float(np.clip((f.quiet_loud_ratio - 0.10) / 0.25, 0.0, 1.0))
    effective_crest_db = (1 - reverb_weight) * f.crest_db_median + reverb_weight * f.crest_db

    # Same correction for flatness: reverb decorrelates the spectrum, so
    # subtract a reverb-driven floor estimate from the measured flatness.
    flatness_corrected = max(f.flatness - 0.10 * reverb_weight, 0.0)

    # Raised the flatness floor from 0.04 to 0.06 to account for harmonic
    # content in clean guitar signals. Expanded range to 0.22 for better
    # discrimination between clean and crunch.
    flat_gain = float(np.clip((flatness_corrected - 0.06) / 0.22, 0.0, 1.0))

    # Crest factor: very high crest (>18 dB) strongly indicates clean signal.
    # Adjusted range to better separate clean from crunch.
    crest_gain = float(np.clip((20.0 - effective_crest_db) / 16.0, 0.0, 1.0))

    # If crest is very high (clean signal), cap the gain estimate.
    if effective_crest_db > 16.0:
        crest_gain = min(crest_gain, 0.25)

    g = float(np.clip(0.55 * flat_gain + 0.45 * crest_gain, 0.0, 1.0))

    agreement = 1.0 - abs(flat_gain - crest_gain)
    conf = float(np.clip(0.45 + 0.35 * agreement, 0.0, 0.95))
    return g, conf


def _estimate_voicing(f: _Features) -> Voicing:
    """Voicing from band-ratios within the guitar-signal range.

    Naive (bass / mid / treble / presence) normalization is dragged
    around by bass dominance in natural guitar spectra. Instead we work
    with two ratios:
      * low_mid_balance = bass / (bass + mid)   → high = bass-heavy
      * upper_balance   = treble / (mid + treble) → high = bright

    And derive mid_scoop directly from whether the mid band is
    significantly below both neighbors on either side.
    """
    be = f.band_energy
    bass = be["bass"] + be["low_mid"]
    mid = be["mid"]
    treble = be["upper_mid"] + be["treble"]
    presence = be["presence"]
    total = bass + mid + treble + presence + 1e-9

    # Each in [0,1], normalized to a 0.5 "neutral" point.
    # Guitar's natural spectrum has more bass; we compensate via a
    # rough prior (bass typically 50% of total energy on clean signals).
    bass_norm     = float(np.clip(bass / total / 0.50,  0.0, 1.0)) * 0.5 + 0.25
    mid_norm      = float(np.clip(mid / total / 0.25,   0.0, 1.0)) * 0.5 + 0.25
    treble_norm   = float(np.clip(treble / total / 0.15, 0.0, 1.0)) * 0.5 + 0.25
    presence_norm = float(np.clip(presence / total / 0.05, 0.0, 1.0)) * 0.5 + 0.25

    # Mid scoop: compare mid band to (bass + treble) ratio.
    # A scooped signal has the mid band significantly lower than the
    # surrounding bands. Use linear ratio to avoid log domain issues.
    # mid_ratio = mid / (bass + treble + 1e-9)
    # For guitar: mid_ratio ~0.3-0.5 is normal, <0.2 is scooped.
    neighbors = bass + treble + 1e-9
    mid_ratio = mid / neighbors
    # If mid_ratio < 0.15, fully scooped. If > 0.35, no scoop.
    mid_scoop = float(np.clip((0.35 - mid_ratio) / 0.20, 0.0, 1.0))

    return Voicing(
        bass=bass_norm, mid=mid_norm, treble=treble_norm,
        presence=presence_norm, mid_scoop=mid_scoop,
    )


def _classify_amp_family(v: Voicing, gain: float, f: _Features) -> Tuple[str, float, list]:
    """Returns (family, confidence, alternates).

    `alternates` is a list of up to 2 runner-up families with their scores,
    intended for A/B auditioning when confidence on the primary is low.
    """
    scores = {
        "fender_clean":    _score_clean(v, gain, f, bright=True),
        "tweed":           _score_clean(v, gain, f, bright=False),
        "vox_chime":       _score_vox(v, gain, f),
        "ac30":            _score_vox(v, gain, f) * 0.9,
        "marshall_plexi":  _score_marshall(v, gain, f, hot=False),
        "marshall_jcm":    _score_marshall(v, gain, f, hot=True),
        "mesa_rectifier":  _score_mesa(v, gain, f),
        "5150_peavey":     _score_5150(v, gain, f),
        "bogner":          _score_modern_high_gain(v, gain, f),
        "soldano":         _score_modern_high_gain(v, gain, f) * 0.95,
        "dumble":          _score_dumble(v, gain, f),
    }
    sorted_items = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top_score = sorted_items[0]
    second_score = sorted_items[1][1]
    margin = (top_score - second_score) / (top_score + 1e-6)
    confidence = float(np.clip(0.4 + margin, 0.0, 0.95))

    # Alternates: top 2 runner-ups, but only if they have non-trivial scores.
    alternates = [
        {"family": name, "score": float(score)}
        for name, score in sorted_items[1:3]
        if score > top_score * 0.4
    ]

    if top_score < 0.02:
        return "unknown", 0.3, alternates
    return top_name, confidence, alternates


def _score_clean(v: Voicing, gain: float, f: _Features, bright: bool) -> float:
    """Score for clean amp families (fender_clean, tweed).

    Uses a softer gain penalty curve and considers crest factor as additional
    evidence of clean signal (high crest = dynamic, uncompressed = clean).
    """
    # Softer gain penalty: still scores decently up to gain ~0.45
    s = max(0.0, 0.7 - gain * 1.2)

    # High crest factor is strong evidence of clean signal
    if f.crest_db > 14.0:
        s *= 1.0 + (f.crest_db - 14.0) * 0.05

    # Clean amps typically aren't heavily scooped
    s *= 1.0 - v.mid_scoop * 0.5

    if bright:
        s *= 0.5 + v.treble
    else:
        s *= 0.5 + (1 - v.treble)
    return s


def _score_vox(v: Voicing, gain: float, f: _Features) -> float:
    s = _gain_pref(gain, 0.2, 0.55)
    chime = f.band_energy["upper_mid"] / (f.band_energy["mid"] + 1e-9)
    s *= float(np.clip((chime - 1.0) / 1.0, 0.0, 1.5))
    s *= 0.4 + v.treble
    return s


def _score_marshall(v: Voicing, gain: float, f: _Features, hot: bool) -> float:
    target = 0.6 if hot else 0.4
    s = _gain_pref(gain, target - 0.15, target + 0.15)
    s *= 0.3 + v.mid
    s *= 1.0 - v.mid_scoop * 0.7
    return s


def _score_mesa(v: Voicing, gain: float, f: _Features) -> float:
    s = _gain_pref(gain, 0.7, 0.95)
    s *= 0.3 + v.mid_scoop
    s *= 0.4 + v.bass
    return s


def _score_5150(v: Voicing, gain: float, f: _Features) -> float:
    s = _gain_pref(gain, 0.75, 0.98)
    upper_mid_bite = f.band_energy["upper_mid"] / (f.band_energy["bass"] + 1e-9)
    s *= float(np.clip(upper_mid_bite / 1.5, 0.0, 1.5))
    return s


def _score_modern_high_gain(v: Voicing, gain: float, f: _Features) -> float:
    s = _gain_pref(gain, 0.6, 0.85)
    s *= 1.0 - abs(v.mid - 0.5) * 1.5
    s *= 0.5 + v.presence * 0.8
    return s


def _score_dumble(v: Voicing, gain: float, f: _Features) -> float:
    s = _gain_pref(gain, 0.3, 0.55)
    s *= 1.0 - v.mid_scoop
    s *= 0.3 + v.mid
    return s * 0.85


def _gain_pref(gain: float, lo: float, hi: float) -> float:
    center = (lo + hi) / 2
    half = (hi - lo) / 2 + 1e-6
    return float(max(0.0, 1.0 - abs(gain - center) / half))


# ---------------------------------------------------------------------------

def _classify_cab(v: Voicing, f: _Features, amp_family: str) -> Tuple[Cab, float]:
    """Speaker character from how energy is distributed in 500 Hz–10 kHz.

    Earlier versions used raw band ratios. That broke on clean signals
    where treble and presence are both near zero — a tiny-over-tiny
    ratio falsely "discovered" a V30-like 3 kHz peak. The fix: work
    with each band as a fraction of the upper-band total. If the
    upper bands are essentially silent, the cab character is
    unrecoverable from audio — defer to an amp-family prior.
    """
    be = f.band_energy
    total_all = sum(be[k] for k in ("bass", "low_mid", "mid", "upper_mid", "treble", "presence")) + 1e-9
    upper_total = be["mid"] + be["upper_mid"] + be["treble"] + be["presence"] + 1e-9

    # Amp-family prior for speaker character (used when we can't read it).
    char_prior = {
        "fender_clean": "jensen_like",
        "tweed":        "jensen_like",
        "vox_chime":    "alnico_blue_like",
        "ac30":         "alnico_blue_like",
        "marshall_plexi": "g12m_like",
        "marshall_jcm":   "g12h_like",
        "mesa_rectifier": "v30_like",
        "5150_peavey":    "v30_like",
        "bogner":         "v30_like",
        "soldano":        "v30_like",
        "dumble":         "g12h_like",
        "unknown":        "v30_like",
    }

    # If less than 5% of total spectral energy is above 500 Hz, the
    # cab character signature (which lives in 2-8 kHz) is unreliable.
    upper_fraction = upper_total / total_all
    if upper_fraction < 0.05:
        char = char_prior.get(amp_family, "v30_like")
        return _build_cab(char, amp_family, mic="on_axis_cap"), 0.35

    # Work in fractions of upper-band energy — no risk of tiny-over-tiny.
    mid_f       = be["mid"]       / upper_total
    upper_mid_f = be["upper_mid"] / upper_total
    treble_f    = be["treble"]    / upper_total
    presence_f  = be["presence"]  / upper_total

    # Signature shapes (each in [0,1] roughly):
    #   V30:     prominent upper_mid bump, dark above 5 kHz.
    #   G12M:    moderate mid-bump, gentle treble (greenback).
    #   G12H:    flatter than V30, brighter than G12M.
    #   Alnico:  smooth mid, modest presence (vintage Vox/Fender).
    #   Jensen:  bright, presence-forward (small clean combos).
    scores = {
        "v30_like":         upper_mid_f * (1.0 - presence_f * 2.0),
        "g12m_like":        upper_mid_f * (0.5 + mid_f) * (1.0 - treble_f),
        "g12h_like":        treble_f * (1.0 - presence_f),
        "alnico_blue_like": mid_f * (0.5 + presence_f * 1.5),
        "jensen_like":      presence_f * (0.5 + treble_f),
    }
    sorted_items = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    char, top_score = sorted_items[0]
    second_score = sorted_items[1][1]

    # If even the top score is weak (no real character emerges), use the
    # prior instead of forcing a noisy pick.
    if top_score < 0.05:
        char = char_prior.get(amp_family, "v30_like")
        return _build_cab(char, amp_family, mic="on_axis_cap"), 0.4

    margin = (top_score - second_score) / (top_score + 1e-6)
    confidence = float(np.clip(0.35 + margin, 0.0, 0.85))
    return _build_cab(char, amp_family, mic="on_axis_cap"), confidence


def _build_cab(char: str, amp_family: str, mic: str) -> Cab:
    config_prior = {
        "mesa_rectifier": "4x12", "5150_peavey": "4x12",
        "bogner": "4x12", "soldano": "4x12",
        "marshall_jcm": "4x12", "marshall_plexi": "4x12",
        "vox_chime": "2x12", "ac30": "2x12",
        "fender_clean": "1x12", "tweed": "1x12", "dumble": "2x12",
        "unknown": "4x12",
    }
    config = config_prior.get(amp_family, "4x12")
    return Cab(configuration=config, speaker_character=char, mic_position=mic)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------

def _detect_effects(f: _Features) -> Tuple[Effects, float]:
    delay = _detect_delay(f)
    reverb = _detect_reverb(f)
    modulation = _detect_modulation(f)
    # Overdrive-pedal vs amp-distortion is unreliable without stem
    # separation telling us the amp itself is mid-gain. TODO.
    overdrive_pedal = None
    compressor = _detect_compressor(f)

    fx = Effects(
        overdrive_pedal=overdrive_pedal,
        compressor=compressor,
        modulation=modulation,
        delay=delay,
        reverb=reverb,
    )
    detections = [
        delay.mix if delay else 0.0,
        reverb.mix if reverb else 0.0,
        modulation.depth if modulation else 0.0,
    ]
    fx_conf = float(0.5 + 0.3 * max(detections))
    return fx, fx_conf


def _detect_delay(f: _Features) -> Optional[Delay]:
    """Find an actual local peak in envelope autocorrelation in [120ms, 900ms].

    Using argmax is wrong: for a sustained signal, autocorrelation
    monotonically decays from lag 0, so argmax just picks min_lag. We
    require a real *local* maximum with prominence above noise.
    """
    from scipy.signal import find_peaks

    env = f.env - np.mean(f.env)
    if np.std(env) < 1e-6:
        return None
    autocorr = np.correlate(env, env, mode="full")
    autocorr = autocorr[len(autocorr) // 2:]
    if autocorr[0] < 1e-9:
        return None
    autocorr = autocorr / autocorr[0]

    hop_sec = _HOP / f.sr
    min_lag = max(int(0.12 / hop_sec), 1)
    max_lag = min(int(0.9 / hop_sec), len(autocorr) - 1)
    if max_lag <= min_lag:
        return None

    region = autocorr[min_lag:max_lag]
    # Relaxed thresholds: prominence 0.05 (was 0.08), height 0.15 (was 0.20)
    # to catch delays that are partially masked by reverb tails.
    peaks, props = find_peaks(region, prominence=0.05, height=0.15)
    if len(peaks) == 0:
        return None

    best = int(peaks[np.argmax(props["prominences"])])
    peak_val = float(region[best])
    time_ms = (min_lag + best) * hop_sec * 1000.0
    mix = float(np.clip(peak_val * 0.8, 0.0, 0.6))

    # Classify delay type based on characteristics:
    # - Digital: clean repeats, any timing
    # - Analog BBD: shorter times, slight degradation
    # - Tape: longer times, warmer/darker repeats
    if time_ms > 400:
        dtype = "tape"  # Long delays often sound like tape echo
    elif time_ms > 250 and peak_val < 0.45:
        dtype = "analog_bbd"  # Medium times with decay suggest analog
    else:
        dtype = "digital"

    return Delay(type=dtype, time_ms=float(time_ms), feedback=float(peak_val), mix=mix)


def _detect_reverb(f: _Features) -> Optional[Reverb]:
    """Reverb shows up as energy *between* notes that's bright/diffuse.

    A heavily distorted guitar has long sustain too, so the simple
    'late energy after onset' metric false-positives. Instead use the
    quiet/loud RMS ratio: dry signals drop sharply between notes, wet
    signals stay elevated.

    Combined with: high-frequency content in the quietest frames is a
    strong reverb tell (sustain on distorted amp would be mostly mids).
    """
    if f.quiet_loud_ratio < 0.10:
        return None  # too dry to be reverb-driven

    # Use HF energy in quiet frames as additional cue.
    rms = f.rms
    if len(rms) < 10:
        return None
    quiet_threshold = float(np.percentile(rms, 30))
    quiet_frames = rms < quiet_threshold
    if not np.any(quiet_frames):
        return None
    # Mean HF energy in quiet frames, normalized by mean HF in loud frames.
    hf_mask = (f.freqs >= 2000) & (f.freqs < 6000)
    if not np.any(hf_mask):
        return None
    hf_quiet = float(np.mean(f.spec[hf_mask][:, quiet_frames]))
    hf_loud = float(np.mean(f.spec[hf_mask][:, ~quiet_frames]) + 1e-9)
    hf_persist = hf_quiet / hf_loud

    # Combine signals. Sustain on a distorted amp keeps mids alive but
    # the quiet-loud ratio of overall RMS will still be lower than for
    # real reverb because there are still note-off gaps in a riff.
    mix = float(np.clip((f.quiet_loud_ratio - 0.10) * 1.8, 0.0, 0.7))
    mix *= float(np.clip(hf_persist * 1.5, 0.3, 1.5))
    mix = float(np.clip(mix, 0.0, 0.7))
    if mix < 0.05:
        return None

    size = float(np.clip(f.quiet_loud_ratio * 1.5, 0.0, 1.0))
    if size > 0.55:
        rtype = "hall"
    elif size > 0.3:
        rtype = "plate"
    else:
        rtype = "room"
    return Reverb(type=rtype, size=size, mix=mix)


def _detect_modulation(f: _Features) -> Optional[Modulation]:
    env = f.env - np.mean(f.env)
    if np.std(env) < 1e-6 or len(env) < 64:
        return None
    fps = f.sr / _HOP
    spec = np.abs(np.fft.rfft(env))
    freqs = np.fft.rfftfreq(len(env), d=1.0 / fps)
    band = (freqs >= 0.5) & (freqs <= 8.0)
    if not np.any(band):
        return None
    band_spec = spec[band]
    band_freqs = freqs[band]
    peak_idx = int(np.argmax(band_spec))
    peak_val = float(band_spec[peak_idx])
    total = float(np.sum(spec) + 1e-9)
    prominence = peak_val / total
    # Stricter: real chorus/tremolo LFOs are very prominent in the envelope FFT.
    # Loose riffs and chord rhythm also create envelope variation, so demand
    # a clear peak well above the broadband level.
    if prominence < 0.20:
        return None
    rate_hz = float(band_freqs[peak_idx])
    rate_norm = float(np.clip((rate_hz - 0.5) / 7.5, 0.0, 1.0))
    depth = float(np.clip(prominence * 2.0, 0.0, 1.0))
    mtype = "tremolo" if rate_hz > 2.5 else "chorus"
    return Modulation(type=mtype, rate=rate_norm, depth=depth)


def _detect_compressor(f: _Features) -> Optional[Compressor]:
    rms = f.rms
    if len(rms) < 10:
        return None
    p95 = float(np.percentile(rms, 95))
    p20 = float(np.percentile(rms, 20)) + 1e-9
    dyn_db = 20 * np.log10(p95 / p20)
    amount = float(np.clip((25 - dyn_db) / 17.0, 0.0, 1.0))
    if amount < 0.2:
        return None
    return Compressor(amount=amount, character="studio")


# ---------------------------------------------------------------------------

def _infer_guitar_context(f: _Features) -> Guitar:
    brightness = float(np.clip((f.centroid - 800) / 2400, 0.0, 1.0))
    if f.onsets_per_sec > 4.5:
        style = "palm_mute"
    elif f.onsets_per_sec > 2.0:
        style = "chord_riff"
    elif f.onsets_per_sec > 0.6:
        style = "lead"
    else:
        style = "clean_strum"
    return Guitar(pickup_brightness=brightness, playing_style=style, estimated_tuning="unknown")


# ---------------------------------------------------------------------------

def _band_mean(spec: np.ndarray, freqs: np.ndarray, lo: float, hi: float) -> float:
    mask = (freqs >= lo) & (freqs < hi)
    if not np.any(mask):
        return 0.0
    return float(np.mean(spec[mask, :]))


def _smooth(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(x) < window:
        return x
    kernel = np.ones(window) / window
    return np.convolve(x, kernel, mode="same")
