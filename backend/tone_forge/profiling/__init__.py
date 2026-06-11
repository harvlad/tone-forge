"""ToneForge profiling infrastructure.

Provides detailed instrumentation for pipeline performance analysis.
"""

from .profiler import (
    Profiler,
    StageProfile,
    ProfileReport,
    get_profiler,
    set_profiler,
    reset_profiler,
    profile_stage,
    profile_function,
)
from .gpu_monitor import GPUMonitor, GPUStats, get_gpu_monitor
from .memory_tracker import MemoryTracker, MemorySnapshot, get_memory_tracker
from .pipeline_instrumentation import (
    PipelineTimings,
    InstrumentedPipeline,
    generate_flamegraph_data,
    generate_bottleneck_report,
)

__all__ = [
    # Core profiler
    "Profiler",
    "StageProfile",
    "ProfileReport",
    "get_profiler",
    "set_profiler",
    "reset_profiler",
    "profile_stage",
    "profile_function",
    # GPU monitoring
    "GPUMonitor",
    "GPUStats",
    "get_gpu_monitor",
    # Memory tracking
    "MemoryTracker",
    "MemorySnapshot",
    "get_memory_tracker",
    # Pipeline instrumentation
    "PipelineTimings",
    "InstrumentedPipeline",
    "generate_flamegraph_data",
    "generate_bottleneck_report",
]
