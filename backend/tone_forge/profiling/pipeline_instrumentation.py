"""Pipeline instrumentation for comprehensive profiling.

Provides instrumented wrappers for all major pipeline stages.
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .profiler import Profiler, ProfileReport, get_profiler, set_profiler, reset_profiler, profile_stage
from .gpu_monitor import GPUMonitor, get_gpu_monitor
from .memory_tracker import MemoryTracker, get_memory_tracker

logger = logging.getLogger(__name__)


@dataclass
class PipelineTimings:
    """Collected timings for a pipeline run."""

    # Top-level stages (ms)
    audio_loading_ms: float = 0.0
    stem_separation_ms: float = 0.0
    midi_extraction_ms: float = 0.0
    tone_analysis_ms: float = 0.0
    quality_analysis_ms: float = 0.0
    visualization_ms: float = 0.0
    export_ms: float = 0.0

    # Stem separation breakdown
    stems_model_load_ms: float = 0.0
    stems_resampling_ms: float = 0.0
    stems_inference_ms: float = 0.0
    stems_output_ms: float = 0.0

    # MIDI extraction breakdown
    midi_basic_pitch_ms: float = 0.0
    midi_pass_pipeline_ms: float = 0.0
    midi_per_pass_ms: Dict[str, float] = field(default_factory=dict)

    # Spectral analysis breakdown
    stft_ms: float = 0.0
    chroma_ms: float = 0.0
    spectral_validation_ms: float = 0.0

    # GPU timing
    gpu_total_ms: float = 0.0
    gpu_stem_separation_ms: float = 0.0
    gpu_basic_pitch_ms: float = 0.0

    # Memory
    peak_memory_mb: float = 0.0
    peak_gpu_memory_mb: float = 0.0

    # Metadata
    audio_duration_sec: float = 0.0
    stem_count: int = 0
    note_count: int = 0

    @property
    def total_ms(self) -> float:
        """Total pipeline time."""
        return (
            self.audio_loading_ms +
            self.stem_separation_ms +
            self.midi_extraction_ms +
            self.tone_analysis_ms +
            self.quality_analysis_ms +
            self.visualization_ms +
            self.export_ms
        )

    @property
    def realtime_factor(self) -> float:
        """Processing time as multiple of audio duration."""
        if self.audio_duration_sec <= 0:
            return 0.0
        return (self.total_ms / 1000) / self.audio_duration_sec

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "stages": {
                "audio_loading_ms": round(self.audio_loading_ms, 2),
                "stem_separation_ms": round(self.stem_separation_ms, 2),
                "midi_extraction_ms": round(self.midi_extraction_ms, 2),
                "tone_analysis_ms": round(self.tone_analysis_ms, 2),
                "quality_analysis_ms": round(self.quality_analysis_ms, 2),
                "visualization_ms": round(self.visualization_ms, 2),
                "export_ms": round(self.export_ms, 2),
            },
            "stem_separation": {
                "model_load_ms": round(self.stems_model_load_ms, 2),
                "resampling_ms": round(self.stems_resampling_ms, 2),
                "inference_ms": round(self.stems_inference_ms, 2),
                "output_ms": round(self.stems_output_ms, 2),
            },
            "midi_extraction": {
                "basic_pitch_ms": round(self.midi_basic_pitch_ms, 2),
                "pass_pipeline_ms": round(self.midi_pass_pipeline_ms, 2),
                "per_pass_ms": {k: round(v, 2) for k, v in self.midi_per_pass_ms.items()},
            },
            "spectral": {
                "stft_ms": round(self.stft_ms, 2),
                "chroma_ms": round(self.chroma_ms, 2),
                "validation_ms": round(self.spectral_validation_ms, 2),
            },
            "gpu": {
                "total_ms": round(self.gpu_total_ms, 2),
                "stem_separation_ms": round(self.gpu_stem_separation_ms, 2),
                "basic_pitch_ms": round(self.gpu_basic_pitch_ms, 2),
            },
            "memory": {
                "peak_mb": round(self.peak_memory_mb, 2),
                "peak_gpu_mb": round(self.peak_gpu_memory_mb, 2),
            },
            "metadata": {
                "audio_duration_sec": round(self.audio_duration_sec, 2),
                "stem_count": self.stem_count,
                "note_count": self.note_count,
            },
            "summary": {
                "total_ms": round(self.total_ms, 2),
                "total_sec": round(self.total_ms / 1000, 2),
                "realtime_factor": round(self.realtime_factor, 2),
            },
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "PIPELINE TIMING SUMMARY",
            "=" * 50,
            f"Audio duration: {self.audio_duration_sec:.1f}s",
            f"Total time: {self.total_ms / 1000:.2f}s ({self.realtime_factor:.1f}x realtime)",
            "",
            "STAGE BREAKDOWN:",
            f"  Audio loading:    {self.audio_loading_ms:>8.0f}ms",
            f"  Stem separation:  {self.stem_separation_ms:>8.0f}ms",
            f"  MIDI extraction:  {self.midi_extraction_ms:>8.0f}ms",
            f"  Tone analysis:    {self.tone_analysis_ms:>8.0f}ms",
            f"  Quality analysis: {self.quality_analysis_ms:>8.0f}ms",
            f"  Visualization:    {self.visualization_ms:>8.0f}ms",
            f"  Export:           {self.export_ms:>8.0f}ms",
            "",
        ]

        if self.stems_inference_ms > 0:
            lines.extend([
                "STEM SEPARATION:",
                f"  Model load:    {self.stems_model_load_ms:>8.0f}ms",
                f"  Resampling:    {self.stems_resampling_ms:>8.0f}ms",
                f"  Inference:     {self.stems_inference_ms:>8.0f}ms (GPU)",
                f"  Output write:  {self.stems_output_ms:>8.0f}ms",
                "",
            ])

        if self.midi_basic_pitch_ms > 0:
            lines.extend([
                "MIDI EXTRACTION:",
                f"  basic-pitch:   {self.midi_basic_pitch_ms:>8.0f}ms (GPU)",
                f"  Pass pipeline: {self.midi_pass_pipeline_ms:>8.0f}ms",
            ])
            for pass_name, pass_time in sorted(
                self.midi_per_pass_ms.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5]:
                lines.append(f"    {pass_name}: {pass_time:.0f}ms")
            lines.append("")

        lines.extend([
            "RESOURCES:",
            f"  Peak memory:     {self.peak_memory_mb:>8.1f}MB",
            f"  Peak GPU memory: {self.peak_gpu_memory_mb:>8.1f}MB",
            f"  GPU time:        {self.gpu_total_ms:>8.0f}ms",
        ])

        return "\n".join(lines)


class InstrumentedPipeline:
    """Wrapper for instrumenting the analysis pipeline.

    Usage:
        pipeline = InstrumentedPipeline()
        pipeline.start()

        with pipeline.stage("stem_separation"):
            # ... do stem separation

        with pipeline.stage("midi_extraction"):
            # ... do MIDI extraction

        report = pipeline.finish()
        print(report.summary())
    """

    def __init__(
        self,
        run_id: Optional[str] = None,
        enable_gpu_monitoring: bool = True,
        enable_memory_tracking: bool = True,
        output_dir: Optional[Path] = None,
    ):
        """Initialize instrumented pipeline.

        Args:
            run_id: Optional run identifier
            enable_gpu_monitoring: Track GPU metrics
            enable_memory_tracking: Track memory metrics
            output_dir: Directory for saving reports
        """
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = output_dir or Path("profile_reports")

        self._profiler = Profiler(
            run_id=self.run_id,
            enable_gpu_monitoring=enable_gpu_monitoring,
            enable_memory_tracking=enable_memory_tracking,
        )

        self._gpu_monitor = get_gpu_monitor() if enable_gpu_monitoring else None
        self._memory_tracker = get_memory_tracker() if enable_memory_tracking else None

        self._timings = PipelineTimings()
        self._started = False

    def start(self) -> None:
        """Start pipeline profiling.

        Sets the internal profiler as the global profiler so that
        instrumented functions (which use get_profiler()) record to
        the same profiler instance.
        """
        # Set as global profiler so instrumented functions use this instance
        set_profiler(self._profiler)

        self._profiler.start()

        if self._gpu_monitor:
            self._gpu_monitor.reset_peak_memory()
            self._gpu_monitor.start_monitoring(interval_sec=0.1)

        if self._memory_tracker:
            self._memory_tracker.set_baseline()
            self._memory_tracker.start_monitoring(interval_sec=0.5)

        self._started = True

    @contextmanager
    def stage(self, stage_name: str, metadata: Optional[Dict[str, Any]] = None):
        """Profile a pipeline stage.

        Args:
            stage_name: Name of the stage
            metadata: Optional metadata
        """
        with self._profiler.profile(stage_name, metadata) as profile:
            yield profile

    def record_timing(self, name: str, time_ms: float) -> None:
        """Record a specific timing.

        Args:
            name: Timing name (must match PipelineTimings field)
            time_ms: Time in milliseconds
        """
        if hasattr(self._timings, name):
            setattr(self._timings, name, time_ms)
        else:
            logger.warning(f"Unknown timing field: {name}")

    def record_gpu_time(self, stage_name: str, gpu_time_ms: float) -> None:
        """Record GPU execution time.

        Args:
            stage_name: Stage name
            gpu_time_ms: GPU time in milliseconds
        """
        self._profiler.record_gpu_time(stage_name, gpu_time_ms)

    def add_metadata(self, key: str, value: Any) -> None:
        """Add metadata to current stage.

        Args:
            key: Metadata key
            value: Metadata value
        """
        if hasattr(self._timings, key):
            setattr(self._timings, key, value)

    def finish(self) -> ProfileReport:
        """Finish profiling and generate report.

        Returns:
            ProfileReport with all collected data
        """
        # Stop monitoring
        if self._gpu_monitor:
            gpu_samples = self._gpu_monitor.stop_monitoring()
            gpu_summary = self._gpu_monitor.get_summary(gpu_samples)
            self._timings.peak_gpu_memory_mb = gpu_summary.get("memory", {}).get("max_allocated_mb", 0)

        if self._memory_tracker:
            mem_samples = self._memory_tracker.stop_monitoring()
            mem_summary = self._memory_tracker.get_summary(mem_samples)
            self._timings.peak_memory_mb = mem_summary.get("rss", {}).get("max_mb", 0)

        # Get report
        report = self._profiler.get_report()

        # Map profiler stages to timings
        self._map_stages_to_timings(report)

        return report

    def _map_stages_to_timings(self, report: ProfileReport) -> None:
        """Map profiler stages to PipelineTimings fields.

        Handles stages with stem suffixes (e.g., midi_extraction_bass, midi_extraction_other)
        by accumulating times into the appropriate aggregate field.
        """
        for name, stage in report.stages.items():
            # Top-level stages (exact matches)
            if name == "audio_loading":
                self._timings.audio_loading_ms = stage.wall_time_ms
            elif name == "stem_separation":
                self._timings.stem_separation_ms = stage.wall_time_ms
                self._timings.gpu_stem_separation_ms = stage.gpu_time_ms
            elif name == "tone_analysis":
                self._timings.tone_analysis_ms = stage.wall_time_ms
            elif name == "quality_analysis":
                self._timings.quality_analysis_ms = stage.wall_time_ms
            elif name == "visualization":
                self._timings.visualization_ms = stage.wall_time_ms
            elif name == "export":
                self._timings.export_ms = stage.wall_time_ms

            # MIDI extraction - exact match or with stem suffix (midi_extraction_bass, midi_extraction_other, etc.)
            elif name == "midi_extraction" or name.startswith("midi_extraction_"):
                # Accumulate all MIDI extraction times
                self._timings.midi_extraction_ms += stage.wall_time_ms

            # Stem separation sub-stages
            elif name == "stem_separation/model_load":
                self._timings.stems_model_load_ms = stage.wall_time_ms
            elif name == "stem_separation/resampling":
                self._timings.stems_resampling_ms = stage.wall_time_ms
            elif name == "stem_separation/inference":
                self._timings.stems_inference_ms = stage.wall_time_ms
            elif name == "stem_separation/output":
                self._timings.stems_output_ms = stage.wall_time_ms

            # MIDI extraction sub-stages - handle with stem suffix
            # e.g., midi_extraction/basic_pitch or midi_extraction_other/basic_pitch
            elif "/basic_pitch" in name and ("midi_extraction" in name):
                self._timings.midi_basic_pitch_ms += stage.wall_time_ms
                self._timings.gpu_basic_pitch_ms += stage.gpu_time_ms
            elif "/pass_pipeline" in name and ("midi_extraction" in name) and not "/pass_pipeline/" in name:
                self._timings.midi_pass_pipeline_ms += stage.wall_time_ms

            # MIDI passes - e.g., midi_extraction/pass_pipeline/OctaveCorrection
            elif "/pass_pipeline/" in name and ("midi_extraction" in name):
                pass_name = name.split("/")[-1]
                if pass_name in self._timings.midi_per_pass_ms:
                    self._timings.midi_per_pass_ms[pass_name] += stage.wall_time_ms
                else:
                    self._timings.midi_per_pass_ms[pass_name] = stage.wall_time_ms

            # Spectral analysis
            elif name == "spectral/stft":
                self._timings.stft_ms = stage.wall_time_ms
            elif name == "spectral/chroma":
                self._timings.chroma_ms = stage.wall_time_ms
            elif name == "spectral/validation":
                self._timings.spectral_validation_ms = stage.wall_time_ms

        # Calculate GPU total
        self._timings.gpu_total_ms = (
            self._timings.gpu_stem_separation_ms +
            self._timings.gpu_basic_pitch_ms
        )

    def get_timings(self) -> PipelineTimings:
        """Get collected pipeline timings."""
        return self._timings

    def save_report(self, report: ProfileReport) -> Path:
        """Save report to JSON file.

        Args:
            report: Report to save

        Returns:
            Path to saved file
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"profile_{self.run_id}.json"

        with open(output_path, "w") as f:
            json.dump({
                "report": report.to_dict(),
                "timings": self._timings.to_dict(),
            }, f, indent=2)

        logger.info(f"Saved profile report to {output_path}")
        return output_path


