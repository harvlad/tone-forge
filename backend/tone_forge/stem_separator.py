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
            sources = apply_model(model, wav, device=device)

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
        sources = apply_model(model, wav, device=device)

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
            sources = apply_model(model, wav, device=device)

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
            sources = apply_model(model, wav, device=device)

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


def is_available() -> bool:
    """Check if stem separation is available (Demucs installed)."""
    return _check_demucs()
