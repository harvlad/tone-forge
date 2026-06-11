"""SQLite database for plugin registry.

Stores discovered plugins with metadata for:
- Fast lookup by name, manufacturer, format
- Change detection (modified time tracking)
- User favorites and usage stats
- Plugin-to-block mappings
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Default database location
DEFAULT_DB_PATH = Path.home() / ".toneforge" / "plugins.db"


class PluginDatabase:
    """SQLite database for plugin registry."""

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize the database.

        Args:
            db_path: Path to database file. Uses default if not specified.
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _init_database(self):
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            conn.executescript("""
                -- Discovered plugins
                CREATE TABLE IF NOT EXISTS plugins (
                    plugin_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    manufacturer TEXT NOT NULL,
                    version TEXT,
                    path TEXT NOT NULL,
                    format TEXT NOT NULL,
                    plugin_type TEXT NOT NULL,
                    categories TEXT,  -- JSON array
                    description TEXT,
                    website TEXT,
                    is_64bit INTEGER DEFAULT 1,
                    supports_mono INTEGER DEFAULT 1,
                    supports_stereo INTEGER DEFAULT 1,
                    modified_time REAL,
                    scan_time TEXT,
                    is_available INTEGER DEFAULT 1,
                    UNIQUE(path)
                );

                -- User favorites
                CREATE TABLE IF NOT EXISTS favorites (
                    plugin_id TEXT PRIMARY KEY,
                    added_time TEXT,
                    FOREIGN KEY (plugin_id) REFERENCES plugins(plugin_id)
                );

                -- Usage statistics
                CREATE TABLE IF NOT EXISTS usage_stats (
                    plugin_id TEXT PRIMARY KEY,
                    use_count INTEGER DEFAULT 0,
                    last_used TEXT,
                    avg_rating REAL,
                    FOREIGN KEY (plugin_id) REFERENCES plugins(plugin_id)
                );

                -- Plugin-to-block mappings
                CREATE TABLE IF NOT EXISTS block_mappings (
                    plugin_id TEXT NOT NULL,
                    block_family TEXT NOT NULL,
                    block_type TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    is_user_defined INTEGER DEFAULT 0,
                    created_time TEXT,
                    PRIMARY KEY (plugin_id, block_family),
                    FOREIGN KEY (plugin_id) REFERENCES plugins(plugin_id)
                );

                -- User-defined parameter mappings
                CREATE TABLE IF NOT EXISTS parameter_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plugin_id TEXT NOT NULL,
                    plugin_param TEXT NOT NULL,
                    toneforge_param TEXT NOT NULL,
                    transform_type TEXT DEFAULT 'linear',
                    transform_params TEXT,  -- JSON
                    FOREIGN KEY (plugin_id) REFERENCES plugins(plugin_id)
                );

                -- Scan history
                CREATE TABLE IF NOT EXISTS scan_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_time TEXT NOT NULL,
                    plugins_found INTEGER,
                    plugins_added INTEGER,
                    plugins_removed INTEGER,
                    scan_duration_ms INTEGER,
                    formats_scanned TEXT  -- JSON array
                );

                -- Indexes for common queries
                CREATE INDEX IF NOT EXISTS idx_plugins_name ON plugins(name);
                CREATE INDEX IF NOT EXISTS idx_plugins_manufacturer ON plugins(manufacturer);
                CREATE INDEX IF NOT EXISTS idx_plugins_format ON plugins(format);
                CREATE INDEX IF NOT EXISTS idx_plugins_type ON plugins(plugin_type);
                CREATE INDEX IF NOT EXISTS idx_plugins_available ON plugins(is_available);
            """)
            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Get a database connection with context manager."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def add_plugin(self, plugin_info) -> bool:
        """Add or update a plugin in the database.

        Args:
            plugin_info: PluginInfo dataclass instance

        Returns:
            True if added/updated, False on error
        """
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO plugins (
                        plugin_id, name, manufacturer, version, path, format,
                        plugin_type, categories, description, website,
                        is_64bit, supports_mono, supports_stereo,
                        modified_time, scan_time, is_available
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    plugin_info.plugin_id,
                    plugin_info.name,
                    plugin_info.manufacturer,
                    plugin_info.version,
                    str(plugin_info.path),
                    plugin_info.format,
                    plugin_info.plugin_type,
                    json.dumps(plugin_info.categories),
                    getattr(plugin_info, 'description', ''),
                    getattr(plugin_info, 'website', ''),
                    1 if getattr(plugin_info, 'is_64bit', True) else 0,
                    1 if getattr(plugin_info, 'supports_mono', True) else 0,
                    1 if getattr(plugin_info, 'supports_stereo', True) else 0,
                    plugin_info.modified_time,
                    datetime.now().isoformat(),
                    1,
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error("Failed to add plugin %s: %s", plugin_info.plugin_id, e)
            return False

    def add_plugins(self, plugins: list) -> Tuple[int, int]:
        """Add multiple plugins to the database.

        Args:
            plugins: List of PluginInfo instances

        Returns:
            Tuple of (added_count, error_count)
        """
        added = 0
        errors = 0

        for plugin in plugins:
            if self.add_plugin(plugin):
                added += 1
            else:
                errors += 1

        return added, errors

    def add_plugin_dict(self, plugin_dict: Dict[str, Any]) -> bool:
        """Add or update a plugin from a dictionary.

        Used for Ableton devices and other sources that return dicts.

        Args:
            plugin_dict: Plugin data as dictionary

        Returns:
            True if added/updated, False on error
        """
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO plugins (
                        plugin_id, name, manufacturer, version, path, format,
                        plugin_type, categories, description, website,
                        is_64bit, supports_mono, supports_stereo,
                        modified_time, scan_time, is_available
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    plugin_dict.get('plugin_id', ''),
                    plugin_dict.get('name', ''),
                    plugin_dict.get('manufacturer', ''),
                    plugin_dict.get('version', ''),
                    str(plugin_dict.get('path', '')),
                    plugin_dict.get('format', ''),
                    plugin_dict.get('plugin_type', ''),
                    json.dumps(plugin_dict.get('categories', [])),
                    plugin_dict.get('description', ''),
                    plugin_dict.get('website', ''),
                    1 if plugin_dict.get('is_64bit', True) else 0,
                    1 if plugin_dict.get('supports_mono', True) else 0,
                    1 if plugin_dict.get('supports_stereo', True) else 0,
                    plugin_dict.get('modified_time', 0.0),
                    datetime.now().isoformat(),
                    1,
                ))
                conn.commit()

                # Store block_family mapping if provided
                if plugin_dict.get('block_family'):
                    self.set_block_mapping(
                        plugin_id=plugin_dict['plugin_id'],
                        block_family=plugin_dict['block_family'],
                        block_type=plugin_dict.get('plugin_type', 'effect'),
                        confidence=0.9,
                        is_user_defined=False,
                    )

                return True
        except Exception as e:
            logger.error("Failed to add plugin dict %s: %s", plugin_dict.get('plugin_id'), e)
            return False

    def get_plugin(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Get a plugin by ID.

        Args:
            plugin_id: Plugin identifier

        Returns:
            Plugin data as dictionary, or None if not found
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM plugins WHERE plugin_id = ?",
                (plugin_id,)
            ).fetchone()

            if row:
                return self._row_to_dict(row)
            return None

    def get_plugin_by_path(self, path: Path) -> Optional[Dict[str, Any]]:
        """Get a plugin by file path.

        Args:
            path: Path to plugin file/bundle

        Returns:
            Plugin data as dictionary, or None if not found
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM plugins WHERE path = ?",
                (str(path),)
            ).fetchone()

            if row:
                return self._row_to_dict(row)
            return None

    def search_plugins(
        self,
        query: str = "",
        format: Optional[str] = None,
        plugin_type: Optional[str] = None,
        category: Optional[str] = None,
        manufacturer: Optional[str] = None,
        favorites_only: bool = False,
        available_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Search for plugins with filters.

        Args:
            query: Search query (matches name or manufacturer)
            format: Filter by format (au, vst3, vst2)
            plugin_type: Filter by type (effect, instrument)
            category: Filter by category
            manufacturer: Filter by manufacturer
            favorites_only: Only return favorites
            available_only: Only return available plugins
            limit: Maximum results
            offset: Offset for pagination

        Returns:
            List of matching plugins
        """
        conditions = []
        params = []

        if query:
            conditions.append("(name LIKE ? OR manufacturer LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])

        if format:
            conditions.append("format = ?")
            params.append(format)

        if plugin_type:
            conditions.append("plugin_type = ?")
            params.append(plugin_type)

        if category:
            conditions.append("categories LIKE ?")
            params.append(f'%"{category}"%')

        if manufacturer:
            conditions.append("manufacturer = ?")
            params.append(manufacturer)

        if available_only:
            conditions.append("is_available = 1")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        sql = f"""
            SELECT p.*, f.added_time as favorite_time, u.use_count, u.last_used,
                   m.block_family, m.block_type, m.confidence as mapping_confidence
            FROM plugins p
            LEFT JOIN favorites f ON p.plugin_id = f.plugin_id
            LEFT JOIN usage_stats u ON p.plugin_id = u.plugin_id
            LEFT JOIN block_mappings m ON p.plugin_id = m.plugin_id
            WHERE {where_clause}
        """

        if favorites_only:
            sql += " AND f.plugin_id IS NOT NULL"

        sql += " ORDER BY p.name LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            results = []
            for row in rows:
                plugin = self._row_to_dict(row)
                # Add block_mapping if present
                if row['block_family']:
                    plugin['block_mapping'] = {
                        'block_family': row['block_family'],
                        'block_type': row['block_type'],
                        'confidence': row['mapping_confidence'],
                    }
                results.append(plugin)
            return results

    def get_all_plugins(
        self,
        available_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Get all plugins.

        Args:
            available_only: Only return available plugins

        Returns:
            List of all plugins
        """
        return self.search_plugins(available_only=available_only, limit=10000)

    def get_plugins_by_format(self, format: str) -> List[Dict[str, Any]]:
        """Get all plugins of a specific format.

        Args:
            format: Plugin format (au, vst3, vst2)

        Returns:
            List of plugins
        """
        return self.search_plugins(format=format, limit=10000)

    def get_manufacturers(self) -> List[str]:
        """Get list of all manufacturers.

        Returns:
            List of manufacturer names
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT manufacturer FROM plugins
                WHERE is_available = 1
                ORDER BY manufacturer
            """).fetchall()
            return [row['manufacturer'] for row in rows]

    def get_categories(self) -> List[str]:
        """Get list of all categories.

        Returns:
            List of category names
        """
        categories = set()

        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT categories FROM plugins WHERE is_available = 1
            """).fetchall()

            for row in rows:
                if row['categories']:
                    cats = json.loads(row['categories'])
                    categories.update(cats)

        return sorted(categories)

    def mark_unavailable(self, plugin_id: str):
        """Mark a plugin as unavailable (file not found).

        Args:
            plugin_id: Plugin identifier
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE plugins SET is_available = 0 WHERE plugin_id = ?",
                (plugin_id,)
            )
            conn.commit()

    def mark_available(self, plugin_id: str):
        """Mark a plugin as available.

        Args:
            plugin_id: Plugin identifier
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE plugins SET is_available = 1 WHERE plugin_id = ?",
                (plugin_id,)
            )
            conn.commit()

    def remove_plugin(self, plugin_id: str):
        """Remove a plugin from the database.

        Args:
            plugin_id: Plugin identifier
        """
        with self._get_connection() as conn:
            conn.execute("DELETE FROM favorites WHERE plugin_id = ?", (plugin_id,))
            conn.execute("DELETE FROM usage_stats WHERE plugin_id = ?", (plugin_id,))
            conn.execute("DELETE FROM block_mappings WHERE plugin_id = ?", (plugin_id,))
            conn.execute("DELETE FROM parameter_mappings WHERE plugin_id = ?", (plugin_id,))
            conn.execute("DELETE FROM plugins WHERE plugin_id = ?", (plugin_id,))
            conn.commit()

    # Favorites management

    def add_favorite(self, plugin_id: str):
        """Add a plugin to favorites.

        Args:
            plugin_id: Plugin identifier
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO favorites (plugin_id, added_time)
                VALUES (?, ?)
            """, (plugin_id, datetime.now().isoformat()))
            conn.commit()

    def remove_favorite(self, plugin_id: str):
        """Remove a plugin from favorites.

        Args:
            plugin_id: Plugin identifier
        """
        with self._get_connection() as conn:
            conn.execute("DELETE FROM favorites WHERE plugin_id = ?", (plugin_id,))
            conn.commit()

    def is_favorite(self, plugin_id: str) -> bool:
        """Check if a plugin is a favorite.

        Args:
            plugin_id: Plugin identifier

        Returns:
            True if favorite
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM favorites WHERE plugin_id = ?",
                (plugin_id,)
            ).fetchone()
            return row is not None

    def get_favorites(self) -> List[Dict[str, Any]]:
        """Get all favorite plugins.

        Returns:
            List of favorite plugins
        """
        return self.search_plugins(favorites_only=True, limit=10000)

    # Usage statistics

    def record_usage(self, plugin_id: str):
        """Record plugin usage.

        Args:
            plugin_id: Plugin identifier
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO usage_stats (plugin_id, use_count, last_used)
                VALUES (?, 1, ?)
                ON CONFLICT(plugin_id) DO UPDATE SET
                    use_count = use_count + 1,
                    last_used = ?
            """, (plugin_id, datetime.now().isoformat(), datetime.now().isoformat()))
            conn.commit()

    def get_most_used(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most frequently used plugins.

        Args:
            limit: Maximum number of results

        Returns:
            List of plugins sorted by usage
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT p.*, u.use_count, u.last_used
                FROM plugins p
                JOIN usage_stats u ON p.plugin_id = u.plugin_id
                WHERE p.is_available = 1
                ORDER BY u.use_count DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def get_recently_used(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recently used plugins.

        Args:
            limit: Maximum number of results

        Returns:
            List of plugins sorted by last used time
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT p.*, u.use_count, u.last_used
                FROM plugins p
                JOIN usage_stats u ON p.plugin_id = u.plugin_id
                WHERE p.is_available = 1
                ORDER BY u.last_used DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [self._row_to_dict(row) for row in rows]

    # Block mappings

    def set_block_mapping(
        self,
        plugin_id: str,
        block_family: str,
        block_type: str,
        confidence: float = 1.0,
        is_user_defined: bool = False,
    ):
        """Set a plugin-to-block mapping.

        Args:
            plugin_id: Plugin identifier
            block_family: ToneForge block family (e.g., "marshall_jcm")
            block_type: Block type (e.g., "amp")
            confidence: Mapping confidence (0-1)
            is_user_defined: Whether user explicitly set this
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO block_mappings
                (plugin_id, block_family, block_type, confidence, is_user_defined, created_time)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                plugin_id,
                block_family,
                block_type,
                confidence,
                1 if is_user_defined else 0,
                datetime.now().isoformat(),
            ))
            conn.commit()

    def get_block_mapping(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Get the block mapping for a plugin.

        Args:
            plugin_id: Plugin identifier

        Returns:
            Mapping data or None
        """
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM block_mappings
                WHERE plugin_id = ?
                ORDER BY is_user_defined DESC, confidence DESC
                LIMIT 1
            """, (plugin_id,)).fetchone()

            if row:
                return dict(row)
            return None

    def get_plugins_for_block(self, block_family: str) -> List[Dict[str, Any]]:
        """Get all plugins mapped to a block family.

        Args:
            block_family: ToneForge block family

        Returns:
            List of plugins with mapping info
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT p.*, m.block_family, m.block_type, m.confidence, m.is_user_defined
                FROM plugins p
                JOIN block_mappings m ON p.plugin_id = m.plugin_id
                WHERE m.block_family = ? AND p.is_available = 1
                ORDER BY m.is_user_defined DESC, m.confidence DESC
            """, (block_family,)).fetchall()
            return [self._row_to_dict(row) for row in rows]

    # Scan history

    def record_scan(
        self,
        plugins_found: int,
        plugins_added: int,
        plugins_removed: int,
        scan_duration_ms: int,
        formats_scanned: List[str],
    ):
        """Record a scan in history.

        Args:
            plugins_found: Total plugins found
            plugins_added: New plugins added
            plugins_removed: Plugins removed (unavailable)
            scan_duration_ms: Scan duration in milliseconds
            formats_scanned: List of formats scanned
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO scan_history
                (scan_time, plugins_found, plugins_added, plugins_removed,
                 scan_duration_ms, formats_scanned)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                plugins_found,
                plugins_added,
                plugins_removed,
                scan_duration_ms,
                json.dumps(formats_scanned),
            ))
            conn.commit()

    def get_scan_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent scan history.

        Args:
            limit: Maximum number of results

        Returns:
            List of scan records
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM scan_history
                ORDER BY scan_time DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]

    def get_last_scan_time(self) -> Optional[datetime]:
        """Get the time of the last scan.

        Returns:
            datetime of last scan, or None if never scanned
        """
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT scan_time FROM scan_history
                ORDER BY scan_time DESC
                LIMIT 1
            """).fetchone()

            if row and row['scan_time']:
                return datetime.fromisoformat(row['scan_time'])
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics.

        Returns:
            Dictionary with stats
        """
        with self._get_connection() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM plugins"
            ).fetchone()[0]

            available = conn.execute(
                "SELECT COUNT(*) FROM plugins WHERE is_available = 1"
            ).fetchone()[0]

            by_format = {}
            for row in conn.execute("""
                SELECT format, COUNT(*) as count FROM plugins
                WHERE is_available = 1 GROUP BY format
            """).fetchall():
                by_format[row['format']] = row['count']

            by_type = {}
            for row in conn.execute("""
                SELECT plugin_type, COUNT(*) as count FROM plugins
                WHERE is_available = 1 GROUP BY plugin_type
            """).fetchall():
                by_type[row['plugin_type']] = row['count']

            favorites = conn.execute(
                "SELECT COUNT(*) FROM favorites"
            ).fetchone()[0]

            return {
                "total_plugins": total,
                "available_plugins": available,
                "unavailable_plugins": total - available,
                "by_format": by_format,
                "by_type": by_type,
                "favorites_count": favorites,
                "manufacturers_count": len(self.get_manufacturers()),
            }

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a database row to dictionary.

        Args:
            row: SQLite row

        Returns:
            Dictionary with parsed JSON fields
        """
        d = dict(row)

        # Parse JSON fields
        if 'categories' in d and d['categories']:
            d['categories'] = json.loads(d['categories'])
        else:
            d['categories'] = []

        if 'formats_scanned' in d and d['formats_scanned']:
            d['formats_scanned'] = json.loads(d['formats_scanned'])

        # Convert path back to Path
        if 'path' in d:
            d['path'] = Path(d['path'])

        return d

    def close(self):
        """Close database connections (no-op for SQLite with context manager)."""
        pass


def get_database(db_path: Optional[Path] = None) -> PluginDatabase:
    """Get a plugin database instance.

    Args:
        db_path: Optional custom path

    Returns:
        PluginDatabase instance
    """
    return PluginDatabase(db_path)