def generate_flamegraph_data(report: ProfileReport) -> List[Dict[str, Any]]:
    """Generate flamegraph-style data from profile report.

    Args:
        report: Profile report

    Returns:
        List of flamegraph data entries
    """
    data = []

    def process_stage(name: str, stage, depth: int = 0):
        entry = {
            "name": name.split("/")[-1],
            "full_name": name,
            "value": stage.wall_time_ms,
            "depth": depth,
            "cpu_time": stage.cpu_time_ms,
            "gpu_time": stage.gpu_time_ms,
            "memory_delta": stage.memory_delta_mb,
        }
        data.append(entry)

        # Process children
        for child_name in stage.children:
            if child_name in report.stages:
                process_stage(child_name, report.stages[child_name], depth + 1)

    # Start with root stages (no parent)
    for name, stage in report.stages.items():
        if stage.parent is None:
            process_stage(name, stage)

    return data


def generate_bottleneck_report(report: ProfileReport) -> Dict[str, Any]:
    """Generate bottleneck analysis report.

    Args:
        report: Profile report

    Returns:
        Bottleneck analysis dictionary
    """
    total_time = report.total_wall_time_ms
    if total_time <= 0:
        return {"error": "no_timing_data"}

    # Get top stages
    top_stages = report.get_stage_ranking("wall_time_ms")[:10]

    # Categorize bottlenecks
    cpu_bound = []
    gpu_bound = []
    memory_bound = []
    io_bound = []

    for name, stage in report.stages.items():
        time_pct = (stage.wall_time_ms / total_time) * 100 if total_time > 0 else 0

        if time_pct < 1:  # Skip insignificant stages
            continue

        entry = {
            "name": name,
            "time_ms": stage.wall_time_ms,
            "time_pct": time_pct,
            "cpu_utilization": stage.cpu_utilization,
            "gpu_utilization": stage.gpu_utilization,
            "memory_delta_mb": stage.memory_delta_mb,
        }

        if stage.cpu_utilization >= 0.8:
            cpu_bound.append(entry)
        elif stage.gpu_utilization >= 0.5:
            gpu_bound.append(entry)
        elif abs(stage.memory_delta_mb) > 100:
            memory_bound.append(entry)
        elif stage.cpu_utilization < 0.3 and stage.gpu_utilization < 0.1:
            io_bound.append(entry)

    return {
        "total_time_ms": total_time,
        "top_stages": [
            {"name": name, "time_ms": time_ms, "pct": (time_ms / total_time) * 100}
            for name, time_ms in top_stages
        ],
        "categories": {
            "cpu_bound": sorted(cpu_bound, key=lambda x: x["time_ms"], reverse=True),
            "gpu_bound": sorted(gpu_bound, key=lambda x: x["time_ms"], reverse=True),
            "memory_bound": sorted(memory_bound, key=lambda x: x["time_ms"], reverse=True),
            "io_bound": sorted(io_bound, key=lambda x: x["time_ms"], reverse=True),
        },
        "recommendations": _generate_recommendations(cpu_bound, gpu_bound, memory_bound, io_bound),
    }


def _generate_recommendations(
    cpu_bound: List[Dict],
    gpu_bound: List[Dict],
    memory_bound: List[Dict],
    io_bound: List[Dict],
) -> List[str]:
    """Generate optimization recommendations."""
    recommendations = []

    if cpu_bound:
        top_cpu = cpu_bound[0]["name"]
        recommendations.append(
            f"CPU bottleneck in '{top_cpu}' - consider parallelization or algorithmic optimization"
        )

    if gpu_bound:
        top_gpu = gpu_bound[0]["name"]
        recommendations.append(
            f"GPU-intensive stage '{top_gpu}' - optimize batch sizes or consider caching"
        )

    if memory_bound:
        top_mem = memory_bound[0]["name"]
        delta = memory_bound[0]["memory_delta_mb"]
        recommendations.append(
            f"High memory usage in '{top_mem}' ({delta:.0f}MB) - consider streaming or chunking"
        )

    if io_bound:
        top_io = io_bound[0]["name"]
        recommendations.append(
            f"Possible I/O wait in '{top_io}' - consider async loading or caching"
        )

    if not recommendations:
        recommendations.append("Pipeline appears well-balanced - no obvious bottlenecks")

    return recommendations
