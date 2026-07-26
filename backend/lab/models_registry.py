"""Lightweight model registry (JSON).

Tracks candidate transcription models: identity, source, LICENSE
(commercial viability matters), requirements, lifecycle status.
"""
from __future__ import annotations

import datetime
import json
from typing import Optional

from . import config

VALID_STATUS = {
    "candidate", "smoke_failed", "scout_failed", "promising", "specialist",
    "generalist", "rejected", "production_candidate", "incumbent",
}


def _load() -> dict:
    if config.MODEL_REGISTRY_PATH.exists():
        return json.loads(config.MODEL_REGISTRY_PATH.read_text())
    return {"models": {}}


def _save(reg: dict) -> None:
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.MODEL_REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True))
    tmp.rename(config.MODEL_REGISTRY_PATH)


def upsert(model_id: str, **fields) -> dict:
    reg = _load()
    entry = reg["models"].get(model_id, {"model_id": model_id,
                                         "status": "candidate",
                                         "added_at": datetime.datetime.now(
                                             datetime.timezone.utc).isoformat()})
    status = fields.get("status")
    if status and status not in VALID_STATUS:
        raise ValueError(f"invalid status '{status}'; valid: {sorted(VALID_STATUS)}")
    entry.update(fields)
    entry["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reg["models"][model_id] = entry
    _save(reg)
    return entry


def get(model_id: str) -> Optional[dict]:
    return _load()["models"].get(model_id)


def list_models() -> list:
    return sorted(_load()["models"].values(), key=lambda m: m["model_id"])


def seed_defaults() -> None:
    """Register the two incumbents if absent."""
    if get("basic_pitch") is None:
        upsert("basic_pitch",
               display_name="Basic Pitch (ICASSP 2022)",
               source="https://github.com/spotify/basic-pitch",
               license="Apache-2.0",
               architecture="CNN (onset/frame/contour heads)",
               checkpoint="icassp_2022",
               polyphonic=True,
               supported_instruments="general (weak low register)",
               gpu_required=False,
               adapter="lab.adapters.basic_pitch_adapter.BasicPitchAdapter",
               status="incumbent",
               notes="Validated: F1 0.313 / recall 42.3% / octave err 16.3% "
                     "@100ms on validated100.")
    if get("mt3") is None:
        upsert("mt3",
               display_name="MT3 (mt3-infer PyTorch port)",
               source="https://github.com/magenta/mt3 (via mt3-infer)",
               license="Apache-2.0 (checkpoint via MR-MT3 HF mirror — verify)",
               architecture="T5 encoder-decoder seq2seq",
               checkpoint="mt3_pytorch",
               polyphonic=True,
               supported_instruments="multi-instrument; strong Bass, coarse timing (~60ms)",
               gpu_required=False,
               adapter="lab.adapters.mt3_adapter.MT3Adapter",
               status="specialist",
               notes="Validated: recall 11.0% global, 42.7% Bass, octave err 3.5% "
                     "@100ms on validated100.")
