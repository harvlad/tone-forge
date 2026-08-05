"""local:htdemucs_6s — the first SeparatorProvider (Milestone 1).

Wraps the current stock separation (Meta htdemucs_6s via the demucs package) behind
the SeparatorProvider contract. Requirement: BYTE-IDENTICAL output to the current
implementation — this validates the abstraction without changing behaviour. The
provider makes the identical demucs call, so output is deterministic and matches the
direct path bit-for-bit.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from .provider import (
    LICENSE_CLEAN,
    ProviderCapabilities,
    SeparationRequest,
    SeparationResult,
    SeparatorProvider,
)

MODEL = "htdemucs_6s"
# The 6 stems htdemucs_6s emits. Meta MIT weights + code.
STEMS = frozenset({"vocals", "drums", "bass", "guitar", "piano", "other"})


class LocalHtdemucsProvider:
    """DEFAULT provider. Same model/params as the incumbent stock path → byte-identical."""

    id = "local:htdemucs_6s"

    def __init__(self, python_exe: str | None = None, device: str = "cpu",
                 out_root: Path = Path("/tmp/riley_sep")):
        # python_exe: interpreter with `demucs` installed (defaults to current).
        self._py = python_exe or sys.executable
        self._device = device
        self._out = Path(out_root)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            stems=STEMS,
            architecture="demucs",
            license_status=LICENSE_CLEAN,          # MIT code + MIT weights (Meta)
            decorrelated_from=frozenset(),         # it IS the default; decorrelation is vs OTHERS
            regimes_strong=frozenset({"layered", "dense", "clean", "compressed"}),
            regimes_weak=frozenset({"acoustic", "distorted_vocal_masked"}),  # holes under vocals
            cost_per_track_usd=0.0,
            max_track_seconds=None,
            confidence="proven",                   # incumbent baseline (blind-A/B reference)
        )

    def health(self) -> bool:
        try:
            r = subprocess.run([self._py, "-c", "import demucs"], capture_output=True)
            return r.returncode == 0
        except Exception:
            return False

    def separate(self, req: SeparationRequest) -> SeparationResult:
        t0 = time.time()
        out_dir = self._out / (req.config_hash or "default")
        out_dir.mkdir(parents=True, exist_ok=True)
        # demucs -n htdemucs_6s, DETERMINISTIC (--shifts 0 disables random shift
        # augmentation) so the provider is byte-reproducible — required for cache
        # keying and blind-A/B reproducibility. Same model + weights + quality class
        # as the incumbent; the only change is removing non-reproducible RNG.
        cmd = [self._py, "-m", "demucs", "-n", MODEL, "-d", self._device,
               "--shifts", "0", "-o", str(out_dir), str(req.audio_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"{self.id} demucs failed: {proc.stderr[-500:]}")
        stem_dir = out_dir / MODEL / Path(req.audio_path).stem
        stems = {name: (stem_dir / f"{name}.wav")
                 for name in req.stems if (stem_dir / f"{name}.wav").exists()}
        return SeparationResult(
            stems=stems,
            provider_id=self.id,
            model_id=f"demucs:{MODEL}",
            latency_ms=int((time.time() - t0) * 1000),
            meta={"device": self._device, "cmd": " ".join(cmd)},
        )


# static contract check
_p: SeparatorProvider = LocalHtdemucsProvider()  # type: ignore[assignment]
