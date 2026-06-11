"""Memory tracking utilities for performance profiling.

Provides CPU memory monitoring and leak detection.
"""
from __future__ import annotations

import gc
import logging
import os
import threading
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import psutil

logger = logging.getLogger(__name__)


@dataclass
class MemorySnapshot:
    """Memory usage snapshot."""

    timestamp: float
    rss_mb: float  # Resident Set Size
    vms_mb: float  # Virtual Memory Size
    percent: float  # Memory percentage
    shared_mb: float = 0.0
    private_mb: float = 0.0

    # Python-specific
    python_allocated_mb: float = 0.0
    python_peak_mb: float = 0.0
    gc_objects: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "rss_mb": round(self.rss_mb, 2),
            "vms_mb": round(self.vms_mb, 2),
            "percent": round(self.percent, 2),
            "shared_mb": round(self.shared_mb, 2),
            "private_mb": round(self.private_mb, 2),
            "python_allocated_mb": round(self.python_allocated_mb, 2),
            "python_peak_mb": round(self.python_peak_mb, 2),
            "gc_objects": self.gc_objects,
        }


@dataclass
class MemoryAllocation:
    """Tracked memory allocation."""

    filename: str
    lineno: int
    size_bytes: int
    count: int

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno} - {self.size_mb:.2f} MB ({self.count} blocks)"


