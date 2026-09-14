"""Stem separation using Demucs.

Separates a full mix into stems (drums, bass, other, vocals, guitar).
Uses the htdemucs model for high-quality separation.

Usage:
    from tone_forge.stem_separator import separate_guitar

    guitar_path = separate_guitar("/path/to/mix.mp3")
    # Returns path to the extracted guitar stem WAV file
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Lazy imports to avoid slow startup when stem separation isn't needed
_demucs_available: Optional[bool] = None


def _get_torch_device():
    """Get the best available torch device (CUDA > MPS > CPU)."""
    import torch

    if torch.cuda.is_available():
        logger.info("Using CUDA GPU for stem separation")
        return torch.device("cuda")
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        logger.info("Using Apple MPS GPU for stem separation")
        return torch.device("mps")
    else:
        logger.info("Using CPU for stem separation")
        return torch.device("cpu")


def _check_demucs() -> bool:
    """Check if Demucs is available."""
    global _demucs_available
    if _demucs_available is None:
        try:
            import torch
            import demucs.pretrained
            import demucs.apply
            _demucs_available = True
        except ImportError:
            _demucs_available = False
    return _demucs_available


def separate_guitar(
    audio_path: str | Path,
    output_dir: str | Path | None = None,
    model_name: str = "htdemucs",
) -> Path:
    """Separate guitar stem from a full mix.

    Args:
        audio_path: Path to the input audio file (MP3, WAV, etc.)
        output_dir: Directory to write the guitar stem. If None, uses a temp dir.
        model_name: Demucs model to use. Options:
            - "htdemucs": High-quality 4-stem model (drums, bass, other, vocals)
            - "htdemucs_ft": Fine-tuned version
            - "mdx_extra": Alternative model

    Returns:
        Path to the extracted guitar stem WAV file.

    Raises:
        ImportError: If Demucs is not installed.
        RuntimeError: If separation fails.
    """
    if not _check_demucs():
        raise ImportError(
            "Demucs is not installed. Install with: pip install demucs torch torchaudio"
        )

    import torch
    import torchaudio
    import soundfile as sf
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Set up output directory
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="toneforge_stems_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Separating stems from {audio_path.name} using {model_name}...")

    try:
        # Load the model
        model = get_model(model_name)
        model.eval()

        # Use best available device (CUDA > MPS > CPU)
        device = _get_torch_device()
        model.to(device)

        # Load audio using soundfile (more reliable than torchaudio.load)
        audio_np, sr = sf.read(str(audio_path))
        # soundfile returns (samples, channels), we need (channels, samples)
        if audio_np.ndim == 1:
            audio_np = audio_np[np.newaxis, :]
        else:
            audio_np = audio_np.T
        wav = torch.from_numpy(audio_np).float()

        # Resample if needed (demucs expects 44.1kHz)
        if sr != model.samplerate:
            wav = torchaudio.functional.resample(wav, sr, model.samplerate)
            sr = model.samplerate

        # Ensure stereo
        if wav.shape[0] == 1:
            wav = wav.repeat(2, 1)
        elif wav.shape[0] > 2:
            wav = wav[:2]

        # Add batch dimension: (channels, samples) -> (batch, channels, samples)
        wav = wav.unsqueeze(0).to(device)

        # Apply the model
        with torch.no_grad():
            # shifts=0 disables Demucs's random temporal-shift averaging
            # so stem separation is bit-exact across runs. The default
            # shifts=1 draws a random shift per chunk from PyTorch RNG
            # (no seed pinned), which made every downstream signal
            # drift between pipeline runs even on the same source
            # audio: piano stem RMS-differed by 57%, other by 39%, bass
            # by 6%, etc., and that drift propagated into chord_density
            # (bass-pitch biasing) and per-stem MIDI features, flipping
            # 20 of 22 guidance-mode classifications on Sex On Fire.
            # The quality cost of shifts=0 is imperceptible for
            # downstream analysis (we don't ship stems as final mix).
            sources = apply_model(model, wav, device=device, shifts=0, overlap=0.1)

        # sources shape: (batch, num_sources, channels, samples)
        # Get source names from model
        source_names = model.sources  # e.g., ['drums', 'bass', 'other', 'vocals']

        # Find guitar-related stem
        # htdemucs has: drums, bass, other, vocals
        # 'other' contains guitar + synths + other instruments
        if "guitar" in source_names:
            stem_idx = source_names.index("guitar")
            stem_name = "guitar"
        elif "other" in source_names:
            stem_idx = source_names.index("other")
            stem_name = "other"
            logger.info("Using 'other' stem (contains guitar + other instruments)")
        else:
            raise RuntimeError(f"No guitar/other stem found. Available: {source_names}")

        # Extract the stem: (batch, channels, samples) -> (channels, samples)
        guitar_stem = sources[0, stem_idx].cpu()

        # Save the guitar stem using soundfile (torchaudio.save requires torchcodec)
        output_path = output_dir / f"{audio_path.stem}_{stem_name}.wav"
        # Convert to (samples, channels) for soundfile
        audio_out = guitar_stem.numpy().T
        sf.write(str(output_path), audio_out, sr)

        logger.info(f"Guitar stem saved to {output_path}")
        return output_path

    except Exception as e:
        raise RuntimeError(f"Stem separation failed: {e}") from e


def separate_all_stems(
    audio_path: str | Path,
    output_dir: str | Path | None = None,
    model_name: str = "htdemucs",
) -> dict[str, Path]:
    """Separate all stems from a full mix.

    Args:
        audio_path: Path to the input audio file.
        output_dir: Directory to write stems. If None, uses a temp dir.
        model_name: Demucs model to use.

    Returns:
        Dictionary mapping stem names to their file paths.
    """
    if not _check_demucs():
        raise ImportError(
            "Demucs is not installed. Install with: pip install demucs torch torchaudio"
        )

    import torch
    import torchaudio
    import soundfile as sf
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="toneforge_stems_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Separating all stems from {audio_path.name}...")

    # Load model
    model = get_model(model_name)
    model.eval()
    device = _get_torch_device()
    model.to(device)

    # Load and prepare audio using soundfile
    audio_np, sr = sf.read(str(audio_path))
    if audio_np.ndim == 1:
        audio_np = audio_np[np.newaxis, :]
    else:
        audio_np = audio_np.T
    wav = torch.from_numpy(audio_np).float()
    if sr != model.samplerate:
        wav = torchaudio.functional.resample(wav, sr, model.samplerate)
        sr = model.samplerate
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    elif wav.shape[0] > 2:
        wav = wav[:2]
    wav = wav.unsqueeze(0).to(device)

    # Separate
    with torch.no_grad():
        # shifts=0: see comment at separate_guitar's apply_model call
        # for full rationale. Bit-exact stems across runs.
        sources = apply_model(model, wav, device=device, shifts=0, overlap=0.1)

    # Save each stem using soundfile
    stem_paths = {}
    for idx, stem_name in enumerate(model.sources):
        stem_audio = sources[0, idx].cpu()
        output_path = output_dir / f"{audio_path.stem}_{stem_name}.wav"
        audio_out = stem_audio.numpy().T
        sf.write(str(output_path), audio_out, sr)
        stem_paths[stem_name] = output_path
        logger.info(f"  {stem_name} -> {output_path.name}")

    return stem_paths


def separate_bass(
    audio_path: str | Path,
    output_dir: str | Path | None = None,
    model_name: str = "htdemucs",
) -> Path:
    """Separate bass stem from a full mix.

    Args:
        audio_path: Path to the input audio file.
        output_dir: Directory to write the bass stem. If None, uses a temp dir.
        model_name: Demucs model to use.

    Returns:
        Path to the extracted bass stem WAV file.
    """
    if not _check_demucs():
        raise ImportError(
            "Demucs is not installed. Install with: pip install demucs torch torchaudio"
        )

    import torch
    import torchaudio
    import soundfile as sf
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="toneforge_stems_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Separating bass from {audio_path.name} using {model_name}...")

    try:
        model = get_model(model_name)
        model.eval()
        device = _get_torch_device()
        model.to(device)

        audio_np, sr = sf.read(str(audio_path))
        if audio_np.ndim == 1:
            audio_np = audio_np[np.newaxis, :]
        else:
            audio_np = audio_np.T
        wav = torch.from_numpy(audio_np).float()

        if sr != model.samplerate:
            wav = torchaudio.functional.resample(wav, sr, model.samplerate)
            sr = model.samplerate

        if wav.shape[0] == 1:
            wav = wav.repeat(2, 1)
        elif wav.shape[0] > 2:
            wav = wav[:2]

        wav = wav.unsqueeze(0).to(device)

        with torch.no_grad():
            # shifts=0 disables Demucs's random temporal-shift averaging
            # so stem separation is bit-exact across runs. The default
            # shifts=1 draws a random shift per chunk from PyTorch RNG
            # (no seed pinned), which made every downstream signal
            # drift between pipeline runs even on the same source
            # audio: piano stem RMS-differed by 57%, other by 39%, bass
            # by 6%, etc., and that drift propagated into chord_density
            # (bass-pitch biasing) and per-stem MIDI features, flipping
            # 20 of 22 guidance-mode classifications on Sex On Fire.
            # The quality cost of shifts=0 is imperceptible for
            # downstream analysis (we don't ship stems as final mix).
            sources = apply_model(model, wav, device=device, shifts=0, overlap=0.1)

        source_names = model.sources
        if "bass" not in source_names:
            raise RuntimeError(f"No bass stem found. Available: {source_names}")

        stem_idx = source_names.index("bass")
        bass_stem = sources[0, stem_idx].cpu()

        output_path = output_dir / f"{audio_path.stem}_bass.wav"
        audio_out = bass_stem.numpy().T
        sf.write(str(output_path), audio_out, sr)

        logger.info(f"Bass stem saved to {output_path}")
        return output_path

    except Exception as e:
        raise RuntimeError(f"Bass stem separation failed: {e}") from e


def separate_drums(
    audio_path: str | Path,
    output_dir: str | Path | None = None,
    model_name: str = "htdemucs",
) -> Path:
    """Separate drums stem from a full mix.

    Args:
        audio_path: Path to the input audio file.
        output_dir: Directory to write the drums stem. If None, uses a temp dir.
        model_name: Demucs model to use.

    Returns:
        Path to the extracted drums stem WAV file.
    """
    if not _check_demucs():
        raise ImportError(
            "Demucs is not installed. Install with: pip install demucs torch torchaudio"
        )

    import torch
    import torchaudio
    import soundfile as sf
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="toneforge_stems_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Separating drums from {audio_path.name} using {model_name}...")

    try:
        model = get_model(model_name)
        model.eval()
        device = _get_torch_device()
        model.to(device)

        audio_np, sr = sf.read(str(audio_path))
        if audio_np.ndim == 1:
            audio_np = audio_np[np.newaxis, :]
        else:
            audio_np = audio_np.T
        wav = torch.from_numpy(audio_np).float()

        if sr != model.samplerate:
            wav = torchaudio.functional.resample(wav, sr, model.samplerate)
            sr = model.samplerate

        if wav.shape[0] == 1:
            wav = wav.repeat(2, 1)
        elif wav.shape[0] > 2:
            wav = wav[:2]

        wav = wav.unsqueeze(0).to(device)

        with torch.no_grad():
            # shifts=0 disables Demucs's random temporal-shift averaging
            # so stem separation is bit-exact across runs. The default
            # shifts=1 draws a random shift per chunk from PyTorch RNG
            # (no seed pinned), which made every downstream signal
            # drift between pipeline runs even on the same source
            # audio: piano stem RMS-differed by 57%, other by 39%, bass
            # by 6%, etc., and that drift propagated into chord_density
            # (bass-pitch biasing) and per-stem MIDI features, flipping
            # 20 of 22 guidance-mode classifications on Sex On Fire.
            # The quality cost of shifts=0 is imperceptible for
            # downstream analysis (we don't ship stems as final mix).
            sources = apply_model(model, wav, device=device, shifts=0, overlap=0.1)

        source_names = model.sources
        if "drums" not in source_names:
            raise RuntimeError(f"No drums stem found. Available: {source_names}")

        stem_idx = source_names.index("drums")
        drums_stem = sources[0, stem_idx].cpu()

        output_path = output_dir / f"{audio_path.stem}_drums.wav"
        audio_out = drums_stem.numpy().T
        sf.write(str(output_path), audio_out, sr)

        logger.info(f"Drums stem saved to {output_path}")
        return output_path

    except Exception as e:
        raise RuntimeError(f"Drums stem separation failed: {e}") from e


def _max_lag_corr(L: np.ndarray, R: np.ndarray, sr: int,
                  max_lag_s: float = 0.05, target_sr: int = 8000) -> float:
    """Max normalized L/R cross-correlation over ±max_lag_s.

    Catches delay-based stereo width (Haas/ADT/chorus): one part whose
    zero-lag correlation is low but which correlates near unity at the
    delay lag. Stride-decimated to ~target_sr — this is a bound for a
    skip-gate, not a measurement.
    """
    step = max(1, int(sr // target_sr))
    a = np.asarray(L[::step], dtype=np.float64)
    b = np.asarray(R[::step], dtype=np.float64)
    n = min(len(a), len(b))
    if n < 32:
        return 0.0
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    ea = float(np.sqrt((a**2).sum()))
    eb = float(np.sqrt((b**2).sum()))
    if ea <= 0 or eb <= 0:
        return 0.0
    max_lag = max(1, int(max_lag_s * sr / step))
    m = 1
    while m < 2 * n:
        m <<= 1
    fa = np.fft.rfft(a, m)
    fb = np.fft.rfft(b, m)
    cc = np.fft.irfft(fa * np.conj(fb), m)
    seg = np.concatenate([cc[: max_lag + 1], cc[m - max_lag:]])
    return float(np.max(np.abs(seg)) / (ea * eb))


def _mean_mag_spectrum(x: np.ndarray, sr: int, nfft: int = 4096):
    """Mean log-magnitude spectrum (hann frames, half overlap, ≤64 frames).
    Feeds the post-split mid-vs-side duplicate-content check. None when the
    signal is too short (caller skips the check)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < nfft // 2:
        return None
    n = min(nfft, len(x))
    w = np.hanning(n)
    acc = None
    cnt = 0
    for h in range(0, max(1, len(x) - n + 1), max(1, n // 2)):
        mag = np.abs(np.fft.rfft(x[h:h + n] * w))
        acc = mag if acc is None else acc + mag
        cnt += 1
        if cnt >= 64:
            break
    if not cnt:
        return None
    return np.log1p(acc / cnt)


def split_stem_by_pan(
    stem_path: str | Path,
    output_dir: str | Path | None = None,
    side_threshold: float = 0.10,
    correlation_threshold: float = 0.70,
) -> dict[str, Path]:
    """Decompose a stereo stem into center + sides using mid/side encoding.

    Why this exists:
        Demucs' ``other`` stem is a single bucket containing every
        non-drums-bass-vocals instrument — typically a mix of rhythm
        guitars (panned wide), lead guitar (centre), synths, etc. For the
        Jam UX we want the rhythm guitarist to be able to mute the doubled
        rhythm parts while keeping the lead, and vice versa. The cheapest
        win is a mid/side split: doubled rhythm parts panned ±100% live in
        the side signal; centre content (lead, vox doubles bleeding into
        ``other``) lives in mid.

    Strategy:
        Compute ``mid = 0.5*(L+R)`` and ``side = 0.5*(L-R)``. If the
        stem's side energy is below ``side_threshold`` (fraction of total),
        the stem is effectively mono and we don't split. Otherwise emit
        two mono WAVs alongside the original.

    Output format (critical):
        Both files are written as STEREO so that, when played back
        and mixed at unity gain, ``center + sides == original``:
            center -> [mid, mid]        (stereo-mono)
            sides  -> [side, -side]     (phase-inverted on right)
        Sum on left channel:  mid + side  = (L+R)/2 + (L-R)/2 = L
        Sum on right channel: mid + -side = (L+R)/2 - (L-R)/2 = R
        Writing ``side`` as a mono channel would make Web Audio
        broadcast it to both speakers, collapsing the mix to L-only.

    Caveats:
        - Mid/side cannot recover content that *both* L and R contain
          (correlated stereo). For that you'd need a time-frequency
          masking approach (pan-angle classification per TF bin). That's
          a follow-up.

    Args:
        stem_path: Path to the source stereo WAV (typically Demucs
            ``other`` output).
        output_dir: Where to write the split files. Defaults to the
            stem_path's parent directory.
        side_threshold: Minimum side-energy ratio to perform the split.
            Songs with mostly-mono ``other`` content fall back to a
            single output keyed as ``center``.

    Returns:
        Dict mapping pan-label to file path. Either ``{"center": <orig>}``
        when no split is meaningful, or ``{"center": Path, "sides": Path}``.
    """
    import soundfile as sf

    stem_path = Path(stem_path)
    if not stem_path.exists():
        raise FileNotFoundError(f"Stem not found: {stem_path}")

    if output_dir is None:
        output_dir = stem_path.parent
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    audio, sr = sf.read(str(stem_path))
    # soundfile returns (samples,) for mono or (samples, channels) for multi.
    if audio.ndim == 1 or (audio.ndim == 2 and audio.shape[1] == 1):
        logger.info(f"Stem {stem_path.name} is mono — pan split skipped.")
        return {"center": stem_path}
    if audio.shape[1] < 2:
        return {"center": stem_path}

    L = audio[:, 0].astype(np.float32)
    R = audio[:, 1].astype(np.float32)
    mid = 0.5 * (L + R)
    side = 0.5 * (L - R)

    mid_energy = float(np.mean(mid * mid))
    side_energy = float(np.mean(side * side))
    total_energy = mid_energy + side_energy + 1e-12
    side_ratio = side_energy / total_energy

    # L/R correlation is the real discriminator between "one source with
    # stereo widening" (high correlation) and "two independent
    # double-tracked sources" (low correlation). Side energy alone fires
    # on any stereo guitar recording — including a single source widened
    # with a Haas/chorus effect — which we don't want to split.
    #
    # The correlation is the MAX over ±50 ms lags, not zero-lag only:
    # delay-based width (Haas doubling, ADT, chorus, short reverb on a mono
    # source) decorrelates the zero-lag coefficient far below any threshold
    # while the content is ONE part — the split then fired and mid=(L+R)/2
    # comb-filtered the take into a mono collapse (both children the same
    # notes at worse quality; caught by ear on real material). At the delay
    # lag the channels correlate near unity, so the lag scan catches exactly
    # the case the zero-lag gate was structurally blind to. Genuine
    # double-tracks stay low at every lag.
    L_std = float(np.std(L))
    R_std = float(np.std(R))
    if L_std < 1e-6 or R_std < 1e-6:
        correlation = 1.0  # one channel silent => effectively mono
        zero_lag = 1.0
    else:
        zero_lag = float(np.corrcoef(L, R)[0, 1])
        correlation = max(abs(zero_lag), _max_lag_corr(L, R, sr))

    logger.info(
        f"Pan analysis for {stem_path.name}: "
        f"side_ratio={side_ratio:.3f}, LR_corr={correlation:.3f} "
        f"(zero_lag={zero_lag:.3f})"
    )

    if side_ratio < side_threshold:
        logger.info(
            f"Side ratio {side_ratio:.3f} below threshold {side_threshold} — "
            "treating as single source."
        )
        return {"center": stem_path}

    if correlation > correlation_threshold:
        # Highly correlated L/R = one source with stereo widening, not
        # two independent parts. Skip the split — Jam doesn't care about
        # stereo image for monitoring.
        logger.info(
            f"LR correlation {correlation:.3f} above threshold "
            f"{correlation_threshold} — single source with stereo width, "
            "skipping pan-split."
        )
        return {"center": stem_path}

    # Post-split sanity: are there actually TWO parts here? Compare the mean
    # magnitude spectra of L and R. One part with time-based width (chorus,
    # varying delay, reverb wash — cases a fixed-lag scan can dilute) has
    # near-identical L/R spectra (delays preserve magnitude), while genuine
    # double-tracked parts differ spectrally. NOTE deliberately L/R, NOT
    # mid/side: mid and side of ANY hard-panned pair share magnitude spectra
    # (|A±B| ≈ sqrt(|A|²+|B|²) for uncorrelated A,B), so a mid/side check
    # would revert exactly the legitimate splits. Conservative: revert =
    # the least-processed raw parent ships.
    try:
        _l_spec = _mean_mag_spectrum(L, sr)
        _r_spec = _mean_mag_spectrum(R, sr)
        if _l_spec is not None and _r_spec is not None:
            sl = _l_spec - _l_spec.mean()
            sr_ = _r_spec - _r_spec.mean()
            _den = float(np.sqrt((sl**2).sum() * (sr_**2).sum()))
            _spec_corr = float((sl * sr_).sum() / _den) if _den > 0 else 0.0
            if _spec_corr > 0.85:
                logger.info(
                    f"Post-split sanity: L/R spectra correlate "
                    f"{_spec_corr:.3f} (> 0.85) — one widened part, not two; "
                    "reverting split."
                )
                return {"center": stem_path}
    except Exception:  # noqa: BLE001 — sanity check is best-effort
        pass

    # Write as proper stereo so center + sides reconstructs original L/R.
    center_path = output_dir / f"{stem_path.stem}_center.wav"
    sides_path = output_dir / f"{stem_path.stem}_sides.wav"
    center_stereo = np.stack([mid, mid], axis=1)
    sides_stereo = np.stack([side, -side], axis=1)
    sf.write(str(center_path), center_stereo, sr)
    sf.write(str(sides_path), sides_stereo, sr)
    logger.info(
        f"Split {stem_path.name} -> center ({mid_energy:.4f}) + sides "
        f"({side_energy:.4f}); LR_corr={correlation:.3f}"
    )
    return {"center": center_path, "sides": sides_path}


def is_available() -> bool:
    """Check if stem separation is available (Demucs installed)."""
    return _check_demucs()
