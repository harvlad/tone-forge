"""Local plugin scanner for ToneForge.

Scans local audio plugin installations and maps them to ToneForge blocks:

- scanner_macos: macOS AU/VST3/VST2 scanning
- scanner_windows: Windows VST3/VST2 scanning
- plugin_db: SQLite registry for discovered plugins
- plugin_mapper: Maps plugins to ToneForge block families

Usage:
    from local_engine.plugin_scanner import scan_and_register

    # Scan and register all plugins
    stats = scan_and_register()

    # Get plugins for a descriptor
    plugins = get_plugins_for_descriptor(descriptor)
"""
from __future__ import annotations

import logging
import platform
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Re-export core classes
from .plugin_db import PluginDatabase, get_database, DEFAULT_DB_PATH
from .plugin_mapper import (
    PluginMapper,
    BlockMapping,
    get_mapper,
    map_plugin,
)

# Import platform-specific scanner
_system = platform.system()

if _system == "Darwin":
    from .scanner_macos import (
        MacOSPluginScanner,
        PluginInfo,
        scan_plugins as _scan_plugins,
        get_plugin_paths,
    )
elif _system == "Windows":
    from .scanner_windows import (
        WindowsPluginScanner,
        scan_plugins as _scan_plugins,
        get_plugin_paths,
    )
    # Import PluginInfo from macos module (shared)
    from .scanner_macos import PluginInfo
else:
    # Linux or other - minimal implementation
    PluginInfo = None
    _scan_plugins = lambda **kwargs: []
    get_plugin_paths = lambda: {}

    class MacOSPluginScanner:
        def __init__(self, **kwargs):
            pass

        def scan(self):
            return []

    class WindowsPluginScanner:
        def __init__(self, **kwargs):
            pass

        def scan(self):
            return []

# Import Ableton scanner (macOS only for now)
if _system == "Darwin":
    try:
        from .scanner_ableton import AbletonScanner, scan_ableton_devices
    except ImportError:
        AbletonScanner = None
        scan_ableton_devices = lambda: []
else:
    AbletonScanner = None
    scan_ableton_devices = lambda: []


__all__ = [
    # Database
    "PluginDatabase",
    "get_database",
    "DEFAULT_DB_PATH",
    # Mapper
    "PluginMapper",
    "BlockMapping",
    "get_mapper",
    "map_plugin",
    # Scanner
    "PluginInfo",
    "scan_plugins",
    "get_plugin_paths",
    # High-level functions
    "scan_and_register",
    "get_all_plugins",
    "search_plugins",
    "get_plugins_for_descriptor",
    "get_plugin_recommendations",
]


# Singleton instances
_database: Optional[PluginDatabase] = None
_mapper: Optional[PluginMapper] = None


def _get_database() -> PluginDatabase:
    """Get singleton database instance."""
    global _database
    if _database is None:
        _database = get_database()
    return _database


def _get_mapper() -> PluginMapper:
    """Get singleton mapper instance."""
    global _mapper
    if _mapper is None:
        _mapper = get_mapper(_get_database())
    return _mapper


def scan_plugins(
    scan_au: bool = True,
    scan_vst3: bool = True,
    scan_vst2: bool = False,
    custom_paths: Optional[List[Path]] = None,
) -> List[PluginInfo]:
    """Scan for plugins on the current platform.

    Args:
        scan_au: Whether to scan Audio Units (macOS only)
        scan_vst3: Whether to scan VST3 plugins
        scan_vst2: Whether to scan VST2 plugins
        custom_paths: Additional paths to scan

    Returns:
        List of discovered plugins
    """
    if _system == "Darwin":
        scanner = MacOSPluginScanner(
            scan_au=scan_au,
            scan_vst3=scan_vst3,
            scan_vst2=scan_vst2,
            custom_paths=custom_paths,
        )
    elif _system == "Windows":
        scanner = WindowsPluginScanner(
            scan_vst3=scan_vst3,
            scan_vst2=scan_vst2,
            custom_paths=custom_paths,
        )
    else:
        logger.warning("Plugin scanning not supported on %s", _system)
        return []

    return scanner.scan()


