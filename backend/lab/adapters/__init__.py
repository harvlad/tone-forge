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
    from .kong_piano_adapter import KongPianoAdapter
    adapters[KongPianoAdapter.model_id] = KongPianoAdapter
    from .riley_adapter import RileyBassAdapter, RileyGuitarAdapter
    adapters[RileyGuitarAdapter.model_id] = RileyGuitarAdapter
    adapters[RileyBassAdapter.model_id] = RileyBassAdapter
    from .swiftf0_adapter import SwiftF0Adapter
    adapters[SwiftF0Adapter.model_id] = SwiftF0Adapter
    from .mt3_family_adapter import MRMT3Adapter, YourMT3Adapter
    adapters[YourMT3Adapter.model_id] = YourMT3Adapter
    adapters[MRMT3Adapter.model_id] = MRMT3Adapter
    from .octave_variants import RileyBassP12Adapter, SwiftF0P12Adapter
    adapters[RileyBassP12Adapter.model_id] = RileyBassP12Adapter
    adapters[SwiftF0P12Adapter.model_id] = SwiftF0P12Adapter
    return adapters


ADAPTERS = _load()


def get_adapter(model_id: str) -> ModelAdapter:
    if model_id not in ADAPTERS:
        raise KeyError(f"Unknown model '{model_id}'. Known: {sorted(ADAPTERS)}")
    return ADAPTERS[model_id]()
