"""Ableton Live device scanner.

Scans Ableton Live installation for built-in devices:
- Instruments (Wavetable, Drift, Analog, Operator, etc.)
- Audio Effects (Amp, Cabinet, Pedal, Saturator, etc.)
- MIDI Effects

Also scans User Library for presets and racks.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any
import glob

logger = logging.getLogger(__name__)

# Ableton Live installation patterns
ABLETON_APP_PATTERNS = [
    "/Applications/Ableton Live * Suite.app",
    "/Applications/Ableton Live * Standard.app",
    "/Applications/Ableton Live * Lite.app",
    "/Applications/Ableton Live *.app",
]

# User Library location
USER_LIBRARY = Path.home() / "Music" / "Ableton" / "User Library"

# Device type mappings for ToneForge block families
DEVICE_TO_BLOCK_FAMILY = {
    # Amp simulation devices
    "Amp": "amp_generic",
    "Cabinet": "cab_generic",
    "Pedal": "overdrive_generic",

    # Distortion/saturation
    "Saturator": "distortion_generic",
    "Overdrive": "overdrive_generic",
    "Dynamic Tube": "tube_preamp",
    "Roar": "distortion_generic",
    "Drum Buss": "compression_generic",

    # EQ
    "EQ Eight": "eq_parametric",
    "EQ Three": "eq_3band",
    "Channel EQ": "eq_channel",

    # Compression
    "Compressor": "compression_generic",
    "Glue Compressor": "compression_glue",
    "Multiband Dynamics": "compression_multiband",
    "Limiter": "limiter",

    # Reverb
    "Reverb": "reverb_generic",
    "Hybrid Reverb": "reverb_hybrid",

    # Delay
    "Delay": "delay_generic",
    "Echo": "delay_tape",
    "Filter Delay": "delay_filter",
    "Grain Delay": "delay_granular",

    # Modulation
    "Chorus-Ensemble": "chorus",
    "Phaser-Flanger": "phaser",
    "Auto Filter": "filter_auto",
    "Auto Pan": "tremolo",

    # Special
    "Redux": "bitcrusher",
    "Vinyl Distortion": "lofi",
    "Vocoder": "vocoder",
    "Shifter": "pitch_shifter",

    # Instruments
    "Wavetable": "synth_wavetable",
    "Drift": "synth_analog",
    "Analog": "synth_analog",
    "Operator": "synth_fm",
    "Tension": "synth_physical",
    "Collision": "synth_physical",
    "Electric": "synth_electric_piano",
    "Sampler": "sampler",
    "Simpler": "sampler_simple",
    "Impulse": "drum_sampler",
    "Meld": "synth_hybrid",
}


@dataclass
class AbletonDeviceInfo:
    """Information about an Ableton device."""

    device_id: str               # Unique identifier
    name: str                    # Display name
    device_type: str             # "instrument", "audio_effect", "midi_effect"
    categories: List[str] = field(default_factory=list)
    block_family: str = ""       # Mapped ToneForge block family

    # Location
    path: Path = None            # Path to device folder
    is_builtin: bool = True      # Built-in vs User Library

    # Ableton version
    live_version: str = ""       # e.g., "12"

    def to_plugin_dict(self) -> Dict[str, Any]:
        """Convert to plugin-compatible dictionary for database storage."""
        return {
            "plugin_id": self.device_id,
            "name": self.name,
            "manufacturer": "Ableton",
            "version": self.live_version,
            "path": str(self.path) if self.path else "",
            "format": "ableton_device",
            "plugin_type": self.device_type,
            "categories": self.categories,
            "description": f"Ableton Live {self.device_type.replace('_', ' ').title()}",
            "website": "https://www.ableton.com",
            "is_64bit": True,
            "supports_mono": True,
            "supports_stereo": True,
            "modified_time": 0.0,
            "block_family": self.block_family,
        }


class AbletonScanner:
    """Scanner for Ableton Live devices."""

    def __init__(self):
        self.devices: List[AbletonDeviceInfo] = []
        self._live_path: Optional[Path] = None
        self._live_version: str = ""

    def find_ableton_installation(self) -> Optional[Path]:
        """Find Ableton Live installation."""
        for pattern in ABLETON_APP_PATTERNS:
            matches = glob.glob(pattern)
            if matches:
                # Sort to get the newest version first
                matches.sort(reverse=True)
                app_path = Path(matches[0])
                if app_path.exists():
                    # Extract version from app name
                    name = app_path.name
                    for part in name.split():
                        if part.isdigit():
                            self._live_version = part
                            break
                    self._live_path = app_path
                    logger.info(f"Found Ableton Live {self._live_version} at {app_path}")
                    return app_path

        logger.warning("Ableton Live installation not found")
        return None

    def scan(self) -> List[AbletonDeviceInfo]:
        """Scan for all Ableton devices."""
        self.devices = []

        app_path = self.find_ableton_installation()
        if not app_path:
            return self.devices

        core_library = app_path / "Contents" / "App-Resources" / "Core Library"
        if not core_library.exists():
            logger.warning(f"Core Library not found at {core_library}")
            return self.devices

        devices_path = core_library / "Devices"

        # Scan instruments
        instruments_path = devices_path / "Instruments"
        if instruments_path.exists():
            self._scan_device_folder(instruments_path, "instrument")

        # Scan audio effects
        effects_path = devices_path / "Audio Effects"
        if effects_path.exists():
            self._scan_device_folder(effects_path, "audio_effect")

        # Scan MIDI effects
        midi_effects_path = devices_path / "MIDI Effects"
        if midi_effects_path.exists():
            self._scan_device_folder(midi_effects_path, "midi_effect")

        # Scan User Library presets
        self._scan_user_library()

        logger.info(f"Found {len(self.devices)} Ableton devices")
        return self.devices

    def _scan_device_folder(self, folder: Path, device_type: str) -> None:
        """Scan a device folder for devices."""
        for item in folder.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                # Skip info folders
                if item.name == "Ableton Folder Info":
                    continue
                if item.name == "Legacy":
                    # Could scan legacy devices separately
                    continue
                if item.name.startswith("Max "):
                    # Max for Live devices
                    continue
                if item.name.startswith("External"):
                    # External routing devices
                    continue

                device = self._create_device_info(item, device_type)
                self.devices.append(device)

    def _create_device_info(self, path: Path, device_type: str) -> AbletonDeviceInfo:
        """Create device info from a device folder."""
        name = path.name
        device_id = f"ableton.{device_type}.{name.lower().replace(' ', '_').replace('-', '_')}"

        # Get block family mapping
        block_family = DEVICE_TO_BLOCK_FAMILY.get(name, "")

        # Categorize
        categories = self._categorize_device(name, device_type)

        return AbletonDeviceInfo(
            device_id=device_id,
            name=name,
            device_type=device_type,
            categories=categories,
            block_family=block_family,
            path=path,
            is_builtin=True,
            live_version=self._live_version,
        )

    def _categorize_device(self, name: str, device_type: str) -> List[str]:
        """Categorize a device based on its name and type."""
        categories = []

        name_lower = name.lower()

        if device_type == "instrument":
            categories.append("Instrument")
            if any(k in name_lower for k in ["synth", "wave", "drift", "analog", "operator", "meld"]):
                categories.append("Synthesizer")
            if any(k in name_lower for k in ["sampler", "simpler"]):
                categories.append("Sampler")
            if "drum" in name_lower or "impulse" in name_lower:
                categories.append("Drums")

        elif device_type == "audio_effect":
            categories.append("Effect")

            # Amp/cab/drive
            if name in ["Amp", "Cabinet", "Pedal"]:
                categories.append("Amp Simulation")
            if any(k in name_lower for k in ["saturator", "overdrive", "tube", "roar", "distortion"]):
                categories.append("Distortion")

            # Dynamics
            if any(k in name_lower for k in ["compressor", "dynamics", "limiter", "gate"]):
                categories.append("Dynamics")

            # EQ
            if "eq" in name_lower or "filter" in name_lower:
                categories.append("EQ")

            # Time-based
            if any(k in name_lower for k in ["reverb", "delay", "echo"]):
                categories.append("Time-Based")

            # Modulation
            if any(k in name_lower for k in ["chorus", "phaser", "flanger", "tremolo", "pan"]):
                categories.append("Modulation")

            # Special
            if any(k in name_lower for k in ["redux", "vinyl", "vocoder", "shifter"]):
                categories.append("Special")

        elif device_type == "midi_effect":
            categories.append("MIDI Effect")

        return categories

    def _scan_user_library(self) -> None:
        """Scan User Library for presets and racks."""
        if not USER_LIBRARY.exists():
            return

        presets_path = USER_LIBRARY / "Presets"
        if presets_path.exists():
            # Count presets per device type
            preset_counts = {}
            for device_folder in presets_path.iterdir():
                if device_folder.is_dir():
                    preset_count = len(list(device_folder.rglob("*.adv")))
                    preset_count += len(list(device_folder.rglob("*.adg")))
                    if preset_count > 0:
                        preset_counts[device_folder.name] = preset_count

            if preset_counts:
                logger.info(f"User Library presets: {preset_counts}")

    def get_devices_by_type(self, device_type: str) -> List[AbletonDeviceInfo]:
        """Get devices filtered by type."""
        return [d for d in self.devices if d.device_type == device_type]

    def get_devices_by_category(self, category: str) -> List[AbletonDeviceInfo]:
        """Get devices filtered by category."""
        return [d for d in self.devices if category in d.categories]

    def get_amp_simulation_devices(self) -> List[AbletonDeviceInfo]:
        """Get devices that can be used for amp simulation."""
        amp_devices = ["Amp", "Cabinet", "Pedal", "Saturator", "Overdrive", "Dynamic Tube", "Roar"]
        return [d for d in self.devices if d.name in amp_devices]


def scan_ableton_devices() -> List[Dict[str, Any]]:
    """Convenience function to scan and return plugin-compatible dicts."""
    scanner = AbletonScanner()
    devices = scanner.scan()
    return [d.to_plugin_dict() for d in devices]


if __name__ == "__main__":
    # Test scanning
    logging.basicConfig(level=logging.INFO)

    scanner = AbletonScanner()
    devices = scanner.scan()

    print(f"\nFound {len(devices)} Ableton devices:\n")

    for device_type in ["instrument", "audio_effect", "midi_effect"]:
        type_devices = scanner.get_devices_by_type(device_type)
        if type_devices:
            print(f"\n{device_type.upper()}S ({len(type_devices)}):")
            for d in type_devices:
                block = f" -> {d.block_family}" if d.block_family else ""
                print(f"  - {d.name}{block}")

    print("\n\nAmp Simulation devices:")
    for d in scanner.get_amp_simulation_devices():
        print(f"  - {d.name}: {d.block_family}")