def scan_and_register(
    scan_au: bool = True,
    scan_vst3: bool = True,
    scan_vst2: bool = False,
    scan_ableton: bool = True,
    custom_paths: Optional[List[Path]] = None,
    update_mappings: bool = True,
) -> Dict[str, Any]:
    """Scan for plugins and register them in the database.

    This is the main entry point for plugin discovery.

    Args:
        scan_au: Whether to scan Audio Units (macOS only)
        scan_vst3: Whether to scan VST3 plugins
        scan_vst2: Whether to scan VST2 plugins
        scan_ableton: Whether to scan Ableton Live devices
        custom_paths: Additional paths to scan
        update_mappings: Whether to compute block mappings

    Returns:
        Dictionary with scan statistics
    """
    start_time = time.time()
    db = _get_database()

    # Get existing plugins to track changes (use string paths as keys)
    existing = {str(p['path']): p for p in db.get_all_plugins(available_only=False)}

    # Scan for plugins
    plugins = scan_plugins(
        scan_au=scan_au,
        scan_vst3=scan_vst3,
        scan_vst2=scan_vst2,
        custom_paths=custom_paths,
    )

    # Scan Ableton devices
    ableton_devices = []
    if scan_ableton and AbletonScanner is not None:
        try:
            ableton_devices = scan_ableton_devices()
            logger.info(f"Found {len(ableton_devices)} Ableton devices")
        except Exception as e:
            logger.warning(f"Ableton scanning failed: {e}")

    # Track found paths (as strings)
    found_paths = set()

    # Register plugins
    added = 0
    updated = 0

    for plugin in plugins:
        path_str = str(plugin.path)
        found_paths.add(path_str)

        existing_plugin = existing.get(path_str)

        if existing_plugin is None:
            # New plugin
            db.add_plugin(plugin)
            added += 1
        elif existing_plugin.get('modified_time') != plugin.modified_time:
            # Updated plugin
            db.add_plugin(plugin)
            updated += 1
        elif not existing_plugin.get('is_available', True):
            # Was unavailable, now found
            db.mark_available(plugin.plugin_id)
            updated += 1

    # Register Ableton devices (they're dicts, not PluginInfo)
    ableton_added = 0
    for device in ableton_devices:
        path_str = str(device.get('path', device['plugin_id']))
        found_paths.add(path_str)

        existing_plugin = existing.get(path_str)

        if existing_plugin is None:
            # New device - add as dict
            db.add_plugin_dict(device)
            ableton_added += 1

    added += ableton_added

    # Mark missing plugins as unavailable
    removed = 0
    for path_str, existing_plugin in existing.items():
        if path_str not in found_paths and existing_plugin.get('is_available', True):
            db.mark_unavailable(existing_plugin['plugin_id'])
            removed += 1

    # Update block mappings
    if update_mappings:
        mapper = _get_mapper()
        all_plugins = db.get_all_plugins()
        mapper.map_plugins(all_plugins)

    # Calculate duration
    duration_ms = int((time.time() - start_time) * 1000)

    # Determine formats scanned
    formats_scanned = []
    if scan_au and _system == "Darwin":
        formats_scanned.append("au")
    if scan_vst3:
        formats_scanned.append("vst3")
    if scan_vst2:
        formats_scanned.append("vst2")
    if scan_ableton and ableton_devices:
        formats_scanned.append("ableton_device")

    # Record scan in history
    db.record_scan(
        plugins_found=len(plugins),
        plugins_added=added,
        plugins_removed=removed,
        scan_duration_ms=duration_ms,
        formats_scanned=formats_scanned,
    )

    return {
        "plugins_found": len(plugins),
        "plugins_added": added,
        "plugins_updated": updated,
        "plugins_removed": removed,
        "scan_duration_ms": duration_ms,
        "formats_scanned": formats_scanned,
    }


