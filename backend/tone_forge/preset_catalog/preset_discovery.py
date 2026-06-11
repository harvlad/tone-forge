"""Preset discovery for Ableton instruments.

Discovers presets from:
- Ableton Core Library
- Factory Packs
- User Library

Supports: Analog, Operator, Wavetable, Drift, Meld
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional
import glob

logger = logging.getLogger(__name__)


# Matches anything we don't want in a filename component. Keep ASCII
# alphanumerics, hyphen, underscore, dot. Everything else collapses
# to an underscore.
_SAFE_NAME_BAD = re.compile(r"[^A-Za-z0-9._-]+")
# Collapses runs of underscores produced by the above substitution.
_SAFE_NAME_UNDERSCORES = re.compile(r"_+")


def safe_filename(preset_id: str) -> str:
    """Return a filesystem-safe stem derived from a preset_id.

    The previous implementation was triplicated across the catalog
    builder, ALS generator, and render-instructions writer as:

        preset_id.replace("/", "_").replace(" ", "_")

    That left dots, parentheses, apostrophes, commas, ampersands,
    colons, and Unicode accents untouched — every one a latent
    collision risk (e.g. ``"Bass A"`` and ``"Bass-A"`` already
    collide because PresetDiscovery folds hyphens to underscores,
    and ``"TR-808 Kick.1"`` vs ``"TR_808 Kick(1)"`` would collide
    after sanitisation).

    This helper:
      1. Normalises Unicode via NFKD and strips combining marks
         (``café`` → ``cafe``).
      2. Replaces any non-``[A-Za-z0-9._-]`` run with a single
         underscore.
      3. Collapses consecutive underscores.
      4. Trims leading / trailing ``._-`` (so the stem never starts
         with a dot — that would create a hidden file on POSIX).

    The output is deterministic; pass the same ``preset_id`` and you
    get the same stem. Collision detection across a *set* of preset
    ids is delegated to :func:`detect_safe_filename_collisions`.
    """
    if not preset_id:
        raise ValueError("safe_filename() called with empty preset_id")
    # 1. Unicode normalisation — fold accents into ASCII when possible.
    normalised = unicodedata.normalize("NFKD", preset_id)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    # 2. Replace illegal-character runs with a single underscore.
    cleaned = _SAFE_NAME_BAD.sub("_", ascii_only)
    # 3. Collapse runs of underscores produced by step 2.
    cleaned = _SAFE_NAME_UNDERSCORES.sub("_", cleaned)
    # 4. Trim leading / trailing punctuation.
    cleaned = cleaned.strip("._-")
    if not cleaned:
        # Pathological input (all-punctuation preset_id). Caller's
        # responsibility, but don't silently return ``""``.
        raise ValueError(
            f"safe_filename({preset_id!r}) produced an empty stem; "
            "preset_id is composed entirely of unsafe characters."
        )
    return cleaned


def detect_safe_filename_collisions(
    preset_ids: Iterable[str],
) -> Dict[str, List[str]]:
    """Return ``{stem: [colliding preset_ids]}`` for stems shared by 2+ ids.

    Used by the catalog builder before any ALS / WAV files are written
    to fail-loud on name collisions rather than silently overwriting.
    """
    by_stem: Dict[str, List[str]] = defaultdict(list)
    for pid in preset_ids:
        by_stem[safe_filename(pid)].append(pid)
    return {stem: ids for stem, ids in by_stem.items() if len(ids) >= 2}

# Ableton installation patterns
ABLETON_APP_PATTERNS = [
    "/Applications/Ableton Live * Suite.app",
    "/Applications/Ableton Live * Standard.app",
    "/Applications/Ableton Live * Lite.app",
    "/Applications/Ableton Live *.app",
]

# Standard paths
USER_LIBRARY = Path.home() / "Music" / "Ableton" / "User Library"
FACTORY_PACKS = Path.home() / "Music" / "Ableton" / "Factory Packs"

# Supported instruments. Must agree with the DEVICE_CONFIG dict in
# preset_als_generator.py — discovering a preset for an instrument that
# has no schema entry would surface as an AdvEmbedError at ALS-generate
# time. Electric, Tension, Collision were added in Phase 2 after
# empirical schema discovery (scripts/discover_device_schema.py).
SUPPORTED_INSTRUMENTS = [
    "Analog",
    "Operator",
    "Wavetable",
    "Drift",
    "Meld",
    "Electric",
    "Tension",
    "Collision",
]

# Category mappings for sound type inference
CATEGORY_SOUND_TYPE = {
    "Bass": "bass",
    "Synth Bass": "bass",
    "Synth Lead": "lead",
    "Lead": "lead",
    "Synth Pad": "pad",
    "Pad": "pad",
    "Synth Keys": "keys",
    "Piano & Keys": "keys",
    "Keys": "keys",
    "Brass": "lead",
    "Strings": "pad",
    "Synth Percussion": "percussion",
    "Percussion": "percussion",
    "Effects": "fx",
    "FX": "fx",
    "Plucks": "pluck",
    "Synth Pluck": "pluck",
    "Arp": "arp",
    "Sequence": "arp",
}


@dataclass
class PresetInfo:
    """Information about a discovered preset."""

    preset_id: str                  # Unique identifier
    name: str                       # Display name
    instrument: str                 # Analog, Operator, etc.
    category: str                   # Folder category (Bass, Lead, etc.)
    sound_type: str                 # Inferred type (bass, lead, pad, etc.)
    path: Path                      # Full path to .adv file
    source: str                     # "core", "pack", "user"
    pack_name: Optional[str] = None # Pack name if from Factory Packs

    def to_dict(self) -> dict:
        return {
            "preset_id": self.preset_id,
            "name": self.name,
            "instrument": self.instrument,
            "category": self.category,
            "sound_type": self.sound_type,
            "path": str(self.path),
            "source": self.source,
            "pack_name": self.pack_name,
        }


class PresetDiscovery:
    """Discovers Ableton instrument presets."""

    def __init__(self):
        self.presets: List[PresetInfo] = []
        self._ableton_path: Optional[Path] = None
        self._core_library: Optional[Path] = None

    def find_ableton_installation(self) -> Optional[Path]:
        """Find Ableton Live installation."""
        for pattern in ABLETON_APP_PATTERNS:
            matches = glob.glob(pattern)
            if matches:
                matches.sort(reverse=True)  # Newest version first
                app_path = Path(matches[0])
                if app_path.exists():
                    self._ableton_path = app_path
                    self._core_library = (
                        app_path / "Contents" / "App-Resources" / "Core Library"
                    )
                    logger.info(f"Found Ableton at {app_path}")
                    return app_path

        logger.warning("Ableton Live installation not found")
        return None

    def discover_all(
        self,
        instruments: Optional[List[str]] = None,
        include_packs: bool = True,
        include_user: bool = False,
    ) -> List[PresetInfo]:
        """Discover all presets for specified instruments.

        Args:
            instruments: List of instruments to scan (default: all supported)
            include_packs: Include Factory Packs presets
            include_user: Include User Library presets

        Returns:
            List of PresetInfo objects
        """
        self.presets = []

        if not self.find_ableton_installation():
            return self.presets

        instruments = instruments or SUPPORTED_INSTRUMENTS

        for instrument in instruments:
            # Core Library
            self._discover_core_library(instrument)

            # Factory Packs
            if include_packs:
                self._discover_factory_packs(instrument)

            # User Library
            if include_user:
                self._discover_user_library(instrument)

        logger.info(f"Discovered {len(self.presets)} presets")
        return self.presets

    def _discover_core_library(self, instrument: str) -> None:
        """Discover presets from Core Library."""
        if not self._core_library:
            return

        instrument_path = self._core_library / "Devices" / "Instruments" / instrument
        if not instrument_path.exists():
            return

        for preset_path in instrument_path.rglob("*.adv"):
            # Skip info folders
            if "Ableton Folder Info" in str(preset_path):
                continue

            preset = self._create_preset_info(
                preset_path, instrument, source="core"
            )
            self.presets.append(preset)

    def _discover_factory_packs(self, instrument: str) -> None:
        """Discover presets from Factory Packs."""
        if not FACTORY_PACKS.exists():
            return

        for pack_path in FACTORY_PACKS.iterdir():
            if not pack_path.is_dir():
                continue

            pack_name = pack_path.name

            # Search for instrument presets in this pack
            for preset_path in pack_path.rglob("*.adv"):
                # Check if this is for the target instrument
                if f"/{instrument}/" not in str(preset_path):
                    continue

                # Skip info folders
                if "Ableton Folder Info" in str(preset_path):
                    continue

                preset = self._create_preset_info(
                    preset_path, instrument, source="pack", pack_name=pack_name
                )
                self.presets.append(preset)

    def _discover_user_library(self, instrument: str) -> None:
        """Discover presets from User Library."""
        user_presets = USER_LIBRARY / "Presets" / "Instruments" / instrument
        if not user_presets.exists():
            return

        for preset_path in user_presets.rglob("*.adv"):
            preset = self._create_preset_info(
                preset_path, instrument, source="user"
            )
            self.presets.append(preset)

    def _create_preset_info(
        self,
        preset_path: Path,
        instrument: str,
        source: str,
        pack_name: Optional[str] = None,
    ) -> PresetInfo:
        """Create PresetInfo from a preset path."""
        name = preset_path.stem

        # Extract category from parent folder
        category = preset_path.parent.name
        if category == instrument:
            category = "Uncategorized"

        # Infer sound type from category
        sound_type = CATEGORY_SOUND_TYPE.get(category, "other")

        # Create unique ID
        preset_id = f"{instrument.lower()}_{name.lower().replace(' ', '_').replace('-', '_')}"
        if pack_name:
            preset_id = f"{pack_name.lower().replace(' ', '_')}_{preset_id}"

        return PresetInfo(
            preset_id=preset_id,
            name=name,
            instrument=instrument,
            category=category,
            sound_type=sound_type,
            path=preset_path,
            source=source,
            pack_name=pack_name,
        )

    def get_by_instrument(self, instrument: str) -> List[PresetInfo]:
        """Get presets filtered by instrument."""
        return [p for p in self.presets if p.instrument == instrument]

    def get_by_sound_type(self, sound_type: str) -> List[PresetInfo]:
        """Get presets filtered by sound type."""
        return [p for p in self.presets if p.sound_type == sound_type]

    def get_by_source(self, source: str) -> List[PresetInfo]:
        """Get presets filtered by source."""
        return [p for p in self.presets if p.source == source]


def discover_presets(
    instruments: Optional[List[str]] = None,
    include_packs: bool = True,
) -> List[PresetInfo]:
    """Convenience function to discover presets.

    Args:
        instruments: List of instruments (default: all)
        include_packs: Include Factory Packs

    Returns:
        List of PresetInfo
    """
    discovery = PresetDiscovery()
    return discovery.discover_all(instruments, include_packs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    presets = discover_presets(["Analog"])

    print(f"\nDiscovered {len(presets)} Analog presets:\n")

    by_category: Dict[str, List[PresetInfo]] = {}
    for p in presets:
        by_category.setdefault(p.category, []).append(p)

    for category, cat_presets in sorted(by_category.items()):
        print(f"\n{category} ({len(cat_presets)}):")
        for p in cat_presets[:5]:
            source = f" [{p.pack_name}]" if p.pack_name else ""
            print(f"  - {p.name}{source}")
        if len(cat_presets) > 5:
            print(f"  ... and {len(cat_presets) - 5} more")
