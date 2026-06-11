"""GPU monitoring utilities for performance profiling.

Provides MPS (Apple Silicon) and CUDA GPU monitoring capabilities.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class GPUStats:
    """GPU statistics snapshot."""

    timestamp: float
    memory_allocated_mb: float = 0.0
    memory_reserved_mb: float = 0.0
    memory_peak_mb: float = 0.0
    utilization_percent: float = 0.0
    temperature_c: float = 0.0
    power_watts: float = 0.0
    device_name: str = ""
    backend: str = ""  # "cuda", "mps", or "cpu"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "memory_allocated_mb": round(self.memory_allocated_mb, 2),
            "memory_reserved_mb": round(self.memory_reserved_mb, 2),
            "memory_peak_mb": round(self.memory_peak_mb, 2),
            "utilization_percent": round(self.utilization_percent, 2),
            "temperature_c": round(self.temperature_c, 1),
            "power_watts": round(self.power_watts, 1),
            "device_name": self.device_name,
            "backend": self.backend,
        }


class GPUMonitor:
    """Monitor GPU usage during pipeline execution.

    Supports both CUDA and MPS (Apple Silicon) backends.
    Provides continuous monitoring or point-in-time snapshots.
    """

    def __init__(self):
        """Initialize the GPU monitor."""
        self._backend = self._detect_backend()
        self._device_name = self._get_device_name()
        self._samples: List[GPUStats] = []
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._sample_interval = 0.1  # 100ms default
        self._lock = threading.Lock()

    def _detect_backend(self) -> str:
        """Detect available GPU backend."""
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            elif torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _get_device_name(self) -> str:
        """Get GPU device name."""
        if self._backend == "cuda":
            try:
                import torch
                return torch.cuda.get_device_name(0)
            except Exception:
                return "CUDA Device"
        elif self._backend == "mps":
            return "Apple Silicon GPU"
        return "CPU"

    @property
    def backend(self) -> str:
        """Get the active GPU backend."""
        return self._backend

    @property
    def device_name(self) -> str:
        """Get the GPU device name."""
        return self._device_name

    @property
    def is_gpu_available(self) -> bool:
        """Check if GPU is available."""
        return self._backend in ("cuda", "mps")

    def get_stats(self) -> GPUStats:
        """Get current GPU statistics."""
        stats = GPUStats(
            timestamp=time.time(),
            device_name=self._device_name,
            backend=self._backend,
        )

        if self._backend == "cuda":
            stats = self._get_cuda_stats(stats)
        elif self._backend == "mps":
            stats = self._get_mps_stats(stats)

        return stats

    def _get_cuda_stats(self, stats: GPUStats) -> GPUStats:
        """Get CUDA GPU statistics."""
        try:
            import torch

            stats.memory_allocated_mb = torch.cuda.memory_allocated() / (1024 * 1024)
            stats.memory_reserved_mb = torch.cuda.memory_reserved() / (1024 * 1024)
            stats.memory_peak_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)

            # Try to get utilization via nvidia-smi (if available)
            try:
                import subprocess
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,power.draw",
                     "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if result.returncode == 0:
                    parts = result.stdout.strip().split(",")
                    if len(parts) >= 3:
                        stats.utilization_percent = float(parts[0].strip())
                        stats.temperature_c = float(parts[1].strip())
                        stats.power_watts = float(parts[2].strip())
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"Failed to get CUDA stats: {e}")

        return stats

    def _get_mps_stats(self, stats: GPUStats) -> GPUStats:
        """Get MPS (Apple Silicon) GPU statistics."""
        try:
            import torch

            # MPS has limited memory stats
            stats.memory_allocated_mb = torch.mps.driver_allocated_memory() / (1024 * 1024)
            stats.memory_reserved_mb = torch.mps.current_allocated_memory() / (1024 * 1024)

            # Try to get GPU utilization via powermetrics (requires sudo)
            # This is expensive, so we only do it if explicitly requested
            # For now, we'll estimate based on memory pressure

        except Exception as e:
            logger.debug(f"Failed to get MPS stats: {e}")

        return stats

    def start_monitoring(self, interval_sec: float = 0.1) -> None:
        """Start continuous GPU monitoring in background thread.

        Args:
            interval_sec: Sampling interval in seconds
        """
        if self._monitoring:
            return

        self._sample_interval = interval_sec
        self._monitoring = True
        self._samples = []

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
        )
        self._monitor_thread.start()

    def stop_monitoring(self) -> List[GPUStats]:
        """Stop monitoring and return collected samples.

        Returns:
            List of GPU statistics samples
        """
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None

        with self._lock:
            samples = list(self._samples)
            self._samples = []
        return samples

    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while self._monitoring:
            stats = self.get_stats()
            with self._lock:
                self._samples.append(stats)
            time.sleep(self._sample_interval)

    def get_summary(self, samples: Optional[List[GPUStats]] = None) -> Dict[str, Any]:
        """Get summary statistics from samples.

        Args:
            samples: Samples to summarize (or use internal samples)

        Returns:
            Summary dictionary
        """
        if samples is None:
            with self._lock:
                samples = list(self._samples)

        if not samples:
            return {
                "backend": self._backend,
                "device_name": self._device_name,
                "sample_count": 0,
            }

        memory_allocated = [s.memory_allocated_mb for s in samples]
        memory_peak = [s.memory_peak_mb for s in samples]
        utilization = [s.utilization_percent for s in samples if s.utilization_percent > 0]

        return {
            "backend": self._backend,
            "device_name": self._device_name,
            "sample_count": len(samples),
            "duration_sec": samples[-1].timestamp - samples[0].timestamp if len(samples) > 1 else 0,
            "memory": {
                "avg_allocated_mb": sum(memory_allocated) / len(memory_allocated),
                "max_allocated_mb": max(memory_allocated),
                "max_peak_mb": max(memory_peak),
            },
            "utilization": {
                "avg_percent": sum(utilization) / len(utilization) if utilization else 0,
                "max_percent": max(utilization) if utilization else 0,
            },
        }

    def reset_peak_memory(self) -> None:
        """Reset GPU peak memory tracking."""
        if self._backend == "cuda":
            try:
                import torch
                torch.cuda.reset_peak_memory_stats()
            except Exception:
                pass

    def synchronize(self) -> None:
        """Synchronize GPU operations (wait for completion)."""
        try:
            import torch
            if self._backend == "cuda":
                torch.cuda.synchronize()
            elif self._backend == "mps":
                torch.mps.synchronize()
        except Exception:
            pass

    def empty_cache(self) -> None:
        """Empty GPU memory cache."""
        try:
            import torch
            if self._backend == "cuda":
                torch.cuda.empty_cache()
            elif self._backend == "mps":
                torch.mps.empty_cache()
        except Exception:
            pass


# Singleton instance
_gpu_monitor: Optional[GPUMonitor] = None


def get_gpu_monitor() -> GPUMonitor:
    """Get the global GPU monitor instance."""
    global _gpu_monitor
    if _gpu_monitor is None:
        _gpu_monitor = GPUMonitor()
    return _gpu_monitor