def get_all_plugins(
    available_only: bool = True,
    include_mappings: bool = False,
) -> List[Dict[str, Any]]:
    """Get all registered plugins.

    Args:
        available_only: Only return available plugins
        include_mappings: Include block mappings in results

    Returns:
        List of plugin dictionaries
    """
    db = _get_database()
    plugins = db.get_all_plugins(available_only=available_only)

    if include_mappings:
        for plugin in plugins:
            mapping = db.get_block_mapping(plugin['plugin_id'])
            plugin['block_mapping'] = mapping

    return plugins


def search_plugins(
    query: str = "",
    format: Optional[str] = None,
    plugin_type: Optional[str] = None,
    category: Optional[str] = None,
    manufacturer: Optional[str] = None,
    favorites_only: bool = False,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Search for plugins.

    Args:
        query: Search query (name or manufacturer)
        format: Filter by format (au, vst3, vst2)
        plugin_type: Filter by type (effect, instrument)
        category: Filter by category
        manufacturer: Filter by manufacturer
        favorites_only: Only return favorites
        limit: Maximum results

    Returns:
        List of matching plugins
    """
    db = _get_database()
    return db.search_plugins(
        query=query,
        format=format,
        plugin_type=plugin_type,
        category=category,
        manufacturer=manufacturer,
        favorites_only=favorites_only,
        limit=limit,
    )


def get_plugins_for_descriptor(
    descriptor: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Find plugins that could recreate a tone descriptor.

    Args:
        descriptor: ToneDescriptor as dict

    Returns:
        Dict mapping slot (amp, cab, effects) to list of plugins with mappings
    """
    db = _get_database()
    mapper = _get_mapper()

    all_plugins = db.get_all_plugins()
    recommendations = mapper.get_plugins_for_descriptor(descriptor, all_plugins)

    # Convert to serializable format
    result = {
        "amp": [],
        "cab": [],
        "effects": [],
    }

    for slot, items in recommendations.items():
        for plugin, mapping in items:
            plugin_dict = plugin if isinstance(plugin, dict) else plugin.to_dict()
            plugin_dict['block_mapping'] = {
                'block_family': mapping.block_family,
                'block_type': mapping.block_type,
                'confidence': mapping.confidence,
                'match_reason': mapping.match_reason,
            }
            result[slot].append(plugin_dict)

    return result


def get_plugin_recommendations(
    block_family: str,
    block_type: str = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Get plugin recommendations for a block family.

    Args:
        block_family: ToneForge block family (e.g., "marshall_jcm")
        block_type: Optional block type filter
        limit: Maximum results

    Returns:
        List of recommended plugins
    """
    db = _get_database()
    plugins = db.get_plugins_for_block(block_family)

    if block_type:
        plugins = [p for p in plugins if p.get('block_type') == block_type]

    return plugins[:limit]


def get_plugin_stats() -> Dict[str, Any]:
    """Get plugin database statistics.

    Returns:
        Dictionary with statistics
    """
    return _get_database().get_stats()


def add_favorite(plugin_id: str):
    """Add a plugin to favorites."""
    _get_database().add_favorite(plugin_id)


def remove_favorite(plugin_id: str):
    """Remove a plugin from favorites."""
    _get_database().remove_favorite(plugin_id)


def record_plugin_usage(plugin_id: str):
    """Record that a plugin was used."""
    _get_database().record_usage(plugin_id)


def get_most_used_plugins(limit: int = 10) -> List[Dict[str, Any]]:
    """Get most frequently used plugins."""
    return _get_database().get_most_used(limit)


def get_recent_plugins(limit: int = 10) -> List[Dict[str, Any]]:
    """Get recently used plugins."""
    return _get_database().get_recently_used(limit)
