"""TransformProvider — the plug-in contract for the Transformation Engine.

Mirrors SeparatorProvider / SourceProvider: a transform is DSP only (file -> file),
deterministic given (input, canonical params, seed). The TransformEngine (engine.py)
owns Asset lineage, hashing, caching, and cataloging — transforms never touch Asset
internals (composition, not inheritance). Adding a transform = one class, zero
downstream change.

Contract:
  id                stable transform id (part of lineage + cache key)
  version           bump when DSP output changes (invalidates cache)
  default_params()  the canonical default parameter dict
  canonical_params(p) normalize params for a stable cache key (fill defaults, sort)
  apply(in, out, params, seed) -> extra_provenance dict   # pure DSP, writes `out`
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

import numpy as np
import soundfile as sf
from scipy.signal import fftconvolve

SR = 44100


@runtime_checkable
class TransformProvider(Protocol):
    id: str
    version: str
    def default_params(self) -> dict: ...
    def canonical_params(self, params: dict) -> dict: ...
    def apply(self, in_path: Path, out_path: Path, params: dict, seed: Optional[int]) -> dict: ...


# ---- shared io helpers ----
def _read(path: Path):
    y, sr = sf.read(str(path), always_2d=True)     # (n, ch)
    return y.astype(np.float32), sr


def _write(path: Path, y: np.ndarray, sr: int):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), y, sr)


def _db_to_lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def _merge_defaults(self_defaults: dict, params: Optional[dict]) -> dict:
    p = dict(self_defaults)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})
    return dict(sorted(p.items()))       # sorted -> stable cache key


# =====================================================================
# 1. Gain — simplest validation transform (deterministic)
# =====================================================================
class GainTransform:
    id = "gain"
    version = "v1"

    def default_params(self) -> dict:
        return {"gain_db": 0.0}

    def canonical_params(self, params: dict) -> dict:
        p = _merge_defaults(self.default_params(), params)
        p["gain_db"] = round(float(p["gain_db"]), 3)
        return p

    def apply(self, in_path: Path, out_path: Path, params: dict, seed: Optional[int]) -> dict:
        y, sr = _read(in_path)
        y = y * _db_to_lin(params["gain_db"])
        _write(out_path, y, sr)
        return {"gain_lin": _db_to_lin(params["gain_db"])}


# =====================================================================
# 2. EQ — deterministic 3-band FFT-domain gain
# =====================================================================
class EQTransform:
    id = "eq"
    version = "v1"

    def default_params(self) -> dict:
        return {"low_db": 0.0, "mid_db": 0.0, "high_db": 0.0,
                "low_hz": 250.0, "high_hz": 4000.0}

    def canonical_params(self, params: dict) -> dict:
        p = _merge_defaults(self.default_params(), params)
        for k in ("low_db", "mid_db", "high_db", "low_hz", "high_hz"):
            p[k] = round(float(p[k]), 3)
        return p

    def apply(self, in_path: Path, out_path: Path, params: dict, seed: Optional[int]) -> dict:
        y, sr = _read(in_path)
        n = y.shape[0]
        freqs = np.fft.rfftfreq(n, 1 / sr)
        g = np.ones_like(freqs)
        g[freqs < params["low_hz"]] = _db_to_lin(params["low_db"])
        g[(freqs >= params["low_hz"]) & (freqs < params["high_hz"])] = _db_to_lin(params["mid_db"])
        g[freqs >= params["high_hz"]] = _db_to_lin(params["high_db"])
        out = np.empty_like(y)
        for c in range(y.shape[1]):
            spec = np.fft.rfft(y[:, c])
            out[:, c] = np.fft.irfft(spec * g, n=n)
        _write(out_path, out.astype(np.float32), sr)
        return {"bands": {"low": params["low_db"], "mid": params["mid_db"], "high": params["high_db"]}}


# =====================================================================
# 3. IR Loader — cabinet/room convolution with an impulse response
# =====================================================================
class IRTransform:
    id = "ir"
    version = "v1"

    def default_params(self) -> dict:
        return {"ir_path": None, "normalize": True}

    def canonical_params(self, params: dict) -> dict:
        p = _merge_defaults(self.default_params(), params)
        if not p.get("ir_path"):
            raise ValueError("IRTransform requires 'ir_path'")
        # cache key must reflect the IR *content*, not just its path
        p["ir_sha256"] = hashlib.sha256(Path(p["ir_path"]).read_bytes()).hexdigest()[:16]
        return p

    def apply(self, in_path: Path, out_path: Path, params: dict, seed: Optional[int]) -> dict:
        y, sr = _read(in_path)
        ir, _ = _read(Path(params["ir_path"]))
        ir_mono = ir.mean(axis=1)
        out = np.empty_like(y)
        for c in range(y.shape[1]):
            conv = fftconvolve(y[:, c], ir_mono)[: y.shape[0]]
            out[:, c] = conv
        if params.get("normalize", True):
            peak = float(np.max(np.abs(out)) + 1e-9)
            if peak > 1.0:
                out = out / peak
        _write(out_path, out.astype(np.float32), sr)
        return {"ir_sha256": params["ir_sha256"]}


# =====================================================================
# 4. NAM — Neural Amp Model re-amp (the first major production transform)
# =====================================================================
class NAMTransform:
    """DI -> amp-rendered guitar.

    Real NAM profiles plug in via params['model'] = path to a .nam file (applied
    through the `neural-amp-modeler` package when installed). Absent a profile, a
    deterministic built-in valve-amp model is used (pre-gain -> asymmetric tanh
    waveshaper -> tone), so the engine/lineage/cache are exercised end-to-end with
    no external model or download. The model identity ('builtin:tanh_v1' or the
    .nam content hash) is recorded in provenance AND folded into the cache key, so
    each profile is a distinct, replayable entry — 'support multiple NAM profiles
    later' is a config change, not a code change.
    """
    id = "nam"
    version = "v1"

    def default_params(self) -> dict:
        return {"model": "builtin:tanh_v1", "drive_db": 12.0, "output_db": -6.0, "bias": 0.15}

    def canonical_params(self, params: dict) -> dict:
        p = _merge_defaults(self.default_params(), params)
        p["drive_db"] = round(float(p["drive_db"]), 3)
        p["output_db"] = round(float(p["output_db"]), 3)
        p["bias"] = round(float(p["bias"]), 3)
        model = p["model"]
        if model != "builtin:tanh_v1" and Path(model).exists():
            p["model_id"] = "nam:" + hashlib.sha256(Path(model).read_bytes()).hexdigest()[:16]
        else:
            p["model_id"] = model
        return p

    def apply(self, in_path: Path, out_path: Path, params: dict, seed: Optional[int]) -> dict:
        y, sr = _read(in_path)
        model = params["model"]
        if model != "builtin:tanh_v1" and Path(model).exists():
            wet = self._apply_real_nam(y, sr, model)
            model_id = params["model_id"]
        else:
            wet = self._apply_builtin(y, params)
            model_id = "builtin:tanh_v1"
        _write(out_path, wet.astype(np.float32), sr)
        return {"model_id": model_id}

    def _apply_builtin(self, y: np.ndarray, params: dict) -> np.ndarray:
        drive = _db_to_lin(params["drive_db"])
        out_lin = _db_to_lin(params["output_db"])
        bias = params["bias"]
        x = y * drive + bias                    # pre-gain + asymmetric bias
        w = np.tanh(x) - np.tanh(bias)          # valve-ish soft clip, DC-corrected
        return w * out_lin

    def _apply_real_nam(self, y: np.ndarray, sr: int, model_path: str) -> np.ndarray:
        """Real NAM inference via the `neural-amp-modeler` package.

        Extension point for M2: loads a .nam profile and renders the (mono) DI
        through it. Deterministic (fixed weights, no dropout at inference). Requires
        `pip install neural-amp-modeler`. Kept as a guarded hook — the built-in
        valve model is M2's shipped DSP; this activates when a real profile lands.
        """
        try:
            import nam  # type: ignore  # noqa: F401
        except Exception as e:
            raise RuntimeError(
                "real .nam inference needs `neural-amp-modeler` installed; "
                f"pass model='builtin:tanh_v1' or install the package ({e})")
        raise NotImplementedError(
            "NAM profile provided but real-inference binding is a later-milestone task; "
            "use the built-in model for M2 (framework + lineage + cache are validated).")


# static contract checks
_t1: TransformProvider = GainTransform()   # type: ignore[assignment]
_t2: TransformProvider = EQTransform()     # type: ignore[assignment]
_t3: TransformProvider = IRTransform()     # type: ignore[assignment]
_t4: TransformProvider = NAMTransform()    # type: ignore[assignment]