class MemoryTracker:
    """Track memory usage during pipeline execution.

    Provides:
    - Process memory monitoring
    - Python object tracking
    - Allocation hotspot detection
    - Memory leak detection
    """

    def __init__(self, enable_tracemalloc: bool = False):
        """Initialize memory tracker.

        Args:
            enable_tracemalloc: Enable detailed Python allocation tracking
                               (has performance overhead)
        """
        self._enable_tracemalloc = enable_tracemalloc
        self._snapshots: List[MemorySnapshot] = []
        self._baseline_snapshot: Optional[MemorySnapshot] = None
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._sample_interval = 0.5  # 500ms default
        self._lock = threading.Lock()
        self._process = psutil.Process(os.getpid())

        # Tracemalloc state
        self._tracemalloc_started = False

    def get_snapshot(self) -> MemorySnapshot:
        """Get current memory snapshot."""
        mem_info = self._process.memory_info()

        snapshot = MemorySnapshot(
            timestamp=time.time(),
            rss_mb=mem_info.rss / (1024 * 1024),
            vms_mb=mem_info.vms / (1024 * 1024),
            percent=self._process.memory_percent(),
            gc_objects=len(gc.get_objects()),
        )

        # Platform-specific extended info
        try:
            if hasattr(mem_info, 'shared'):
                snapshot.shared_mb = mem_info.shared / (1024 * 1024)
            if hasattr(mem_info, 'private'):
                snapshot.private_mb = mem_info.private / (1024 * 1024)
        except Exception:
            pass

        # Tracemalloc info
        if self._tracemalloc_started:
            try:
                current, peak = tracemalloc.get_traced_memory()
                snapshot.python_allocated_mb = current / (1024 * 1024)
                snapshot.python_peak_mb = peak / (1024 * 1024)
            except Exception:
                pass

        return snapshot

    def set_baseline(self) -> MemorySnapshot:
        """Set baseline memory snapshot for comparison.

        Returns:
            Baseline snapshot
        """
        gc.collect()
        self._baseline_snapshot = self.get_snapshot()
        return self._baseline_snapshot

    def get_delta(self, snapshot: Optional[MemorySnapshot] = None) -> Dict[str, float]:
        """Get memory delta from baseline.

        Args:
            snapshot: Snapshot to compare (or current if None)

        Returns:
            Delta dictionary
        """
        if snapshot is None:
            snapshot = self.get_snapshot()

        if self._baseline_snapshot is None:
            return {"rss_mb": 0, "vms_mb": 0, "percent": 0}

        return {
            "rss_mb": snapshot.rss_mb - self._baseline_snapshot.rss_mb,
            "vms_mb": snapshot.vms_mb - self._baseline_snapshot.vms_mb,
            "percent": snapshot.percent - self._baseline_snapshot.percent,
            "python_mb": (
                snapshot.python_allocated_mb - self._baseline_snapshot.python_allocated_mb
            ),
        }

    def start_monitoring(self, interval_sec: float = 0.5) -> None:
        """Start continuous memory monitoring.

        Args:
            interval_sec: Sampling interval in seconds
        """
        if self._monitoring:
            return

        self._sample_interval = interval_sec
        self._monitoring = True
        self._snapshots = []

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
        )
        self._monitor_thread.start()

    def stop_monitoring(self) -> List[MemorySnapshot]:
        """Stop monitoring and return collected snapshots.

        Returns:
            List of memory snapshots
        """
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None

        with self._lock:
            snapshots = list(self._snapshots)
            self._snapshots = []
        return snapshots

    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while self._monitoring:
            snapshot = self.get_snapshot()
            with self._lock:
                self._snapshots.append(snapshot)
            time.sleep(self._sample_interval)

    def start_allocation_tracking(self) -> None:
        """Start detailed Python allocation tracking.

        Note: This has performance overhead.
        """
        if not self._enable_tracemalloc:
            logger.warning("tracemalloc not enabled in constructor")
            return

        if not self._tracemalloc_started:
            tracemalloc.start()
            self._tracemalloc_started = True

    def stop_allocation_tracking(self) -> None:
        """Stop allocation tracking."""
        if self._tracemalloc_started:
            tracemalloc.stop()
            self._tracemalloc_started = False

    def get_top_allocations(self, limit: int = 10) -> List[MemoryAllocation]:
        """Get top memory allocations by size.

        Args:
            limit: Number of top allocations to return

        Returns:
            List of MemoryAllocation objects
        """
        if not self._tracemalloc_started:
            return []

        try:
            snapshot = tracemalloc.take_snapshot()
            stats = snapshot.statistics("lineno")

            allocations = []
            for stat in stats[:limit]:
                frame = stat.traceback[0]
                allocations.append(MemoryAllocation(
                    filename=frame.filename,
                    lineno=frame.lineno,
                    size_bytes=stat.size,
                    count=stat.count,
                ))

            return allocations
        except Exception as e:
            logger.warning(f"Failed to get allocations: {e}")
            return []

    def get_summary(
        self,
        snapshots: Optional[List[MemorySnapshot]] = None,
    ) -> Dict[str, Any]:
        """Get summary statistics from snapshots.

        Args:
            snapshots: Snapshots to summarize (or use internal)

        Returns:
            Summary dictionary
        """
        if snapshots is None:
            with self._lock:
                snapshots = list(self._snapshots)

        if not snapshots:
            current = self.get_snapshot()
            return {
                "current_rss_mb": current.rss_mb,
                "current_vms_mb": current.vms_mb,
                "sample_count": 0,
            }

        rss_values = [s.rss_mb for s in snapshots]
        vms_values = [s.vms_mb for s in snapshots]

        return {
            "sample_count": len(snapshots),
            "duration_sec": snapshots[-1].timestamp - snapshots[0].timestamp if len(snapshots) > 1 else 0,
            "rss": {
                "start_mb": rss_values[0],
                "end_mb": rss_values[-1],
                "min_mb": min(rss_values),
                "max_mb": max(rss_values),
                "avg_mb": sum(rss_values) / len(rss_values),
                "delta_mb": rss_values[-1] - rss_values[0],
            },
            "vms": {
                "start_mb": vms_values[0],
                "end_mb": vms_values[-1],
                "delta_mb": vms_values[-1] - vms_values[0],
            },
        }

    def detect_leak(
        self,
        snapshots: Optional[List[MemorySnapshot]] = None,
        threshold_mb_per_sec: float = 10.0,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Detect potential memory leak.

        Args:
            snapshots: Snapshots to analyze
            threshold_mb_per_sec: Leak threshold in MB/sec

        Returns:
            (is_leaking, analysis_dict)
        """
        if snapshots is None:
            with self._lock:
                snapshots = list(self._snapshots)

        if len(snapshots) < 3:
            return False, {"reason": "insufficient_samples"}

        rss_values = [s.rss_mb for s in snapshots]
        timestamps = [s.timestamp for s in snapshots]

        # Calculate growth rate
        duration = timestamps[-1] - timestamps[0]
        if duration <= 0:
            return False, {"reason": "no_duration"}

        growth_mb = rss_values[-1] - rss_values[0]
        growth_rate = growth_mb / duration

        # Check for monotonic increase
        increasing_segments = 0
        for i in range(1, len(rss_values)):
            if rss_values[i] > rss_values[i - 1]:
                increasing_segments += 1

        monotonic_ratio = increasing_segments / (len(rss_values) - 1)

        is_leaking = (
            growth_rate > threshold_mb_per_sec and
            monotonic_ratio > 0.7  # 70% of samples show increase
        )

        return is_leaking, {
            "growth_mb": round(growth_mb, 2),
            "duration_sec": round(duration, 2),
            "growth_rate_mb_per_sec": round(growth_rate, 3),
            "monotonic_ratio": round(monotonic_ratio, 3),
            "threshold_mb_per_sec": threshold_mb_per_sec,
        }

    def force_gc(self) -> Dict[str, int]:
        """Force garbage collection and return stats.

        Returns:
            GC statistics
        """
        before_objects = len(gc.get_objects())
        collected = gc.collect()
        after_objects = len(gc.get_objects())

        return {
            "collected": collected,
            "objects_before": before_objects,
            "objects_after": after_objects,
            "objects_freed": before_objects - after_objects,
        }


# Singleton instance
_memory_tracker: Optional[MemoryTracker] = None


def get_memory_tracker(enable_tracemalloc: bool = False) -> MemoryTracker:
    """Get the global memory tracker instance."""
    global _memory_tracker
    if _memory_tracker is None:
        _memory_tracker = MemoryTracker(enable_tracemalloc=enable_tracemalloc)
    return _memory_tracker
