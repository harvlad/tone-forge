"""Register-normalized adapter variants.

DISCOVERY (2026-07-26, Bass3 sanity): Slakh bass patches sound one octave
below written MIDI; models that report SOUNDING pitch (FiloBass, SwiftF0)
match Slakh GT almost exclusively at octave_delta = -12 (riley_bass:
575/576 octave matches at -12).  A deterministic +12 transpose converts
them into written-pitch space: riley_bass 0.000 -> 0.932 recall.

These variants run the SAME official inference as their parents and apply
a fixed, documented transpose recorded in inference_config.  Cache
entries may be derived from the parent's cached predictions (provenance
`derived:transpose+12`), which is exact because the transform is
deterministic on the output.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .riley_adapter import RileyBassAdapter
from .swiftf0_adapter import SwiftF0Adapter


class _TransposeMixin:
    OCTAVE_SHIFT = 12

    def inference_config(self) -> dict:
        cfg = super().inference_config()
        cfg["octave_shift"] = self.OCTAVE_SHIFT
        return cfg

    def predict(self, audio_path: Path) -> List[dict]:
        notes = super().predict(audio_path)
        for n in notes:
            n["pitch"] = int(n["pitch"]) + self.OCTAVE_SHIFT
        return notes


class RileyBassP12Adapter(_TransposeMixin, RileyBassAdapter):
    model_id = "riley_bass_p12"
    display_name = "Riley FiloBass +12 (written-pitch space)"


class SwiftF0P12Adapter(_TransposeMixin, SwiftF0Adapter):
    model_id = "swiftf0_p12"
    display_name = "SwiftF0 +12 (written-pitch space)"


def derive_cached(parent_model_id: str, child_model_id: str, stems) -> int:
    """Backfill the child's cache by transposing the parent's cached
    predictions (exact, no inference).  Returns number derived."""
    from .. import cache
    from . import get_adapter
    from ..runner import stem_prediction_key
    parent = get_adapter(parent_model_id)
    child = get_adapter(child_model_id)
    n = 0
    for _, row in stems.iterrows():
        pkey = stem_prediction_key(parent, row)
        ckey = stem_prediction_key(child, row)
        if cache.has(child.model_id, ckey) or not cache.has(parent.model_id, pkey):
            continue
        df = cache.load(parent.model_id, pkey).copy()
        df["pitch"] = df["pitch"] + child.OCTAVE_SHIFT
        pmeta = cache.load_meta(parent.model_id, pkey) or {}
        cache.store(child.model_id, ckey, df,
                    stem_id=row["stem_id"], audio_hash=row["audio_hash"],
                    model_version=child.model_version,
                    checkpoint_hash=child.checkpoint_hash(),
                    adapter_version=child.adapter_version,
                    inference_config=child.inference_config(),
                    runtime_seconds=pmeta.get("runtime_seconds"),
                    provenance=f"derived:transpose+{child.OCTAVE_SHIFT}:{parent.model_id}",
                    device=pmeta.get("device", ""))
        n += 1
    return n
