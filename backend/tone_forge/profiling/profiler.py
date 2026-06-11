"""Core profiling infrastructure for ToneForge pipeline.

Provides hierarchical timing, memory tracking, and GPU monitoring
for comprehensive performance analysis.
"""
from __future__ import annotations

import functools
import gc
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

import psutil

logger = logging.getLogger(__name__)

# Thread-local storage for profiler context
_thread_local = threading.local()

# Type variable for decorated functions
F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class StageProfile:
    """Profile data for a single pipeline stage."""

    name: str
    parent: Optional[str] = None

    # Timing (all in milliseconds)
    wall_time_ms: float = 0.0
    cpu_time_ms: float = 0.0
    user_time_ms: float = 0.0
    system_time_ms: float = 0.0

    # Memory (in MB)
    memory_start_mb: float = 0.0
    memory_end_mb: float = 0.0
    memory_peak_mb: float = 0.0
    memory_delta_mb: float = 0.0

    # GPU (in MB)
    gpu_memory_start_mb: float = 0.0
    gpu_memory_end_mb: float = 0.0
    gpu_memory_peak_mb: float = 0.0
    gpu_time_ms: float = 0.0

    # Execution metadata
    invocation_count: int = 0
    error_count: int = 0

    # Child stages
    children: List[str] = field(default_factory=list)

    # Additional metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Timestamps
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    @property
    def total_time_ms(self) -> float:
        """Total wall time including all invocations."""
        return self.wall_time_ms

    @property
    def avg_time_ms(self) -> float:
        """Average wall time per invocation."""
        return self.wall_time_ms / max(self.invocation_count, 1)

    @property
    def cpu_utilization(self) -> float:
        """CPU utilization as ratio of CPU time to wall time."""
        if self.wall_time_ms <= 0:
            return 0.0
        return self.cpu_time_ms / self.wall_time_ms

    @property
    def gpu_utilization(self) -> float:
        """GPU utilization as ratio of GPU time to wall time."""
        if self.wall_time_ms <= 0:
            return 0.0
        return self.gpu_time_ms / self.wall_time_ms

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "parent": self.parent,
            "timing": {
                "wall_time_ms": round(self.wall_time_ms, 2),
                "cpu_time_ms": round(self.cpu_time_ms, 2),
                "user_time_ms": round(self.user_time_ms, 2),
                "system_time_ms": round(self.system_time_ms, 2),
                "avg_time_ms": round(self.avg_time_ms, 2),
                "gpu_time_ms": round(self.gpu_time_ms, 2),
            },
            "memory": {
                "start_mb": round(self.memory_start_mb, 2),
                "end_mb": round(self.memory_end_mb, 2),
                "peak_mb": round(self.memory_peak_mb, 2),
                "delta_mb": round(self.memory_delta_mb, 2),
            },
            "gpu_memory": {
                "start_mb": round(self.gpu_memory_start_mb, 2),
                "end_mb": round(self.gpu_memory_end_mb, 2),
                "peak_mb": round(self.gpu_memory_peak_mb, 2),
            },
            "utilization": {
                "cpu": round(self.cpu_utilization, 3),
                "gpu": round(self.gpu_utilization, 3),
            },
            "invocation_count": self.invocation_count,
            "error_count": self.error_count,
            "children": self.children,
            "metadata": self.metadata,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }


