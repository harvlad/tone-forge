"""Instrumented stem separation with detailed profiling.

Wraps the stem separator with comprehensive timing instrumentation
for identifying performance bottlenecks.
"""
from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from .profiler import profile_stage, get_profiler
from .gpu_monitor import get_gpu_monitor

logger = logging.getLogger(__name__)


def separate_all_stems_profiled(
    audio_path: str | Path,
    output_dir: str | Path | None = None,
    model_name: str = "htdemucs",
) -> Dict[str, Path]:
    """Separate all stems with detailed profiling.

    This is a drop-in replacement for stem_separator.separate_all_stems
    with comprehensive timing instrumentation.

    Args:
        audio_path: Path to input audio
        output_dir: Output directory for stems
        model_name: Demucs model name

    Returns:
        Dictionary mapping stem names to paths
    """
    import torch
    import torchaudio
    import soundfile as sf
    from demucs.pretrained import get_model
    from demucs.apply import apply_model

    profiler = get_profiler()
    gpu_monitor = get_gpu_monitor()

    audio_path = Path(audio_path)
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="toneforge_stems_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    stem_paths = {}

    with profiler.profile("stem_separation") as stage:
        stage.metadata["audio_file"] = str(audio_path.name)
        stage.metadata["model"] = model_name
        stage.metadata["gpu_backend"] = gpu_monitor.backend

        # Model loading
        with profiler.profile("stem_separation/model_load") as load_stage:
            model = get_model(model_name)
            model.eval()

            # Device selection
            if torch.cuda.is_available():
                device = torch.device("cuda")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = torch.device("mps")
            else:
                device = torch.device("cpu")

            model.to(device)
            load_stage.metadata["device"] = str(device)
            load_stage.metadata["source_count"] = len(model.sources)

        # Audio loading
        with profiler.profile("stem_separation/audio_load") as audio_stage:
            audio_np, sr = sf.read(str(audio_path))
            if audio_np.ndim == 1:
                audio_np = audio_np[np.newaxis, :]
            else:
                audio_np = audio_np.T

            audio_stage.metadata["sample_rate"] = sr
            audio_stage.metadata["channels"] = audio_np.shape[0]
            audio_stage.metadata["samples"] = audio_np.shape[1]
            audio_stage.metadata["duration_sec"] = audio_np.shape[1] / sr

            wav = torch.from_numpy(audio_np).float()

        # Resampling
        with profiler.profile("stem_separation/resampling") as resample_stage:
            original_sr = sr
            if sr != model.samplerate:
                wav = torchaudio.functional.resample(wav, sr, model.samplerate)
                sr = model.samplerate
                resample_stage.metadata["resampled"] = True
                resample_stage.metadata["from_sr"] = original_sr
                resample_stage.metadata["to_sr"] = sr
            else:
                resample_stage.metadata["resampled"] = False

            # Ensure stereo
            if wav.shape[0] == 1:
                wav = wav.repeat(2, 1)
            elif wav.shape[0] > 2:
                wav = wav[:2]

            resample_stage.metadata["final_shape"] = list(wav.shape)

        # GPU transfer and inference
        with profiler.profile("stem_separation/inference") as inference_stage:
            # Record GPU memory before
            gpu_mem_before = gpu_monitor.get_stats().memory_allocated_mb

            # Transfer to GPU
            transfer_start = time.perf_counter()
            wav = wav.unsqueeze(0).to(device)
            transfer_time = (time.perf_counter() - transfer_start) * 1000

            inference_stage.metadata["transfer_time_ms"] = round(transfer_time, 2)
            inference_stage.metadata["input_shape"] = list(wav.shape)
            inference_stage.metadata["input_bytes"] = wav.nelement() * wav.element_size()

            # Synchronize before inference for accurate timing
            if device.type == "cuda":
                torch.cuda.synchronize()
            elif device.type == "mps":
                torch.mps.synchronize()

            # Run inference
            inference_start = time.perf_counter()
            with torch.no_grad():
                sources = apply_model(model, wav, device=device)
            inference_time = (time.perf_counter() - inference_start) * 1000

            # Synchronize after inference
            if device.type == "cuda":
                torch.cuda.synchronize()
            elif device.type == "mps":
                torch.mps.synchronize()

            # Record GPU time
            profiler.record_gpu_time("stem_separation/inference", inference_time)

            # Record GPU memory after
            gpu_mem_after = gpu_monitor.get_stats().memory_allocated_mb

            inference_stage.metadata["inference_time_ms"] = round(inference_time, 2)
            inference_stage.metadata["output_shape"] = list(sources.shape)
            inference_stage.metadata["gpu_memory_delta_mb"] = round(gpu_mem_after - gpu_mem_before, 2)

        # Output writing
        with profiler.profile("stem_separation/output") as output_stage:
            output_stage.metadata["stem_count"] = len(model.sources)

            for idx, stem_name in enumerate(model.sources):
                with profiler.profile(f"stem_separation/output/{stem_name}"):
                    stem_audio = sources[0, idx].cpu()
                    output_path = output_dir / f"{audio_path.stem}_{stem_name}.wav"
                    audio_out = stem_audio.numpy().T
                    sf.write(str(output_path), audio_out, sr)
                    stem_paths[stem_name] = output_path

            output_stage.metadata["total_output_mb"] = sum(
                p.stat().st_size / (1024 * 1024) for p in stem_paths.values()
            )

        # Cleanup GPU memory
        if device.type == "cuda":
            torch.cuda.empty_cache()
        elif device.type == "mps":
            torch.mps.empty_cache()

        stage.metadata["stem_count"] = len(stem_paths)
        stage.metadata["stems"] = list(stem_paths.keys())

    return stem_paths


def separate_single_stem_profiled(
    audio_path: str | Path,
    stem_name: str,
    output_dir: str | Path | None = None,
    model_name: str = "htdemucs",
) -> Path:
    """Separate a single stem with profiling.

    Args:
        audio_path: Path to input audio
        stem_name: Name of stem to extract (bass, drums, vocals, other)
        output_dir: Output directory
        model_name: Demucs model name

    Returns:
        Path to extracted stem
    """
    # Use the all-stems function and return the requested one
    # This is less efficient but maintains profiling consistency
    stems = separate_all_stems_profiled(audio_path, output_dir, model_name)

    if stem_name not in stems:
        available = list(stems.keys())
        raise ValueError(f"Stem '{stem_name}' not found. Available: {available}")

    return stems[stem_name]


def get_stem_separation_summary(report) -> Dict:
    """Extract stem separation summary from a profile report.

    Args:
        report: ProfileReport from profiler

    Returns:
        Summary dictionary
    """
    summary = {
        "total_time_ms": 0,
        "model_load_ms": 0,
        "audio_load_ms": 0,
        "resampling_ms": 0,
        "inference_ms": 0,
        "output_ms": 0,
        "gpu_time_ms": 0,
    }

    for name, stage in report.stages.items():
        if name == "stem_separation":
            summary["total_time_ms"] = stage.wall_time_ms
        elif name == "stem_separation/model_load":
            summary["model_load_ms"] = stage.wall_time_ms
        elif name == "stem_separation/audio_load":
            summary["audio_load_ms"] = stage.wall_time_ms
        elif name == "stem_separation/resampling":
            summary["resampling_ms"] = stage.wall_time_ms
        elif name == "stem_separation/inference":
            summary["inference_ms"] = stage.wall_time_ms
            summary["gpu_time_ms"] = stage.gpu_time_ms
        elif name == "stem_separation/output":
            summary["output_ms"] = stage.wall_time_ms

    return summary
