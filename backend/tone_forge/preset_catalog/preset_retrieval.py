"""Preset catalog retrieval — match input audio to the 99-preset catalog.

Loads the rendered preset catalog (``catalog_<instrument>.json``), computes
the same 8-feature fingerprint on an input audio clip, and returns the
top-k closest presets by Euclidean distance in that feature space.

The fingerprint extraction reuses ``catalog_builder.extract_preset_fingerprint``
so input and catalog fingerprints are guaranteed identical schema. The
distance function comes from ``PresetCatalog.find_similar``.

This is the integration point between the analysis pipeline and the
99-preset Analog catalog produced by Preset Rendering Pipeline v2.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from .catalog_builder import PresetCatalog, extract_preset_fingerprint
from .preset_discovery import PresetInfo


# Default location of the rendered catalog directory.
_DEFAULT_CATALOG_DIR = (
    Path(__file__).resolve().parents[2] / "preset_catalog_output" / "catalog"
)


@lru_cache(maxsize=8)
def _load_catalog(instrument: str, catalog_dir: str) -> PresetCatalog:
    """Load and cache a catalog by instrument name."""
    path = Path(catalog_dir) / f"catalog_{instrument.lower()}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Preset catalog not found for instrument '{instrument}': {path}"
        )
    return PresetCatalog.load(path)


def match_audio_file(
    audio_path: Path,
    k: int = 5,
    instrument: str = "Analog",
    sound_type_filter: Optional[str] = None,
    catalog_dir: Optional[Path] = None,
) -> List[Dict]:
    """Match an audio clip against the preset catalog.

    Args:
        audio_path: Path to the input WAV/MP3/etc.
        k: Number of top matches to return.
        instrument: Catalog instrument to query (default ``Analog``).
        sound_type_filter: Optional sound_type filter (``bass``, ``lead``,
            ``pad``, ``keys``, ``fx``, ``percussion``, ``other``).
        catalog_dir: Override the catalog directory. Defaults to
            ``backend/preset_catalog_output/catalog``.

    Returns:
        List of match dicts in descending similarity order:
            {
                "preset_id": str,
                "preset_name": str,
                "instrument": str,
                "category": str,
                "sound_type": str,
                "distance": float,
                "audio_path": str | None,
                "preset_path": str | None,
                "features": {...},
            }
    """
    catalog_dir_str = str((catalog_dir or _DEFAULT_CATALOG_DIR).resolve())
    catalog = _load_catalog(instrument, catalog_dir_str)

    # Construct a minimal PresetInfo for the input clip so the existing
    # extractor's signature is honoured. The metadata fields are placeholders;
    # the only thing that matters for retrieval is the feature vector.
    query_info = PresetInfo(
        preset_id="__query__",
        name=audio_path.stem,
        instrument=instrument,
        category="",
        sound_type=sound_type_filter or "",
        path=audio_path,
        source="query",
    )

    query_fp = extract_preset_fingerprint(audio_path, query_info)

    # Optional sound_type pre-filter so the nearest-neighbour search runs
    # only within a category (e.g. find the closest *bass* preset).
    if sound_type_filter:
        filtered = PresetCatalog(version=catalog.version)
        for p in catalog.presets:
            if p.sound_type == sound_type_filter:
                filtered.add(p)
        results = filtered.find_similar(query_fp, k=k)
    else:
        results = catalog.find_similar(query_fp, k=k)

    matches: List[Dict] = []
    for preset, distance in results:
        matches.append({
            "preset_id": preset.preset_id,
            "preset_name": preset.preset_name,
            "instrument": preset.instrument,
            "category": preset.category,
            "sound_type": preset.sound_type,
            "distance": float(distance),
            "audio_path": preset.audio_path,
            "preset_path": preset.preset_path,
            "features": {
                "brightness": preset.brightness,
                "warmth": preset.warmth,
                "air": preset.air,
                "attack_ms": preset.attack_ms,
                "decay_ms": preset.decay_ms,
                "sustain_ratio": preset.sustain_ratio,
                "harmonic_ratio": preset.harmonic_ratio,
                "pitch_stability": preset.pitch_stability,
            },
        })

    return matches
