"""Model adapters.  Register new models here.

Adding a model = write one adapter module + add it to ADAPTERS + register
it in the model registry (`lab models add`).  Nothing else.
"""
from __future__ import annotations

from typing import Dict, Type

from .base import ModelAdapter


def _load() -> Dict[str, Type[ModelAdapter]]:
    # Imports kept lazy-tolerant: an adapter whose deps are missing must not
    # break the rest of the lab (analysis works without any model installed).
    adapters: Dict[str, Type[ModelAdapter]] = {}
    from .basic_pitch_adapter import BasicPitchAdapter
    adapters[BasicPitchAdapter.model_id] = BasicPitchAdapter
    from .mt3_adapter import MT3Adapter
    adapters[MT3Adapter.model_id] = MT3Adapter
    return adapters


ADAPTERS = _load()


def get_adapter(model_id: str) -> ModelAdapter:
    if model_id not in ADAPTERS:
        raise KeyError(f"Unknown model '{model_id}'. Known: {sorted(ADAPTERS)}")
    return ADAPTERS[model_id]()