@dataclass
class ProfileReport:
    """Complete profile report for a pipeline run."""

    run_id: str
    start_time: str
    end_time: str
    total_wall_time_ms: float

    # Stage profiles
    stages: Dict[str, StageProfile] = field(default_factory=dict)

    # System info
    system_info: Dict[str, Any] = field(default_factory=dict)

    # Top-level metrics
    total_cpu_time_ms: float = 0.0
    total_gpu_time_ms: float = 0.0
    peak_memory_mb: float = 0.0
    peak_gpu_memory_mb: float = 0.0

    def get_stage_ranking(self, metric: str = "wall_time_ms") -> List[Tuple[str, float]]:
        """Get stages ranked by a metric."""
        rankings = []
        for name, stage in self.stages.items():
            value = getattr(stage, metric, 0)
            rankings.append((name, value))
        return sorted(rankings, key=lambda x: x[1], reverse=True)

    def get_bottlenecks(self, top_n: int = 5) -> List[Tuple[str, StageProfile]]:
        """Get top N bottleneck stages by wall time."""
        rankings = self.get_stage_ranking("wall_time_ms")
        return [(name, self.stages[name]) for name, _ in rankings[:top_n]]

    def get_cpu_bound_stages(self, threshold: float = 0.8) -> List[str]:
        """Get stages that are CPU-bound (high CPU utilization)."""
        return [
            name for name, stage in self.stages.items()
            if stage.cpu_utilization >= threshold and stage.wall_time_ms > 100
        ]

    def get_gpu_bound_stages(self, threshold: float = 0.5) -> List[str]:
        """Get stages that use significant GPU time."""
        return [
            name for name, stage in self.stages.items()
            if stage.gpu_utilization >= threshold and stage.gpu_time_ms > 100
        ]

    def get_memory_intensive_stages(self, threshold_mb: float = 100) -> List[str]:
        """Get stages with high memory delta."""
        return [
            name for name, stage in self.stages.items()
            if abs(stage.memory_delta_mb) >= threshold_mb
        ]

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "=" * 70,
            "PIPELINE PROFILE REPORT",
            "=" * 70,
            f"Run ID: {self.run_id}",
            f"Total time: {self.total_wall_time_ms / 1000:.2f}s",
            f"CPU time: {self.total_cpu_time_ms / 1000:.2f}s",
            f"GPU time: {self.total_gpu_time_ms / 1000:.2f}s",
            f"Peak memory: {self.peak_memory_mb:.1f} MB",
            f"Peak GPU memory: {self.peak_gpu_memory_mb:.1f} MB",
            "",
            "TOP 10 STAGES BY TIME:",
            "-" * 70,
            f"{'Stage':<40} {'Time (s)':>10} {'CPU %':>8} {'GPU %':>8}",
            "-" * 70,
        ]

        rankings = self.get_stage_ranking()[:10]
        for name, time_ms in rankings:
            stage = self.stages[name]
            time_s = time_ms / 1000
            cpu_pct = stage.cpu_utilization * 100
            gpu_pct = stage.gpu_utilization * 100
            lines.append(f"{name:<40} {time_s:>10.2f} {cpu_pct:>7.1f}% {gpu_pct:>7.1f}%")

        # Bottleneck analysis
        lines.extend([
            "",
            "BOTTLENECK ANALYSIS:",
            "-" * 70,
        ])

        cpu_bound = self.get_cpu_bound_stages()
        if cpu_bound:
            lines.append(f"CPU-bound stages: {', '.join(cpu_bound[:5])}")

        gpu_bound = self.get_gpu_bound_stages()
        if gpu_bound:
            lines.append(f"GPU-intensive stages: {', '.join(gpu_bound[:5])}")

        memory_intensive = self.get_memory_intensive_stages()
        if memory_intensive:
            lines.append(f"Memory-intensive stages: {', '.join(memory_intensive[:5])}")

        # Time breakdown
        total = self.total_wall_time_ms
        if total > 0:
            lines.extend([
                "",
                "TIME BREAKDOWN:",
                "-" * 70,
            ])
            for name, time_ms in rankings[:5]:
                pct = (time_ms / total) * 100
                bar_len = int(pct / 2)
                bar = "█" * bar_len + "░" * (50 - bar_len)
                lines.append(f"{name:<30} {bar} {pct:>5.1f}%")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_wall_time_ms": round(self.total_wall_time_ms, 2),
            "total_cpu_time_ms": round(self.total_cpu_time_ms, 2),
            "total_gpu_time_ms": round(self.total_gpu_time_ms, 2),
            "peak_memory_mb": round(self.peak_memory_mb, 2),
            "peak_gpu_memory_mb": round(self.peak_gpu_memory_mb, 2),
            "stages": {name: stage.to_dict() for name, stage in self.stages.items()},
            "rankings": {
                "by_wall_time": self.get_stage_ranking("wall_time_ms")[:10],
                "by_cpu_time": self.get_stage_ranking("cpu_time_ms")[:10],
                "by_memory_delta": self.get_stage_ranking("memory_delta_mb")[:10],
            },
            "bottlenecks": {
                "cpu_bound": self.get_cpu_bound_stages(),
                "gpu_bound": self.get_gpu_bound_stages(),
                "memory_intensive": self.get_memory_intensive_stages(),
            },
            "system_info": self.system_info,
        }


