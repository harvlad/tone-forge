"""Windows plugin scanner for VST2 and VST3 plugins.

Scans standard Windows plugin locations for:
- VST3 plugins (.vst3)
- VST2 plugins (.dll)

Extracts plugin metadata from:
- VST3 moduleinfo.json
- PE file properties for VST2
- Registry entries
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

logger = logging.getLogger(__name__)

# Standard Windows VST paths
VST3_PATHS_WIN = [
    Path(os.environ.get("COMMONPROGRAMFILES", "C:\\Program Files\\Common Files")) / "VST3",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Common" / "VST3",
]

VST2_PATHS_WIN = [
    Path(os.environ.get("COMMONPROGRAMFILES", "C:\\Program Files\\Common Files")) / "VST2",
    Path(os.environ.get("COMMONPROGRAMFILES", "C:\\Program Files\\Common Files")) / "Steinberg" / "VST2",
    Path("C:\\Program Files\\VSTPlugins"),
    Path("C:\\Program Files (x86)\\VSTPlugins"),
    Path("C:\\VSTPlugins"),
]

# Re-use PluginInfo from macOS scanner for consistency
from .scanner_macos import PluginInfo


class WindowsPluginScanner:
    """Scanner for Windows audio plugins (VST2 and VST3)."""

    def __init__(
        self,
        scan_vst3: bool = True,
        scan_vst2: bool = True,
        custom_paths: Optional[List[Path]] = None,
    ):
        """Initialize the scanner.

        Args:
            scan_vst3: Whether to scan VST3 plugins
            scan_vst2: Whether to scan VST2 plugins
            custom_paths: Additional paths to scan
        """
        self.scan_vst3 = scan_vst3
        self.scan_vst2 = scan_vst2
        self.custom_paths = custom_paths or []

    def scan(self) -> List[PluginInfo]:
        """Scan for all plugins.

        Returns:
            List of discovered plugins
        """
        plugins = []

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

    def _scan_vst3(self) -> List[PluginInfo]:
        """Scan for VST3 plugins."""
        plugins = []

        for vst3_path in VST3_PATHS_WIN:
            if not vst3_path.exists():
                continue

            # VST3 bundles are directories with .vst3 extension
            for bundle in vst3_path.glob("**/*.vst3"):
                if bundle.is_dir():
                    try:
                        plugin = self._parse_vst3_bundle(bundle)
                        if plugin:
                            plugins.append(plugin)
                    except Exception as e:
                        logger.warning("Failed to parse VST3 %s: %s", bundle, e)

        logger.info("Found %d VST3 plugins", len(plugins))
        return plugins

    def _parse_vst3_bundle(self, path: Path) -> Optional[PluginInfo]:
        """Parse a VST3 bundle directory."""
        # VST3 on Windows has moduleinfo.json in Contents/x86_64-win or similar
        module_info = None

        # Try to find moduleinfo.json
        for info_path in [
            path / "Contents" / "x86_64-win" / "moduleinfo.json",
            path / "Contents" / "x86-win" / "moduleinfo.json",
            path / "Contents" / "moduleinfo.json",
            path / "moduleinfo.json",
        ]:
            if info_path.exists():
                try:
                    with open(info_path, "r", encoding="utf-8") as f:
                        module_info = json.load(f)
                    break
                except Exception:
                    continue

        if module_info:
            return self._parse_vst3_moduleinfo(path, module_info)

        # Fallback: use directory name
        return PluginInfo(
            plugin_id=f"vst3:{path.stem}",
            name=path.stem,
            manufacturer="Unknown",
            version="1.0",
            path=path,
            format="vst3",
            plugin_type="effect",
            categories=self._categorize_from_name(path.stem),
            modified_time=path.stat().st_mtime,
        )

    def _parse_vst3_moduleinfo(
        self,
        path: Path,
        module_info: Dict,
    ) -> PluginInfo:
        """Parse VST3 moduleinfo.json."""
        # moduleinfo.json structure
        name = module_info.get("Name", path.stem)
        vendor = module_info.get("Vendor", "Unknown")
        version = module_info.get("Version", "1.0")
        url = module_info.get("URL", "")

        # Get categories from Classes
        categories = []
        plugin_type = "effect"

        classes = module_info.get("Classes", [])
        if classes:
            first_class = classes[0]
            class_categories = first_class.get("Categories", "")

            if "Instrument" in class_categories:
                plugin_type = "instrument"
                categories.append("Instrument")
            if "Fx" in class_categories:
                plugin_type = "effect"

            # Parse sub-categories
            if "|" in class_categories:
                sub_cats = class_categories.split("|")
                for cat in sub_cats:
                    cat = cat.strip()
                    if cat and cat not in ["Fx", "Instrument", "Audio"]:
                        categories.append(cat)

        # Add name-based categories
        categories.extend(self._categorize_from_name(name))
        categories = list(set(categories))

        # Generate unique ID
        sdk_version = module_info.get("SDKVersion", "")
        plugin_id = f"vst3:{vendor}.{name}".lower().replace(" ", "_")

        return PluginInfo(
            plugin_id=plugin_id,
            name=name,
            manufacturer=vendor,
            version=version,
            path=path,
            format="vst3",
            plugin_type=plugin_type,
            categories=categories,
            website=url,
            modified_time=path.stat().st_mtime,
        )

    def _scan_vst2(self) -> List[PluginInfo]:
        """Scan for VST2 plugins (DLL files)."""
        plugins = []

        for vst2_path in VST2_PATHS_WIN:
            if not vst2_path.exists():
                continue

            # VST2 plugins are DLL files
            for dll in vst2_path.glob("**/*.dll"):
                try:
                    plugin = self._parse_vst2_dll(dll)
                    if plugin:
                        plugins.append(plugin)
                except Exception as e:
                    logger.warning("Failed to parse VST2 %s: %s", dll, e)

        logger.info("Found %d VST2 plugins", len(plugins))
        return plugins

    def _parse_vst2_dll(self, path: Path) -> Optional[PluginInfo]:
        """Parse a VST2 DLL file."""
        # Try to extract version info from PE file
        version_info = self._get_pe_version_info(path)

        name = version_info.get("ProductName", path.stem)
        manufacturer = version_info.get("CompanyName", "Unknown")
        version = version_info.get("FileVersion", "1.0")
        description = version_info.get("FileDescription", "")

        return PluginInfo(
            plugin_id=f"vst2:{path.stem}",
            name=name,
            manufacturer=manufacturer,
            version=version,
            path=path,
            format="vst2",
            plugin_type="effect",
            categories=self._categorize_from_name(name),
            description=description,
            modified_time=path.stat().st_mtime,
        )

    def _get_pe_version_info(self, path: Path) -> Dict[str, str]:
        """Extract version info from PE file.

        Uses pefile library if available, otherwise returns empty dict.
        """
        try:
            import pefile

            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
            )

            info = {}

            if hasattr(pe, "FileInfo"):
                for file_info in pe.FileInfo:
                    for entry in file_info:
                        if hasattr(entry, "StringTable"):
                            for st in entry.StringTable:
                                for key, value in st.entries.items():
                                    info[key.decode()] = value.decode()

            pe.close()
            return info

        except ImportError:
            logger.debug("pefile not available, skipping PE version info")
            return {}
        except Exception as e:
            logger.debug("Failed to read PE info for %s: %s", path, e)
            return {}

    def _scan_directory(self, path: Path) -> List[PluginInfo]:
        """Scan a directory for plugins of any type."""
        plugins = []

        # VST3 bundles
        for vst3 in path.glob("**/*.vst3"):
            if vst3.is_dir():
                plugin = self._parse_vst3_bundle(vst3)
                if plugin:
                    plugins.append(plugin)

        # VST2 DLLs
        for dll in path.glob("**/*.dll"):
            plugin = self._parse_vst2_dll(dll)
            if plugin:
                plugins.append(plugin)

        return plugins

    def _categorize_from_name(self, name: str) -> List[str]:
        """Categorize plugin from its name."""
        categories = []
        name_lower = name.lower()

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
    scan_vst3: bool = True,
    scan_vst2: bool = True,
) -> List[PluginInfo]:
    """Convenience function to scan for plugins on Windows.

    Args:
        scan_vst3: Whether to scan VST3 plugins
        scan_vst2: Whether to scan VST2 plugins

    Returns:
        List of discovered plugins
    """
    scanner = WindowsPluginScanner(
        scan_vst3=scan_vst3,
        scan_vst2=scan_vst2,
    )
    return scanner.scan()


def get_plugin_paths() -> Dict[str, List[Path]]:
    """Get standard plugin paths for Windows.

    Returns:
        Dictionary mapping format to list of paths
    """
    return {
        "vst3": VST3_PATHS_WIN,
        "vst2": VST2_PATHS_WIN,
    }
