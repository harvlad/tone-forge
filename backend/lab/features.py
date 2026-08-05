"""Lazy audio feature cache.

Features compute on demand, persist to Parquet keyed on
(audio_hash, feature name, feature version, params).  Nothing is
precomputed corpus-wide.  Bump a feature's version when its
implementation changes — old entries become unreachable, never stale.

Register new features in FEATURES: name -> (version, fn(audio, sr, params) -> DataFrame).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from . import config, corpus
from .hashing import config_hash, short


def _feat_rms(audio: np.ndarray, sr: int, params: dict) -> pd.DataFrame:
    import librosa
    hop = int(params.get("hop_length", 512))
    rms = librosa.feature.rms(y=audio, hop_length=hop)[0]
    t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    return pd.DataFrame({"time": t, "rms": rms})


def _feat_spectral_centroid(audio: np.ndarray, sr: int, params: dict) -> pd.DataFrame:
    import librosa
    hop = int(params.get("hop_length", 512))
    sc = librosa.feature.spectral_centroid(y=audio, sr=sr, hop_length=hop)[0]
    t = librosa.frames_to_time(np.arange(len(sc)), sr=sr, hop_length=hop)
    return pd.DataFrame({"time": t, "spectral_centroid": sc})


FEATURES: Dict[str, Tuple[str, Callable]] = {
    "rms": ("v1", _feat_rms),
    "spectral_centroid": ("v1", _feat_spectral_centroid),
    # Add: subharmonic energy, harmonic support, register evidence, ... as
    # experiments need them.  version bump on implementation change.
}


def feature_path(name: str, version: str, audio_hash: str, params: dict) -> Path:
    ph = short(config_hash(params or {}), 12)
    return config.FEATURES_DIR / name / version / f"{audio_hash}_{ph}.parquet"


def get_feature(name: str, stem_row, params: Optional[dict] = None,
                sr: int = 22050) -> pd.DataFrame:
    if name not in FEATURES:
        raise KeyError(f"Unknown feature '{name}'. Known: {sorted(FEATURES)}")
    version, fn = FEATURES[name]
    params = params or {}
    path = feature_path(name, version, stem_row["audio_hash"], params)
    if path.exists():
        try:
            return pd.read_parquet(path)
        except Exception:
            path.unlink()
    import librosa
    audio, sr_ = librosa.load(str(corpus.resolve_audio(stem_row)), sr=sr, mono=True)
    df = fn(audio, sr_, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.rename(path)
    return df
