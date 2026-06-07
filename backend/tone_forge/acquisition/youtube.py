"""YouTube acquisition via ``yt-dlp``.

Pulled out of ``tone_forge.unified_pipeline._load_from_url`` as part of
Priority 1 step 6 of ``/EXECUTION_PLAN.md``.

The function below returns primitives (numpy array, sample rate, path,
title). It does *not* return ``AudioData`` or ``PipelineConfig``-shaped
objects: those belong to the calling pipeline, not to acquisition. A
future ``acquire()`` entry point will wrap this and emit
``contracts.AcquiredAudio`` (with content hash + duration) when the
Jam-facing route lands.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import librosa
import numpy as np


def download_audio(
    url: str,
    *,
    target_sr: int,
    max_duration_s: float,
    trim_start_s: Optional[float] = None,
    trim_end_s: Optional[float] = None,
    download_timeout_s: int = 300,
    title_timeout_s: int = 30,
) -> Tuple[np.ndarray, int, Path, str]:
    """Download a YouTube URL to a wav and decode it.

    Args:
        url: YouTube (or other ``yt-dlp``-supported) URL.
        target_sr: Sample rate to decode to (Hz).
        max_duration_s: Hard cap on output duration in seconds.
        trim_start_s: Optional start offset (seconds).
        trim_end_s: Optional end offset (seconds). If both ``trim_start_s``
            and ``trim_end_s`` are ``None`` the full clip (up to
            ``max_duration_s``) is returned.
        download_timeout_s: Timeout for the ``yt-dlp`` download call.
        title_timeout_s: Timeout for the ``yt-dlp --get-title`` call.

    Returns:
        ``(audio, sr, wav_path, title)`` where ``audio`` is a mono float
        ndarray at ``sr``, ``wav_path`` points at the on-disk wav (kept
        for the caller's cache), and ``title`` is best-effort metadata.

    Raises:
        RuntimeError: if ``yt-dlp`` fails or produces no output file.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="toneforge_yt_"))

    # Best-effort title fetch — failure here is non-fatal.
    title_cmd = ["yt-dlp", "--get-title", url]
    try:
        title_result = subprocess.run(
            title_cmd, capture_output=True, text=True, timeout=title_timeout_s
        )
        title = (
            title_result.stdout.strip()[:50]
            if title_result.returncode == 0
            else "YouTube Audio"
        )
    except Exception:
        title = "YouTube Audio"

    output_path = tmp_dir / f"{title}.wav"
    download_cmd = [
        "yt-dlp",
        "-x",  # Extract audio
        "--audio-format", "wav",
        "-o", str(output_path),
        url,
    ]
    result = subprocess.run(
        download_cmd, capture_output=True, timeout=download_timeout_s
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.decode()}")

    wav_files = list(tmp_dir.glob("*.wav"))
    if not wav_files:
        raise RuntimeError("No audio file found after download")
    actual_path = wav_files[0]

    audio, sr = librosa.load(str(actual_path), sr=target_sr, mono=True)

    if trim_start_s is not None or trim_end_s is not None:
        start_sample = int((trim_start_s or 0) * sr)
        end_sample = int((trim_end_s or len(audio) / sr) * sr)
        audio = audio[start_sample:end_sample]

    max_samples = int(max_duration_s * sr)
    if len(audio) > max_samples:
        audio = audio[:max_samples]

    return audio, sr, actual_path, title