class Profiler:
    """Hierarchical profiler for pipeline instrumentation.

    Usage:
        profiler = Profiler()
        with profiler.profile("stem_separation"):
            with profiler.profile("model_load"):
                load_model()
            with profiler.profile("inference"):
                run_inference()

        report = profiler.get_report()
        print(report.summary())
    """

    def __init__(
        self,
        run_id: Optional[str] = None,
        enable_gpu_monitoring: bool = True,
        enable_memory_tracking: bool = True,
    ):
        """Initialize the profiler.

        Args:
            run_id: Optional run identifier
            enable_gpu_monitoring: Track GPU memory and time
            enable_memory_tracking: Track CPU memory usage
        """
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.enable_gpu_monitoring = enable_gpu_monitoring
        self.enable_memory_tracking = enable_memory_tracking

        self._stages: Dict[str, StageProfile] = {}
        self._active_stages: List[str] = []
        self._lock = threading.Lock()
        self._start_time: Optional[float] = None
        self._start_datetime: Optional[str] = None

        # System info
        self._system_info = self._collect_system_info()

        # Peak tracking
        self._peak_memory_mb = 0.0
        self._peak_gpu_memory_mb = 0.0

    def _collect_system_info(self) -> Dict[str, Any]:
        """Collect system information."""
        import platform

        info = {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        }

        # GPU info
        try:
            import torch
            info["torch_version"] = torch.__version__
            info["cuda_available"] = torch.cuda.is_available()
            info["mps_available"] = torch.backends.mps.is_available()
            if torch.cuda.is_available():
                info["cuda_device"] = torch.cuda.get_device_name(0)
            if torch.backends.mps.is_available():
                info["mps_device"] = "Apple Silicon GPU"
        except ImportError:
            pass

        return info

    def start(self) -> None:
        """Start the profiling session."""
        self._start_time = time.perf_counter()
        self._start_datetime = datetime.now().isoformat()
        gc.collect()  # Clean slate

    def _get_memory_mb(self) -> float:
        """Get current process memory usage in MB."""
        if not self.enable_memory_tracking:
            return 0.0
        try:
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0

    def _get_gpu_memory_mb(self) -> float:
        """Get current GPU memory usage in MB."""
        if not self.enable_gpu_monitoring:
            return 0.0
        try:
            import torch
            if torch.cuda.is_available():
                return torch.cuda.memory_allocated() / (1024 * 1024)
            elif torch.backends.mps.is_available():
                # MPS doesn't expose memory stats directly
                # We'll estimate from driver memory
                return torch.mps.driver_allocated_memory() / (1024 * 1024)
        except Exception:
            pass
        return 0.0

    def _get_cpu_times(self) -> Tuple[float, float, float]:
        """Get CPU times (user, system, total) in seconds."""
        try:
            process = psutil.Process(os.getpid())
            times = process.cpu_times()
            return times.user, times.system, times.user + times.system
        except Exception:
            return 0.0, 0.0, 0.0

    @contextmanager
    def profile(
        self,
        stage_name: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Context manager for profiling a stage.

        Args:
            stage_name: Name of the stage
            metadata: Optional metadata to attach
        """
        full_name = stage_name
        parent = None

        with self._lock:
            # Build hierarchical name
            if self._active_stages:
                parent = self._active_stages[-1]
                full_name = f"{parent}/{stage_name}"

            # Initialize or get stage profile
            if full_name not in self._stages:
                self._stages[full_name] = StageProfile(
                    name=full_name,
                    parent=parent,
                )

            stage = self._stages[full_name]
            stage.invocation_count += 1
            stage.start_time = datetime.now().isoformat()

            if metadata:
                stage.metadata.update(metadata)

            # Add to parent's children
            if parent and full_name not in self._stages[parent].children:
                self._stages[parent].children.append(full_name)

            self._active_stages.append(full_name)

        # Capture start metrics
        wall_start = time.perf_counter()
        cpu_start = self._get_cpu_times()
        mem_start = self._get_memory_mb()
        gpu_mem_start = self._get_gpu_memory_mb()

        stage.memory_start_mb = mem_start
        stage.gpu_memory_start_mb = gpu_mem_start

        try:
            yield stage
        except Exception as e:
            stage.error_count += 1
            raise
        finally:
            # Capture end metrics
            wall_end = time.perf_counter()
            cpu_end = self._get_cpu_times()
            mem_end = self._get_memory_mb()
            gpu_mem_end = self._get_gpu_memory_mb()

            # Update stage profile
            stage.wall_time_ms += (wall_end - wall_start) * 1000
            stage.user_time_ms += (cpu_end[0] - cpu_start[0]) * 1000
            stage.system_time_ms += (cpu_end[1] - cpu_start[1]) * 1000
            stage.cpu_time_ms += (cpu_end[2] - cpu_start[2]) * 1000

            stage.memory_end_mb = mem_end
            stage.memory_delta_mb += mem_end - mem_start
            stage.memory_peak_mb = max(stage.memory_peak_mb, mem_end)

            stage.gpu_memory_end_mb = gpu_mem_end
            stage.gpu_memory_peak_mb = max(stage.gpu_memory_peak_mb, gpu_mem_end)

            stage.end_time = datetime.now().isoformat()

            # Update peak tracking
            self._peak_memory_mb = max(self._peak_memory_mb, mem_end)
            self._peak_gpu_memory_mb = max(self._peak_gpu_memory_mb, gpu_mem_end)

            with self._lock:
                self._active_stages.pop()

    def record_gpu_time(self, stage_name: str, gpu_time_ms: float) -> None:
        """Record GPU execution time for a stage.

        Use this for explicit GPU timing (e.g., CUDA events).

        Args:
            stage_name: Stage to record for
            gpu_time_ms: GPU time in milliseconds
        """
        with self._lock:
            if stage_name in self._stages:
                self._stages[stage_name].gpu_time_ms += gpu_time_ms

    def add_metadata(self, stage_name: str, key: str, value: Any) -> None:
        """Add metadata to a stage.

        Args:
            stage_name: Stage name
            key: Metadata key
            value: Metadata value
        """
        with self._lock:
            if stage_name in self._stages:
                self._stages[stage_name].metadata[key] = value

    def get_report(self) -> ProfileReport:
        """Generate the profile report."""
        end_time = time.perf_counter()
        end_datetime = datetime.now().isoformat()

        total_wall_time = (end_time - (self._start_time or end_time)) * 1000

        # Calculate totals
        total_cpu_time = sum(s.cpu_time_ms for s in self._stages.values())
        total_gpu_time = sum(s.gpu_time_ms for s in self._stages.values())

        return ProfileReport(
            run_id=self.run_id,
            start_time=self._start_datetime or end_datetime,
            end_time=end_datetime,
            total_wall_time_ms=total_wall_time,
            stages=dict(self._stages),
            system_info=self._system_info,
            total_cpu_time_ms=total_cpu_time,
            total_gpu_time_ms=total_gpu_time,
            peak_memory_mb=self._peak_memory_mb,
            peak_gpu_memory_mb=self._peak_gpu_memory_mb,
        )

    def reset(self) -> None:
        """Reset profiler state."""
        with self._lock:
            self._stages.clear()
            self._active_stages.clear()
            self._start_time = None
            self._start_datetime = None
            self._peak_memory_mb = 0.0
            self._peak_gpu_memory_mb = 0.0


# Global profiler instance
_global_profiler: Optional[Profiler] = None
_profiler_lock = threading.Lock()


def get_profiler() -> Profiler:
    """Get or create the global profiler instance."""
    global _global_profiler
    with _profiler_lock:
        if _global_profiler is None:
            _global_profiler = Profiler()
        return _global_profiler


def set_profiler(profiler: Profiler) -> None:
    """Set the global profiler instance."""
    global _global_profiler
    with _profiler_lock:
        _global_profiler = profiler


def reset_profiler() -> None:
    """Reset the global profiler."""
    global _global_profiler
    with _profiler_lock:
        if _global_profiler:
            _global_profiler.reset()


@contextmanager
def profile_stage(
    stage_name: str,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Context manager for profiling a stage using the global profiler.

    Args:
        stage_name: Name of the stage
        metadata: Optional metadata
    """
    profiler = get_profiler()
    with profiler.profile(stage_name, metadata) as stage:
        yield stage


def profile_function(stage_name: Optional[str] = None) -> Callable[[F], F]:
    """Decorator for profiling a function.

    Args:
        stage_name: Optional stage name (defaults to function name)

    Returns:
        Decorated function
    """
    def decorator(func: F) -> F:
        name = stage_name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with profile_stage(name):
                return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator
