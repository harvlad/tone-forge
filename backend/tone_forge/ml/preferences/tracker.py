"""Behavior tracking for preference learning.

Collects and stores user behavior events for learning preferences:
- Analysis events (what tones are analyzed)
- Translation events (what recommendations are used)
- Edit events (what parameters are tweaked)
- Session events (usage patterns)

All data stored locally in SQLite for privacy.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)

# Default database location
DEFAULT_TRACKER_DB = Path.home() / ".toneforge" / "behavior.db"


class EventType(Enum):
    """Types of trackable events."""
    ANALYSIS = "analysis"
    TRANSLATION = "translation"
    BLOCK_SELECTION = "block_selection"
    PARAMETER_EDIT = "parameter_edit"
    EXPORT = "export"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    GENRE_DETECTED = "genre_detected"
    ARCHETYPE_USED = "archetype_used"
    PLUGIN_USED = "plugin_used"
    FAVORITE_ADDED = "favorite_added"
    PRESET_SAVED = "preset_saved"


@dataclass
class BehaviorEvent:
    """A single tracked behavior event."""

    event_type: EventType
    timestamp: str
    session_id: str

    # Event-specific data
    data: Dict[str, Any]

    # Optional context
    descriptor_hash: Optional[str] = None
    genre: Optional[str] = None
    platform: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "data": self.data,
            "descriptor_hash": self.descriptor_hash,
            "genre": self.genre,
            "platform": self.platform,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BehaviorEvent":
        return cls(
            event_type=EventType(d["event_type"]),
            timestamp=d["timestamp"],
            session_id=d["session_id"],
            data=d.get("data", {}),
            descriptor_hash=d.get("descriptor_hash"),
            genre=d.get("genre"),
            platform=d.get("platform"),
        )


class BehaviorTracker:
    """Tracks user behavior for preference learning.

    Privacy-first design:
    - All data stored locally in SQLite
    - Audio content is never stored, only hashes and metadata
    - User can view, export, and delete all data
    - Tracking can be disabled
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        enabled: bool = True,
    ):
        """Initialize the tracker.

        Args:
            db_path: Path to SQLite database
            enabled: Whether tracking is enabled
        """
        self.db_path = db_path or DEFAULT_TRACKER_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self._current_session_id: Optional[str] = None
        self._init_database()

    def _init_database(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            conn.executescript("""
                -- Behavior events
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    data TEXT,  -- JSON
                    descriptor_hash TEXT,
                    genre TEXT,
                    platform TEXT
                );

                -- Sessions
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    event_count INTEGER DEFAULT 0,
                    platform TEXT,
                    summary TEXT  -- JSON
                );

                -- Aggregated statistics (for quick access)
                CREATE TABLE IF NOT EXISTS stats (
                    stat_key TEXT PRIMARY KEY,
                    stat_value TEXT,  -- JSON
                    updated_at TEXT
                );

                -- Indexes
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_genre ON events(genre);
            """)
            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Get database connection with context manager."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def start_session(self, platform: Optional[str] = None) -> str:
        """Start a new tracking session.

        Args:
            platform: Target platform (helix, axe_fx, etc.)

        Returns:
            Session ID
        """
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._current_session_id = session_id

        if not self.enabled:
            return session_id

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO sessions (session_id, start_time, platform)
                VALUES (?, ?, ?)
            """, (session_id, datetime.now().isoformat(), platform))
            conn.commit()

        self._track_event(
            EventType.SESSION_START,
            {"platform": platform},
        )

        return session_id

    def end_session(self, summary: Optional[Dict] = None):
        """End the current tracking session.

        Args:
            summary: Optional session summary data
        """
        if not self._current_session_id:
            return

        if self.enabled:
            self._track_event(
                EventType.SESSION_END,
                summary or {},
            )

            with self._get_connection() as conn:
                # Update session end time
                conn.execute("""
                    UPDATE sessions
                    SET end_time = ?, summary = ?
                    WHERE session_id = ?
                """, (
                    datetime.now().isoformat(),
                    json.dumps(summary or {}),
                    self._current_session_id,
                ))

                # Update event count
                conn.execute("""
                    UPDATE sessions
                    SET event_count = (
                        SELECT COUNT(*) FROM events
                        WHERE session_id = ?
                    )
                    WHERE session_id = ?
                """, (self._current_session_id, self._current_session_id))

                conn.commit()

        self._current_session_id = None

    def _track_event(
        self,
        event_type: EventType,
        data: Dict[str, Any],
        descriptor_hash: Optional[str] = None,
        genre: Optional[str] = None,
        platform: Optional[str] = None,
    ):
        """Internal method to track an event."""
        if not self.enabled:
            return

        session_id = self._current_session_id or "no_session"

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO events
                (event_type, timestamp, session_id, data, descriptor_hash, genre, platform)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                event_type.value,
                datetime.now().isoformat(),
                session_id,
                json.dumps(data),
                descriptor_hash,
                genre,
                platform,
            ))
            conn.commit()

    # High-level tracking methods

    def track_analysis(
        self,
        descriptor: Dict[str, Any],
        confidence: float,
        genre: Optional[str] = None,
    ):
        """Track an audio analysis event.

        Args:
            descriptor: The ToneDescriptor (will be hashed)
            confidence: Analysis confidence
            genre: Detected genre
        """
        # Create hash of descriptor (never store actual audio)
        desc_hash = self._hash_descriptor(descriptor)

        # Extract key attributes for learning
        amp_data = descriptor.get("amp", {})
        cab_data = descriptor.get("cab", {})
        effects_data = descriptor.get("effects", {})

        self._track_event(
            EventType.ANALYSIS,
            {
                "confidence": confidence,
                "amp_family": amp_data.get("family"),
                "gain": amp_data.get("gain"),
                "cab_config": cab_data.get("configuration"),
                "effect_count": len(effects_data),
                "effect_types": list(effects_data.keys()),
            },
            descriptor_hash=desc_hash,
            genre=genre,
        )

    def track_translation(
        self,
        descriptor_hash: str,
        platform: str,
        blocks: List[Dict],
        genre: Optional[str] = None,
    ):
        """Track a translation event.

        Args:
            descriptor_hash: Hash of source descriptor
            platform: Target platform
            blocks: Recommended blocks
            genre: Genre context
        """
        # Extract block info
        block_info = []
        for block in blocks:
            block_info.append({
                "slot": block.get("slot"),
                "block_id": block.get("block_id"),
                "family": block.get("family"),
            })

        self._track_event(
            EventType.TRANSLATION,
            {
                "block_count": len(blocks),
                "blocks": block_info,
            },
            descriptor_hash=descriptor_hash,
            genre=genre,
            platform=platform,
        )

    def track_block_selection(
        self,
        slot: str,
        block_id: str,
        block_family: str,
        was_top_pick: bool,
        rank: int,
        descriptor_hash: Optional[str] = None,
    ):
        """Track when user selects a block.

        Args:
            slot: Block slot (amp, cab, effect1, etc.)
            block_id: Selected block ID
            block_family: Block family
            was_top_pick: Whether this was the #1 recommendation
            rank: Position in recommendations (1-indexed)
            descriptor_hash: Source descriptor hash
        """
        self._track_event(
            EventType.BLOCK_SELECTION,
            {
                "slot": slot,
                "block_id": block_id,
                "block_family": block_family,
                "was_top_pick": was_top_pick,
                "rank": rank,
            },
            descriptor_hash=descriptor_hash,
        )

    def track_parameter_edit(
        self,
        slot: str,
        block_id: str,
        parameter: str,
        old_value: Any,
        new_value: Any,
        descriptor_hash: Optional[str] = None,
    ):
        """Track when user edits a parameter.

        Args:
            slot: Block slot
            block_id: Block ID
            parameter: Parameter name
            old_value: Original value
            new_value: New value
            descriptor_hash: Source descriptor hash
        """
        # Calculate direction of change
        direction = None
        if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
            if new_value > old_value:
                direction = "increase"
            elif new_value < old_value:
                direction = "decrease"
            else:
                direction = "unchanged"

        self._track_event(
            EventType.PARAMETER_EDIT,
            {
                "slot": slot,
                "block_id": block_id,
                "parameter": parameter,
                "old_value": old_value,
                "new_value": new_value,
                "direction": direction,
            },
            descriptor_hash=descriptor_hash,
        )

    def track_export(
        self,
        export_format: str,
        platform: str,
        block_count: int,
        descriptor_hash: Optional[str] = None,
    ):
        """Track an export event.

        Args:
            export_format: Export format (preset, hlx, etc.)
            platform: Target platform
            block_count: Number of blocks exported
            descriptor_hash: Source descriptor hash
        """
        self._track_event(
            EventType.EXPORT,
            {
                "export_format": export_format,
                "block_count": block_count,
            },
            descriptor_hash=descriptor_hash,
            platform=platform,
        )

    def track_genre_detected(
        self,
        genre: str,
        subgenre: Optional[str],
        confidence: float,
        descriptor_hash: Optional[str] = None,
    ):
        """Track genre detection.

        Args:
            genre: Primary genre
            subgenre: Subgenre if detected
            confidence: Detection confidence
            descriptor_hash: Source descriptor hash
        """
        self._track_event(
            EventType.GENRE_DETECTED,
            {
                "subgenre": subgenre,
                "confidence": confidence,
            },
            descriptor_hash=descriptor_hash,
            genre=genre,
        )

    def track_archetype_used(
        self,
        archetype: str,
        genre: str,
        descriptor_hash: Optional[str] = None,
    ):
        """Track archetype usage.

        Args:
            archetype: Archetype name
            genre: Associated genre
            descriptor_hash: Source descriptor hash
        """
        self._track_event(
            EventType.ARCHETYPE_USED,
            {
                "archetype": archetype,
            },
            descriptor_hash=descriptor_hash,
            genre=genre,
        )

    def track_plugin_used(
        self,
        plugin_id: str,
        plugin_name: str,
        block_family: str,
    ):
        """Track local plugin usage.

        Args:
            plugin_id: Plugin identifier
            plugin_name: Plugin display name
            block_family: Mapped block family
        """
        self._track_event(
            EventType.PLUGIN_USED,
            {
                "plugin_id": plugin_id,
                "plugin_name": plugin_name,
                "block_family": block_family,
            },
        )

    def track_favorite(
        self,
        item_type: str,
        item_id: str,
        item_name: str,
    ):
        """Track when something is favorited.

        Args:
            item_type: Type (block, preset, plugin)
            item_id: Item identifier
            item_name: Display name
        """
        self._track_event(
            EventType.FAVORITE_ADDED,
            {
                "item_type": item_type,
                "item_id": item_id,
                "item_name": item_name,
            },
        )

    def track_preset_saved(
        self,
        preset_name: str,
        platform: str,
        block_count: int,
        genre: Optional[str] = None,
    ):
        """Track preset save.

        Args:
            preset_name: Preset name
            platform: Target platform
            block_count: Number of blocks
            genre: Associated genre
        """
        self._track_event(
            EventType.PRESET_SAVED,
            {
                "preset_name": preset_name,
                "block_count": block_count,
            },
            genre=genre,
            platform=platform,
        )

    def track_analysis_feedback(
        self,
        analysis_id: str,
        overall_rating: float,
        descriptor_accuracy: Optional[float] = None,
        recommendations_usefulness: Optional[float] = None,
        notes: Optional[str] = None,
    ):
        """Track feedback on an analysis.

        Args:
            analysis_id: Analysis ID
            overall_rating: Overall rating (1-5)
            descriptor_accuracy: Rating for descriptor accuracy
            recommendations_usefulness: Rating for recommendations
            notes: Free-form notes
        """
        self._track_event(
            EventType.ANALYSIS,  # Reuse ANALYSIS event type for feedback
            {
                "analysis_id": analysis_id,
                "is_feedback": True,
                "overall_rating": overall_rating,
                "descriptor_accuracy": descriptor_accuracy,
                "recommendations_usefulness": recommendations_usefulness,
                "notes": notes,
            },
        )

    # Query methods

    def get_events(
        self,
        event_type: Optional[EventType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[BehaviorEvent]:
        """Get tracked events.

        Args:
            event_type: Filter by event type
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of events
        """
        with self._get_connection() as conn:
            if event_type:
                rows = conn.execute("""
                    SELECT * FROM events
                    WHERE event_type = ?
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                """, (event_type.value, limit, offset)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM events
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                """, (limit, offset)).fetchall()

            events = []
            for row in rows:
                events.append(BehaviorEvent(
                    event_type=EventType(row['event_type']),
                    timestamp=row['timestamp'],
                    session_id=row['session_id'],
                    data=json.loads(row['data']) if row['data'] else {},
                    descriptor_hash=row['descriptor_hash'],
                    genre=row['genre'],
                    platform=row['platform'],
                ))

            return events

    def get_event_count(self, event_type: Optional[EventType] = None) -> int:
        """Get count of events.

        Args:
            event_type: Filter by type

        Returns:
            Event count
        """
        with self._get_connection() as conn:
            if event_type:
                result = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type = ?",
                    (event_type.value,)
                ).fetchone()
            else:
                result = conn.execute("SELECT COUNT(*) FROM events").fetchone()

            return result[0]

    def get_sessions(self, limit: int = 20) -> List[Dict]:
        """Get session history.

        Args:
            limit: Maximum results

        Returns:
            List of session records
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM sessions
                ORDER BY start_time DESC
                LIMIT ?
            """, (limit,)).fetchall()

            sessions = []
            for row in rows:
                sessions.append({
                    "session_id": row['session_id'],
                    "start_time": row['start_time'],
                    "end_time": row['end_time'],
                    "event_count": row['event_count'],
                    "platform": row['platform'],
                    "summary": json.loads(row['summary']) if row['summary'] else {},
                })

            return sessions

    def get_aggregated_stats(self) -> Dict[str, Any]:
        """Get aggregated behavior statistics.

        Returns:
            Dictionary with statistics
        """
        with self._get_connection() as conn:
            stats = {}

            # Total events by type
            type_counts = {}
            for row in conn.execute("""
                SELECT event_type, COUNT(*) as count FROM events
                GROUP BY event_type
            """).fetchall():
                type_counts[row['event_type']] = row['count']
            stats['event_counts'] = type_counts

            # Genre distribution
            genre_counts = {}
            for row in conn.execute("""
                SELECT genre, COUNT(*) as count FROM events
                WHERE genre IS NOT NULL
                GROUP BY genre
                ORDER BY count DESC
            """).fetchall():
                genre_counts[row['genre']] = row['count']
            stats['genre_distribution'] = genre_counts

            # Platform distribution
            platform_counts = {}
            for row in conn.execute("""
                SELECT platform, COUNT(*) as count FROM events
                WHERE platform IS NOT NULL
                GROUP BY platform
                ORDER BY count DESC
            """).fetchall():
                platform_counts[row['platform']] = row['count']
            stats['platform_distribution'] = platform_counts

            # Session count
            stats['total_sessions'] = conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]

            # Total events
            stats['total_events'] = conn.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]

            return stats

    def _hash_descriptor(self, descriptor: Dict) -> str:
        """Create a hash of a descriptor for privacy.

        Args:
            descriptor: ToneDescriptor dict

        Returns:
            SHA256 hash string
        """
        # Normalize and hash
        normalized = json.dumps(descriptor, sort_keys=True)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def set_enabled(self, enabled: bool):
        """Enable or disable tracking.

        Args:
            enabled: Whether tracking is enabled
        """
        self.enabled = enabled

    def close(self):
        """Close tracker (end session if active)."""
        if self._current_session_id:
            self.end_session()


# Singleton instance
_tracker: Optional[BehaviorTracker] = None


def get_tracker(enabled: bool = True) -> BehaviorTracker:
    """Get the singleton tracker instance.

    Args:
        enabled: Whether tracking is enabled

    Returns:
        BehaviorTracker instance
    """
    global _tracker
    if _tracker is None:
        _tracker = BehaviorTracker(enabled=enabled)
    return _tracker


def track_event(event_type: EventType, data: Dict[str, Any], **kwargs):
    """Convenience function to track an event.

    Args:
        event_type: Event type
        data: Event data
        **kwargs: Additional arguments
    """
    tracker = get_tracker()
    tracker._track_event(event_type, data, **kwargs)
