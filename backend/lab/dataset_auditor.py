"""Dataset Auditor — CPU-first quality gate + regime metadata for every track.

The hard lesson of Wave-4 runs 1-8: a $0 local RMS scan found what $3.77 of GPU
could not (36% of "guitar" tracks had no guitar). This module makes that scan
mandatory and automatic. NO track reaches training until it passes here.

Two jobs per track, both CPU-only:

  1. QUALITY VERDICT  — PASS / REVIEW / REJECT with reasons. Reject checks:
       guitar_rms, occupancy, clipping, silence, duplicates, leakage,
       label_consistency, dynamic_range, spectral_balance.

  2. REGIME METADATA  — the tags that let training target weaknesses (Phase 5)
       and the gate do genre/regime breakdowns (Phase 4):
       genre, guitar_type, gain, pickup, tempo, key, vocal_density,
       guitar_occupancy, masking_score, difficulty, quality_score,
       recording_type, synthetic_real, license.

Audio-derived fields are computed here; dataset-level fields (genre, pickup,
recording_type, synthetic_real) are carried from the caller / dataset metadata;
license is pulled from the training-data provenance registry (single source of
truth). Fields that cannot be inferred from a mix (true amp gain, pickup
position) are marked "unknown" unless the dataset supplies them — `gain` and
`guitar_type` get audio *proxies* (distortion/timbre), flagged as estimated.

Output feeds `training_data.build_manifest` (which enforces the commercial
license gate) — only PASS tracks from registered, license-clean datasets ship.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import soundfile as sf
    import librosa
    _HAS_AUDIO = True
except Exception:  # pragma: no cover - environment guard
    _HAS_AUDIO = False

try:
    from . import training_data as _td
except Exception:  # pragma: no cover
    _td = None


# --------------------------------------------------------------------------
# Thresholds — every reject rule in one place, tunable and inspectable.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AuditThresholds:
    sr: int = 44100
    frame_s: float = 0.20              # analysis frame for occupancy/silence
    active_rms: float = 0.01           # a frame counts as "guitar active" above this
    silence_rms: float = 0.003         # below this the frame is silent
    # reject rules
    min_guitar_rms: float = 0.005      # whole-track; the run-1 killer (near-silent target)
    min_occupancy: float = 0.15        # <15% guitar-active -> not a useful guitar track
    max_clipping: float = 0.005        # >0.5% samples at full-scale -> clipped master
    max_silence_frac: float = 0.85     # mostly silent
    min_dynamic_range_db: float = 6.0  # crest factor; below = dead/over-limited
    max_leakage: float = 0.35          # cross-stem energy correlation (vocal/other bleed)
    min_rolloff_hz: float = 2500.0     # 85%-energy rolloff below this = bandlimited (mp3/lowpass).
                                       # NB: guitars roll off ~6kHz, so a >8kHz-energy test
                                       # wrongly flags them; rolloff is the correct probe.
    dup_cosine: float = 0.995          # fingerprint cosine >= this => duplicate


REJECT, REVIEW, PASS = "REJECT", "REVIEW", "PASS"


@dataclass
class TrackAudit:
    track_id: str
    dataset: str
    guitar_path: str
    verdict: str = REVIEW
    reasons: list = field(default_factory=list)      # why REJECT/REVIEW

    # ---- quality metrics (audio-derived) ----
    guitar_rms: float = 0.0
    occupancy: float = 0.0             # == guitar_occupancy (regime tag too)
    clipping_frac: float = 0.0
    silence_frac: float = 0.0
    dynamic_range_db: float = 0.0
    leakage: Optional[float] = None    # None if no other stems to compare
    hf_ratio: float = 0.0              # energy >8kHz / total (info only; guitars ~0 here)
    lf_ratio: float = 0.0              # energy <250Hz / total
    rolloff_hz: float = 0.0            # 85%-energy rolloff freq (bandlimit probe)
    label_ok: bool = True              # spectral sanity: is it plausibly guitar
    is_duplicate: bool = False
    dup_of: Optional[str] = None

    # ---- regime metadata (the 14 requested tags) ----
    genre: str = "unknown"
    guitar_type: str = "unknown"       # acoustic|electric_clean|distorted (proxy if estimated)
    gain: Optional[float] = None       # 0..1 distortion proxy (estimated) unless dataset-given
    pickup: str = "unknown"            # not inferable from a mix; dataset-only
    tempo: Optional[float] = None
    key: str = "unknown"
    vocal_density: Optional[float] = None   # frac frames vocals active (needs vocal stem)
    guitar_occupancy: float = 0.0
    masking_score: Optional[float] = None   # vocal-over-guitar overlap; needs vocal stem
    difficulty: float = 0.0            # 0..1 composite (higher = harder regime)
    quality_score: float = 0.0         # 0..1 composite (higher = cleaner)
    recording_type: str = "unknown"    # DI|mic|multitrack|synthetic (dataset-given)
    synthetic_real: str = "unknown"    # synthetic|real (dataset-given)
    license: str = "unregistered"
    estimated_fields: list = field(default_factory=list)  # which tags are audio-guessed

    fingerprint: Optional[list] = None  # compact vector for dedup (not serialized to manifest)


# --------------------------------------------------------------------------
# DSP helpers
# --------------------------------------------------------------------------
def _load_mono(path: Path, sr: int) -> np.ndarray:
    y, native = sf.read(str(path), always_2d=True)
    y = y.mean(axis=1)  # downmix
    if native != sr:
        y = librosa.resample(y.astype(np.float32), orig_sr=native, target_sr=sr)
    return y.astype(np.float32)


def _frame_rms(y: np.ndarray, sr: int, frame_s: float) -> np.ndarray:
    n = max(1, int(frame_s * sr))
    trim = len(y) - (len(y) % n)
    if trim <= 0:
        return np.array([np.sqrt(np.mean(y ** 2) + 1e-12)])
    frames = y[:trim].reshape(-1, n)
    return np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)


def _crest_db(y: np.ndarray) -> float:
    peak = float(np.max(np.abs(y)) + 1e-9)
    rms = float(np.sqrt(np.mean(y ** 2)) + 1e-9)
    return 20.0 * np.log10(peak / rms)


def _spectral_bands(y: np.ndarray, sr: int) -> tuple[float, float, float]:
    """Return (lf_ratio, mf_ratio, hf_ratio) of energy < 250Hz / 250-8k / > 8kHz."""
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2 if len(y) < (1 << 20) else \
        np.abs(librosa.stft(y, n_fft=2048)).mean(axis=1) ** 2
    freqs = np.fft.rfftfreq(len(y), 1 / sr) if len(y) < (1 << 20) else \
        librosa.fft_frequencies(sr=sr, n_fft=2048)
    total = float(spec.sum() + 1e-12)
    lf = float(spec[freqs < 250].sum()) / total
    hf = float(spec[freqs > 8000].sum()) / total
    mf = max(0.0, 1.0 - lf - hf)
    return lf, mf, hf


def _rolloff_hz(y: np.ndarray, sr: int, pct: float = 0.85) -> float:
    """85%-energy spectral rolloff, averaged over time. Low value => bandlimited
    (mp3/telephone/lowpass). Correct bandlimit probe for guitar (which has ~no
    energy >8kHz yet is full-band up to its natural rolloff)."""
    ro = librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=pct)
    return float(np.median(ro))


def _distortion_proxy(y: np.ndarray, sr: int) -> float:
    """0..1 estimate of guitar distortion/gain from spectral flatness + crest.
    Distorted guitar: high harmonic density (flat-ish spectrum) + low crest (compressed)."""
    flat = float(np.mean(librosa.feature.spectral_flatness(y=y)))     # 0..1, higher=noisier/distorted
    crest = _crest_db(y)
    crest_term = np.clip((14.0 - crest) / 14.0, 0.0, 1.0)             # low crest -> distorted
    return float(np.clip(0.55 * min(1.0, flat * 6.0) + 0.45 * crest_term, 0.0, 1.0))


def _fingerprint(y: np.ndarray, sr: int, dim: int = 64) -> np.ndarray:
    """Compact, level-invariant spectral fingerprint for near-duplicate detection."""
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=dim)
    v = np.log1p(mel).mean(axis=1)
    n = np.linalg.norm(v) + 1e-9
    return (v / n).astype(np.float32)


def _active_mask(rms: np.ndarray, thr: float) -> np.ndarray:
    return rms > thr


# --------------------------------------------------------------------------
# Per-track audit
# --------------------------------------------------------------------------
def audit_track(guitar_path: str | Path, *, dataset: str, track_id: Optional[str] = None,
                vocal_path: str | Path | None = None,
                other_paths: Optional[list] = None,
                mix_path: str | Path | None = None,
                genre: str = "unknown", pickup: str = "unknown",
                recording_type: str = "unknown", synthetic_real: str = "unknown",
                guitar_type: Optional[str] = None, gain: Optional[float] = None,
                expect_guitar: bool = True,
                th: AuditThresholds = AuditThresholds()) -> TrackAudit:
    """Audit one track from its guitar stem (+ optional vocal/other/mix stems).

    Dataset-level fields (genre/pickup/recording_type/synthetic_real/guitar_type/gain)
    are carried through when the dataset supplies them; audio proxies fill the rest.
    """
    if not _HAS_AUDIO:
        raise RuntimeError("dataset_auditor requires soundfile + librosa")
    gp = Path(guitar_path)
    tid = track_id or gp.parent.name
    a = TrackAudit(track_id=tid, dataset=dataset, guitar_path=str(gp),
                   genre=genre, pickup=pickup, recording_type=recording_type,
                   synthetic_real=synthetic_real)

    # license from the single-source-of-truth registry
    if _td is not None:
        try:
            a.license = _td.dataset_facts(dataset)["license"]
        except Exception:
            a.license = "unregistered"

    g = _load_mono(gp, th.sr)
    grms = _frame_rms(g, th.sr, th.frame_s)

    # ---- quality metrics ----
    a.guitar_rms = float(np.sqrt(np.mean(g ** 2) + 1e-12))
    active = _active_mask(grms, th.active_rms)
    a.occupancy = a.guitar_occupancy = float(active.mean())
    a.clipping_frac = float(np.mean(np.abs(g) >= 0.999))
    a.silence_frac = float(np.mean(grms < th.silence_rms))
    a.dynamic_range_db = _crest_db(g[np.abs(g) > 1e-4]) if np.any(np.abs(g) > 1e-4) else 0.0
    a.lf_ratio, _mf, a.hf_ratio = _spectral_bands(g, th.sr)
    a.rolloff_hz = round(_rolloff_hz(g, th.sr), 0)
    a.fingerprint = _fingerprint(g, th.sr).tolist()

    # label sanity: a real guitar stem has broadband harmonic content — not
    # near-silence, not pure sub-bass (bass mislabel), not near-DC (broken/lowpassed).
    # For BACKING assets (expect_guitar=False: drums/bass/vocals/keys), the
    # guitar-shaped-spectrum check does not apply — only the generic quality gates do.
    a.label_ok = (not expect_guitar) or (
        a.guitar_rms >= th.min_guitar_rms and a.lf_ratio < 0.85
        and a.rolloff_hz >= 1200.0)

    # ---- regime metadata (audio-derived) ----
    try:
        tempo = librosa.beat.tempo(y=(mix := _load_mono(Path(mix_path), th.sr)) if mix_path else g,
                                   sr=th.sr)
        a.tempo = round(float(np.atleast_1d(tempo)[0]), 1)
    except Exception:
        a.tempo = None
    try:
        a.key = _estimate_key(g, th.sr)
    except Exception:
        a.key = "unknown"

    # gain / guitar_type proxies (flag as estimated unless dataset gave them)
    if gain is not None:
        a.gain = float(gain)
    else:
        a.gain = round(_distortion_proxy(g, th.sr), 3)
        a.estimated_fields.append("gain")
    if guitar_type is not None:
        a.guitar_type = guitar_type
    else:
        a.guitar_type = _guitar_type_proxy(g, th.sr, a.gain)
        a.estimated_fields.append("guitar_type")

    # ---- cross-stem: leakage, vocal density, masking ----
    others = [Path(p) for p in (other_paths or [])]
    if vocal_path:
        others.append(Path(vocal_path))
    if others:
        a.leakage = _leakage(g, grms, active, others, th)
    if vocal_path:
        v = _load_mono(Path(vocal_path), th.sr)
        vrms = _frame_rms(v, th.sr, th.frame_s)
        vac = _active_mask(vrms, th.active_rms)
        a.vocal_density = float(vac.mean())
        a.masking_score = _masking(g, v, grms, vrms, active, th)

    # ---- composite scores ----
    a.quality_score = _quality_score(a, th)
    a.difficulty = _difficulty(a)

    # ---- verdict ----
    _verdict(a, th)
    return a


def _estimate_key(y: np.ndarray, sr: int) -> str:
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    # Krumhansl-Schmuckler major/minor profiles
    maj = np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
    minp = np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
    best, mode, score = 0, "maj", -1e9
    for i in range(12):
        for prof, m in ((maj, "maj"), (minp, "min")):
            s = float(np.corrcoef(np.roll(prof, i), chroma)[0, 1])
            if s > score:
                best, mode, score = i, m, s
    return f"{names[best]}{'m' if mode == 'min' else ''}"


def _guitar_type_proxy(y: np.ndarray, sr: int, gain: float) -> str:
    """Honest coarse proxy. Distortion is reliably detectable from a stem;
    acoustic-vs-clean-electric is NOT reliable without a trained classifier, so
    we only split distorted|clean here. Acoustic/electric needs a dataset label."""
    return "distorted" if gain >= 0.5 else "clean"


def _leakage(g: np.ndarray, grms: np.ndarray, gactive: np.ndarray,
             other_paths: list, th: AuditThresholds) -> float:
    """Max normalized cross-correlation between the guitar stem and any other stem.
    High => the guitar target contains bleed from vocals/other (bad ground truth)."""
    worst = 0.0
    for p in other_paths:
        try:
            o = _load_mono(p, th.sr)
        except Exception:
            continue
        n = min(len(g), len(o))
        if n < th.sr:
            continue
        gg, oo = g[:n], o[:n]
        num = float(np.dot(gg, oo))
        den = float(np.linalg.norm(gg) * np.linalg.norm(oo) + 1e-9)
        worst = max(worst, abs(num / den))
    return round(worst, 3)


def _masking(g, v, grms, vrms, gactive, th: AuditThresholds) -> float:
    """0..1: at guitar-active frames, how much louder vocals are than guitar.
    High = guitar buried under vocal (the exact hard regime stock/B1 failed)."""
    n = min(len(grms), len(vrms))
    grms, vrms, gactive = grms[:n], vrms[:n], gactive[:n]
    if gactive.sum() < 1:
        return 0.0
    ratio = vrms[gactive] / (grms[gactive] + 1e-6)
    # fraction of guitar-active frames where vocal energy exceeds guitar energy
    return round(float(np.mean(ratio > 1.0)), 3)


def _quality_score(a: TrackAudit, th: AuditThresholds) -> float:
    s = 1.0
    s -= min(0.5, a.clipping_frac * 40.0)                 # clipping hurts a lot
    s -= 0.2 if a.rolloff_hz < th.min_rolloff_hz else 0.0  # bandlimited
    s -= min(0.3, max(0.0, (th.min_dynamic_range_db - a.dynamic_range_db)) / th.min_dynamic_range_db * 0.3)
    if a.leakage is not None:
        s -= min(0.4, max(0.0, a.leakage - th.max_leakage) * 1.5)
    s -= 0.4 if not a.label_ok else 0.0
    return round(float(np.clip(s, 0.0, 1.0)), 3)


def _difficulty(a: TrackAudit) -> float:
    """0..1 regime difficulty: masked + sparse + (mildly) distorted are harder."""
    d = 0.0
    if a.masking_score is not None:
        d += 0.5 * a.masking_score
    d += 0.3 * (1.0 - a.occupancy)                         # sparse guitar is harder to catch
    d += 0.2 * (a.gain or 0.0)                             # distortion adds difficulty
    return round(float(np.clip(d, 0.0, 1.0)), 3)


def _verdict(a: TrackAudit, th: AuditThresholds) -> None:
    r = a.reasons
    if a.guitar_rms < th.min_guitar_rms:
        r.append(f"near-silent guitar (rms {a.guitar_rms:.4f} < {th.min_guitar_rms})")
    if a.occupancy < th.min_occupancy:
        r.append(f"low occupancy ({a.occupancy:.2f} < {th.min_occupancy})")
    if a.clipping_frac > th.max_clipping:
        r.append(f"clipping ({a.clipping_frac:.3f} > {th.max_clipping})")
    if a.silence_frac > th.max_silence_frac:
        r.append(f"mostly silent ({a.silence_frac:.2f})")
    if a.dynamic_range_db < th.min_dynamic_range_db:
        r.append(f"low dynamic range ({a.dynamic_range_db:.1f} dB)")
    if a.leakage is not None and a.leakage > th.max_leakage:
        r.append(f"stem leakage ({a.leakage:.2f} > {th.max_leakage})")
    if not a.label_ok:
        r.append("label sanity failed (not guitar-like spectrum)")
    if a.rolloff_hz < th.min_rolloff_hz:
        r.append(f"bandlimited (rolloff {a.rolloff_hz:.0f}Hz < {th.min_rolloff_hz:.0f})")
    if a.is_duplicate:
        r.append(f"duplicate of {a.dup_of}")

    hard = any(k in " ".join(r) for k in
               ("near-silent", "low occupancy", "clipping", "mostly silent",
                "leakage", "label sanity", "duplicate"))
    if hard:
        a.verdict = REJECT
    elif r:
        a.verdict = REVIEW
    else:
        a.verdict = PASS


# --------------------------------------------------------------------------
# Dataset-level audit (adds cross-track dedup + aggregate report)
# --------------------------------------------------------------------------
def audit_dataset(audits: list, th: AuditThresholds = AuditThresholds()) -> dict:
    """Take per-track audits, run near-duplicate detection across them, aggregate.
    Returns a report dict; mutates each audit's is_duplicate/verdict."""
    # dedup by fingerprint cosine
    fps = [(a, np.asarray(a.fingerprint)) for a in audits if a.fingerprint]
    for i in range(len(fps)):
        ai, vi = fps[i]
        if ai.is_duplicate:
            continue
        for j in range(i + 1, len(fps)):
            aj, vj = fps[j]
            if aj.is_duplicate:
                continue
            if float(np.dot(vi, vj)) >= th.dup_cosine:
                aj.is_duplicate = True
                aj.dup_of = ai.track_id
                if "duplicate" not in " ".join(aj.reasons):
                    aj.reasons.append(f"duplicate of {ai.track_id}")
                    aj.verdict = REJECT

    passed = [a for a in audits if a.verdict == PASS]
    report = {
        "n_total": len(audits),
        "n_pass": sum(a.verdict == PASS for a in audits),
        "n_review": sum(a.verdict == REVIEW for a in audits),
        "n_reject": sum(a.verdict == REJECT for a in audits),
        "reject_reasons": _count_reasons(audits),
        "regime_distribution": {
            "guitar_type": _hist(passed, "guitar_type"),
            "genre": _hist(passed, "genre"),
            "synthetic_real": _hist(passed, "synthetic_real"),
            "difficulty_buckets": _difficulty_hist(passed),
        },
        "mean_quality_pass": round(float(np.mean([a.quality_score for a in passed])) if passed else 0.0, 3),
    }
    return report


def _count_reasons(audits: list) -> dict:
    from collections import Counter
    c = Counter()
    for a in audits:
        for r in a.reasons:
            c[r.split(" (")[0]] += 1
    return dict(c.most_common())


def _hist(audits: list, field_name: str) -> dict:
    from collections import Counter
    return dict(Counter(getattr(a, field_name) for a in audits))


def _difficulty_hist(audits: list) -> dict:
    b = {"easy(<.33)": 0, "med(.33-.66)": 0, "hard(>.66)": 0}
    for a in audits:
        b["easy(<.33)" if a.difficulty < .33 else "med(.33-.66)" if a.difficulty < .66 else "hard(>.66)"] += 1
    return b


def audit_to_dict(a: TrackAudit) -> dict:
    d = asdict(a)
    d.pop("fingerprint", None)   # keep manifests small; fingerprints are transient
    return d
