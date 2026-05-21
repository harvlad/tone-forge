"""macOS plugin scanner for VST3 and Audio Unit plugins.

Scans standard macOS plugin locations for:
- Audio Units (.component)
- VST3 plugins (.vst3)
- VST2 plugins (.vst) - legacy

Extracts plugin metadata including:
- Plugin name, manufacturer, version
- Plugin type (instrument, effect, etc.)
- Supported formats and categories
"""
from __future__ import annotations

import logging
import os
import plistlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

logger = logging.getLogger(__name__)

# Standard macOS plugin locations
AU_PATHS = [
    Path.home() / "Library" / "Audio" / "Plug-Ins" / "Components",
    Path("/Library/Audio/Plug-Ins/Components"),
]

VST3_PATHS = [
    Path.home() / "Library" / "Audio" / "Plug-Ins" / "VST3",
    Path("/Library/Audio/Plug-Ins/VST3"),
]

VST2_PATHS = [
    Path.home() / "Library" / "Audio" / "Plug-Ins" / "VST",
    Path("/Library/Audio/Plug-Ins/VST"),
]


@dataclass
class PluginInfo:
    """Information about a discovered plugin."""

    # Identity
    plugin_id: str               # Unique identifier
    name: str                    # Display name
    manufacturer: str            # Plugin manufacturer
    version: str                 # Version string

    # Location
    path: Path                   # Path to plugin bundle
    format: str                  # "au", "vst3", "vst2"

    # Type
    plugin_type: str             # "effect", "instrument", "midi_effect", "unknown"
    categories: List[str] = field(default_factory=list)  # E.g., ["Distortion", "Amp"]

    # Metadata
    description: str = ""
    website: str = ""

    # Technical details
    is_64bit: bool = True
    supports_mono: bool = True
    supports_stereo: bool = True

    # Timestamps
    modified_time: float = 0.0   # File modification time

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "version": self.version,
            "path": str(self.path),
            "format": self.format,
            "plugin_type": self.plugin_type,
            "categories": self.categories,
            "description": self.description,
            "website": self.website,
            "is_64bit": self.is_64bit,
            "supports_mono": self.supports_mono,
            "supports_stereo": self.supports_stereo,
            "modified_time": self.modified_time,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PluginInfo":
        """Create from dictionary."""
        d = d.copy()
        d["path"] = Path(d["path"])
        return cls(**d)


class MacOSPluginScanner:
    """Scanner for macOS audio plugins (AU and VST3)."""

    def __init__(
        self,
        scan_au: bool = True,
        scan_vst3: bool = True,
        scan_vst2: bool = False,  # Legacy, disabled by default
        custom_paths: Optional[List[Path]] = None,
    ):
        """Initialize the scanner.

        Args:
            scan_au: Whether to scan Audio Units
            scan_vst3: Whether to scan VST3 plugins
            scan_vst2: Whether to scan VST2 plugins (legacy)
            custom_paths: Additional paths to scan
        """
        self.scan_au = scan_au
        self.scan_vst3 = scan_vst3
        self.scan_vst2 = scan_vst2
        self.custom_paths = custom_paths or []

    def scan(self) -> List[PluginInfo]:
        """Scan for all plugins.

        Returns:
            List of discovered plugins
        """
        plugins = []

        if self.scan_au:
            plugins.extend(self._scan_audio_units())

        if self.scan_vst3:
            plugins.extend(self._scan_vst3())

        if self.scan_vst2:
            plugins.extend(self._scan_vst2())

        # Scan custom paths
        for path in self.custom_paths:
            if path.exists():
                plugins.extend(self._scan_directory(path))

        logger.info("Discovered %d plugins", len(plugins))
        return plugins

    def _scan_audio_units(self) -> List[PluginInfo]:
        """Scan for Audio Unit plugins."""
        plugins = []

        for au_path in AU_PATHS:
            if not au_path.exists():
                continue

            for component in au_path.glob("*.component"):
                try:
                    plugin = self._parse_audio_unit(component)
                    if plugin:
                        plugins.append(plugin)
                except Exception as e:
                    logger.warning("Failed to parse AU %s: %s", component, e)

        logger.info("Found %d Audio Units", len(plugins))
        return plugins

    def _parse_audio_unit(self, path: Path) -> Optional[PluginInfo]:
        """Parse an Audio Unit bundle."""
        info_plist = path / "Contents" / "Info.plist"

        if not info_plist.exists():
            return None

        try:
            with open(info_plist, "rb") as f:
                plist = plistlib.load(f)
        except Exception as e:
            logger.debug("Failed to read plist for %s: %s", path, e)
            return None

        # Extract basic info
        name = plist.get("CFBundleName", path.stem)
        manufacturer = plist.get("AudioUnit Manufacturer", "Unknown")

        # Try to get manufacturer from bundle identifier
        bundle_id = plist.get("CFBundleIdentifier", "")
        if manufacturer == "Unknown" and bundle_id:
            parts = bundle_id.split(".")
            if len(parts) >= 2:
                manufacturer = parts[1].title()

        version = plist.get("CFBundleShortVersionString", "1.0")

        # Determine plugin type from AudioComponents
        plugin_type = "effect"
        categories = []

        audio_components = plist.get("AudioComponents", [])
        if audio_components:
            component = audio_components[0]
            au_type = component.get("type", "")

            if au_type == "aumu":
                plugin_type = "instrument"
            elif au_type == "aumf":
                plugin_type = "midi_effect"
            elif au_type == "aufx":
                plugin_type = "effect"

            # Extract subtype for categorization
            subtype = component.get("subtype", "")
            if subtype:
                categories = self._categorize_from_subtype(subtype, name)

        # Generate unique ID
        plugin_id = f"au:{bundle_id}" if bundle_id else f"au:{path.stem}"

        return PluginInfo(
            plugin_id=plugin_id,
            name=name,
            manufacturer=manufacturer,
            version=version,
            path=path,
            format="au",
            plugin_type=plugin_type,
            categories=categories,
            modified_time=path.stat().st_mtime,
        )

    def _scan_vst3(self) -> List[PluginInfo]:
        """Scan for VST3 plugins."""
        plugins = []

        for vst3_path in VST3_PATHS:
            if not vst3_path.exists():
                continue

            for bundle in vst3_path.glob("*.vst3"):
                try:
                    plugin = self._parse_vst3(bundle)
                    if plugin:
                        plugins.append(plugin)
                except Exception as e:
                    logger.warning("Failed to parse VST3 %s: %s", bundle, e)

        logger.info("Found %d VST3 plugins", len(plugins))
        return plugins

    def _parse_vst3(self, path: Path) -> Optional[PluginInfo]:
        """Parse a VST3 bundle."""
        info_plist = path / "Contents" / "Info.plist"

        if not info_plist.exists():
            return None

        try:
            with open(info_plist, "rb") as f:
                plist = plistlib.load(f)
        except Exception as e:
            logger.debug("Failed to read plist for %s: %s", path, e)
            return None

        name = plist.get("CFBundleName", path.stem)
        manufacturer = plist.get("CFBundleGetInfoString", "")

        # Try to extract manufacturer from various fields
        if not manufacturer or manufacturer == name:
            bundle_id = plist.get("CFBundleIdentifier", "")
            if bundle_id:
                parts = bundle_id.split(".")
                if len(parts) >= 2:
                    manufacturer = parts[1].title()
            else:
                manufacturer = "Unknown"

        version = plist.get("CFBundleShortVersionString", "1.0")

        # Determine type and categories from name/path
        plugin_type = "effect"  # Default for VST3
        categories = self._categorize_from_name(name)

        if any(cat in categories for cat in ["Synth", "Instrument", "Generator"]):
            plugin_type = "instrument"

        bundle_id = plist.get("CFBundleIdentifier", "")
        plugin_id = f"vst3:{bundle_id}" if bundle_id else f"vst3:{path.stem}"

        return PluginInfo(
            plugin_id=plugin_id,
            name=name,
            manufacturer=manufacturer,
            version=version,
            path=path,
            format="vst3",
            plugin_type=plugin_type,
            categories=categories,
            modified_time=path.stat().st_mtime,
        )

    def _scan_vst2(self) -> List[PluginInfo]:
        """Scan for legacy VST2 plugins."""
        plugins = []

        for vst2_path in VST2_PATHS:
            if not vst2_path.exists():
                continue

            for bundle in vst2_path.glob("*.vst"):
                try:
                    plugin = self._parse_vst2(bundle)
                    if plugin:
                        plugins.append(plugin)
                except Exception as e:
                    logger.warning("Failed to parse VST2 %s: %s", bundle, e)

        logger.info("Found %d VST2 plugins", len(plugins))
        return plugins

    def _parse_vst2(self, path: Path) -> Optional[PluginInfo]:
        """Parse a VST2 bundle."""
        info_plist = path / "Contents" / "Info.plist"

        if not info_plist.exists():
            # VST2 might not have Info.plist, use filename
            return PluginInfo(
                plugin_id=f"vst2:{path.stem}",
                name=path.stem,
                manufacturer="Unknown",
                version="1.0",
                path=path,
                format="vst2",
                plugin_type="effect",
                categories=self._categorize_from_name(path.stem),
                modified_time=path.stat().st_mtime,
            )

        try:
            with open(info_plist, "rb") as f:
                plist = plistlib.load(f)
        except Exception:
            return None

        name = plist.get("CFBundleName", path.stem)
        bundle_id = plist.get("CFBundleIdentifier", "")
        version = plist.get("CFBundleShortVersionString", "1.0")

        manufacturer = "Unknown"
        if bundle_id:
            parts = bundle_id.split(".")
            if len(parts) >= 2:
                manufacturer = parts[1].title()

        return PluginInfo(
            plugin_id=f"vst2:{bundle_id}" if bundle_id else f"vst2:{path.stem}",
            name=name,
            manufacturer=manufacturer,
            version=version,
            path=path,
            format="vst2",
            plugin_type="effect",
            categories=self._categorize_from_name(name),
            modified_time=path.stat().st_mtime,
        )

    def _scan_directory(self, path: Path) -> List[PluginInfo]:
        """Scan a directory for plugins of any type."""
        plugins = []

        for component in path.glob("*.component"):
            plugin = self._parse_audio_unit(component)
            if plugin:
                plugins.append(plugin)

        for vst3 in path.glob("*.vst3"):
            plugin = self._parse_vst3(vst3)
            if plugin:
                plugins.append(plugin)

        for vst2 in path.glob("*.vst"):
            plugin = self._parse_vst2(vst2)
            if plugin:
                plugins.append(plugin)

        return plugins

    def _categorize_from_subtype(self, subtype: str, name: str) -> List[str]:
        """Categorize plugin from AU subtype and name."""
        categories = []

        # Common AU subtypes
        subtype_map = {
            "dist": ["Distortion"],
            "dlay": ["Delay"],
            "rvb2": ["Reverb"],
            "mcom": ["Compressor"],
            "lmtr": ["Limiter"],
            "pequ": ["EQ"],
            "hpas": ["Filter"],
            "lpas": ["Filter"],
            "bpas": ["Filter"],
            "dcmp": ["Compressor"],
            "nois": ["Noise"],
        }

        if subtype.lower() in subtype_map:
            categories.extend(subtype_map[subtype.lower()])

        # Also check name for categories
        categories.extend(self._categorize_from_name(name))

        return list(set(categories))

    def _categorize_from_name(self, name: str) -> List[str]:
        """Categorize plugin from its name."""
        categories = []
        name_lower = name.lower()

        # Effect categories
        category_keywords = {
            "Amp": ["amp", "amplifier", "preamp"],
            "Cabinet": ["cabinet", "cab", "ir", "impulse"],
            "Distortion": ["distortion", "overdrive", "fuzz", "drive", "saturation"],
            "Delay": ["delay", "echo"],
            "Reverb": ["reverb", "verb", "room", "hall", "plate"],
            "Chorus": ["chorus"],
            "Flanger": ["flanger"],
            "Phaser": ["phaser", "phase"],
            "Modulation": ["modulation", "mod", "tremolo", "vibrato"],
            "EQ": ["eq", "equalizer", "equaliser"],
            "Compressor": ["compressor", "comp", "limiter", "dynamics"],
            "Filter": ["filter", "wah"],
            "Pitch": ["pitch", "harmony", "harmonizer"],
            "Noise Gate": ["gate", "noise"],
            "Synth": ["synth", "synthesizer"],
        }

        for category, keywords in category_keywords.items():
            if any(kw in name_lower for kw in keywords):
                categories.append(category)

        return categories


def scan_plugins(
    scan_au: bool = True,
    scan_vst3: bool = True,
    scan_vst2: bool = False,
) -> List[PluginInfo]:
    """Convenience function to scan for plugins.

    Args:
        scan_au: Whether to scan Audio Units
        scan_vst3: Whether to scan VST3 plugins
        scan_vst2: Whether to scan VST2 plugins

    Returns:
        List of discovered plugins
    """
    scanner = MacOSPluginScanner(
        scan_au=scan_au,
        scan_vst3=scan_vst3,
        scan_vst2=scan_vst2,
    )
    return scanner.scan()


def get_plugin_paths() -> Dict[str, List[Path]]:
    """Get standard plugin paths for macOS.

    Returns:
        Dictionary mapping format to list of paths
    """
    return {
        "au": AU_PATHS,
        "vst3": VST3_PATHS,
        "vst2": VST2_PATHS,
    }
