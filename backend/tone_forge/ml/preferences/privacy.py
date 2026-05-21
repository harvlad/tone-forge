"""Privacy controls for user preference data.

Provides user control over their data:
- View all collected data
- Export data in portable formats
- Delete specific data or all data
- Configure tracking settings

All data is stored locally by default. Cloud sync is opt-in only.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from .models import UserPreferences
from .tracker import BehaviorTracker, DEFAULT_TRACKER_DB, EventType

logger = logging.getLogger(__name__)

# Default paths
PREFERENCES_FILE = Path.home() / ".toneforge" / "preferences.json"
DATA_EXPORT_DIR = Path.home() / ".toneforge" / "exports"


class PrivacyManager:
    """Manages user data privacy controls."""

    def __init__(
        self,
        tracker: Optional[BehaviorTracker] = None,
        preferences_path: Optional[Path] = None,
    ):
        """Initialize the privacy manager.

        Args:
            tracker: BehaviorTracker instance
            preferences_path: Path to preferences file
        """
        from .tracker import get_tracker

        self.tracker = tracker or get_tracker()
        self.preferences_path = preferences_path or PREFERENCES_FILE
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)

    # Data viewing

    def get_data_summary(self) -> Dict[str, Any]:
        """Get a summary of all collected data.

        Returns:
            Dictionary with data summary
        """
        summary = {
            "storage_location": str(self.tracker.db_path),
            "preferences_location": str(self.preferences_path),
            "tracking_enabled": self.tracker.enabled,
        }

        # Get event statistics
        stats = self.tracker.get_aggregated_stats()
        summary["total_events"] = stats.get('total_events', 0)
        summary["total_sessions"] = stats.get('total_sessions', 0)
        summary["event_breakdown"] = stats.get('event_counts', {})
        summary["genre_distribution"] = stats.get('genre_distribution', {})
        summary["platform_distribution"] = stats.get('platform_distribution', {})

        # Calculate storage size
        if self.tracker.db_path.exists():
            summary["database_size_bytes"] = self.tracker.db_path.stat().st_size

        if self.preferences_path.exists():
            summary["preferences_size_bytes"] = self.preferences_path.stat().st_size

        return summary

    def get_all_events(
        self,
        event_type: Optional[EventType] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get all tracked events.

        Args:
            event_type: Filter by event type
            limit: Maximum events to return

        Returns:
            List of event dictionaries
        """
        events = self.tracker.get_events(event_type=event_type, limit=limit)
        return [e.to_dict() for e in events]

    def get_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all tracked sessions.

        Args:
            limit: Maximum sessions to return

        Returns:
            List of session dictionaries
        """
        return self.tracker.get_sessions(limit=limit)

    def get_preferences(self) -> Optional[Dict[str, Any]]:
        """Get current preferences.

        Returns:
            Preferences dictionary or None
        """
        if self.preferences_path.exists():
            with open(self.preferences_path, 'r') as f:
                return json.load(f)
        return None

    # Data export

    def export_all_data(
        self,
        output_dir: Optional[Path] = None,
        format: str = "json",
    ) -> Path:
        """Export all user data to files.

        Args:
            output_dir: Directory for export files
            format: Export format (json, csv)

        Returns:
            Path to export directory
        """
        output_dir = output_dir or DATA_EXPORT_DIR
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = output_dir / f"toneforge_export_{timestamp}"
        export_dir.mkdir(parents=True, exist_ok=True)

        # Export preferences
        prefs = self.get_preferences()
        if prefs:
            prefs_file = export_dir / "preferences.json"
            with open(prefs_file, 'w') as f:
                json.dump(prefs, f, indent=2)

        # Export events
        events = self.get_all_events(limit=10000)
        events_file = export_dir / "events.json"
        with open(events_file, 'w') as f:
            json.dump(events, f, indent=2)

        # Export sessions
        sessions = self.get_sessions(limit=1000)
        sessions_file = export_dir / "sessions.json"
        with open(sessions_file, 'w') as f:
            json.dump(sessions, f, indent=2)

        # Export summary
        summary = self.get_data_summary()
        summary['export_time'] = datetime.now().isoformat()
        summary_file = export_dir / "summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)

        # Create manifest
        manifest = {
            "export_version": "1.0",
            "export_time": datetime.now().isoformat(),
            "files": [
                "preferences.json",
                "events.json",
                "sessions.json",
                "summary.json",
            ],
            "total_events": len(events),
            "total_sessions": len(sessions),
        }
        manifest_file = export_dir / "manifest.json"
        with open(manifest_file, 'w') as f:
            json.dump(manifest, f, indent=2)

        logger.info("Exported data to %s", export_dir)
        return export_dir

    def export_preferences_only(
        self,
        output_path: Optional[Path] = None,
    ) -> Optional[Path]:
        """Export only preferences.

        Args:
            output_path: Output file path

        Returns:
            Path to exported file or None
        """
        prefs = self.get_preferences()
        if not prefs:
            return None

        output_path = output_path or (
            DATA_EXPORT_DIR / f"preferences_{datetime.now().strftime('%Y%m%d')}.json"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w') as f:
            json.dump(prefs, f, indent=2)

        return output_path

    # Data deletion

    def delete_all_events(self):
        """Delete all tracked events."""
        with sqlite3.connect(str(self.tracker.db_path)) as conn:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM stats")
            conn.commit()

        logger.info("Deleted all tracking events")

    def delete_events_by_type(self, event_type: EventType):
        """Delete events of a specific type.

        Args:
            event_type: Type of events to delete
        """
        with sqlite3.connect(str(self.tracker.db_path)) as conn:
            conn.execute(
                "DELETE FROM events WHERE event_type = ?",
                (event_type.value,)
            )
            conn.commit()

        logger.info("Deleted events of type %s", event_type.value)

    def delete_events_before(self, before_date: datetime):
        """Delete events before a specific date.

        Args:
            before_date: Delete events before this date
        """
        with sqlite3.connect(str(self.tracker.db_path)) as conn:
            conn.execute(
                "DELETE FROM events WHERE timestamp < ?",
                (before_date.isoformat(),)
            )
            conn.commit()

        logger.info("Deleted events before %s", before_date.isoformat())

    def delete_preferences(self):
        """Delete saved preferences."""
        if self.preferences_path.exists():
            self.preferences_path.unlink()
            logger.info("Deleted preferences file")

    def delete_all_data(self):
        """Delete all user data (nuclear option)."""
        # Delete events
        self.delete_all_events()

        # Delete preferences
        self.delete_preferences()

        # Delete database file entirely
        if self.tracker.db_path.exists():
            self.tracker.db_path.unlink()

        logger.warning("Deleted all ToneForge user data")

    def reset_preferences(self) -> UserPreferences:
        """Reset preferences to defaults.

        Returns:
            Fresh UserPreferences
        """
        prefs = UserPreferences()

        # Save fresh preferences
        self.save_preferences(prefs)

        return prefs

    # Settings

    def save_preferences(self, prefs: UserPreferences):
        """Save preferences to file.

        Args:
            prefs: Preferences to save
        """
        with open(self.preferences_path, 'w') as f:
            json.dump(prefs.to_dict(), f, indent=2)

    def load_preferences(self) -> UserPreferences:
        """Load preferences from file.

        Returns:
            UserPreferences (fresh if no file exists)
        """
        if self.preferences_path.exists():
            with open(self.preferences_path, 'r') as f:
                data = json.load(f)
            return UserPreferences.from_dict(data)
        return UserPreferences()

    def set_tracking_enabled(self, enabled: bool):
        """Enable or disable tracking.

        Args:
            enabled: Whether tracking should be enabled
        """
        self.tracker.set_enabled(enabled)

        # Update preferences
        prefs = self.load_preferences()
        prefs.tracking_enabled = enabled
        self.save_preferences(prefs)

        logger.info("Tracking %s", "enabled" if enabled else "disabled")

    def set_cloud_sync_enabled(self, enabled: bool):
        """Enable or disable cloud sync (placeholder for future).

        Args:
            enabled: Whether cloud sync should be enabled
        """
        prefs = self.load_preferences()
        prefs.cloud_sync_enabled = enabled
        self.save_preferences(prefs)

        logger.info("Cloud sync %s", "enabled" if enabled else "disabled")

    def get_storage_paths(self) -> Dict[str, Path]:
        """Get all storage paths used by ToneForge.

        Returns:
            Dictionary of path names to paths
        """
        toneforge_dir = Path.home() / ".toneforge"

        paths = {
            "base_directory": toneforge_dir,
            "preferences": self.preferences_path,
            "behavior_database": self.tracker.db_path,
            "exports": DATA_EXPORT_DIR,
        }

        # Add other known paths
        embeddings_dir = toneforge_dir / "embeddings"
        if embeddings_dir.exists():
            paths["embeddings"] = embeddings_dir

        plugins_db = toneforge_dir / "plugins.db"
        if plugins_db.exists():
            paths["plugins_database"] = plugins_db

        feedback_db = toneforge_dir / "feedback.db"
        if feedback_db.exists():
            paths["feedback_database"] = feedback_db

        return paths

    def get_total_storage_size(self) -> int:
        """Get total storage used by ToneForge.

        Returns:
            Total bytes used
        """
        total = 0
        paths = self.get_storage_paths()

        for name, path in paths.items():
            if path.exists():
                if path.is_file():
                    total += path.stat().st_size
                elif path.is_dir():
                    for file in path.rglob('*'):
                        if file.is_file():
                            total += file.stat().st_size

        return total


# Singleton instance
_privacy_manager: Optional[PrivacyManager] = None


def get_privacy_manager() -> PrivacyManager:
    """Get the singleton privacy manager instance.

    Returns:
        PrivacyManager instance
    """
    global _privacy_manager
    if _privacy_manager is None:
        _privacy_manager = PrivacyManager()
    return _privacy_manager


# Convenience functions

def get_data_summary() -> Dict[str, Any]:
    """Get summary of all collected data."""
    return get_privacy_manager().get_data_summary()


def export_all_data(output_dir: Optional[Path] = None) -> Path:
    """Export all user data."""
    return get_privacy_manager().export_all_data(output_dir)


def delete_all_data():
    """Delete all user data."""
    get_privacy_manager().delete_all_data()


def set_tracking_enabled(enabled: bool):
    """Enable or disable tracking."""
    get_privacy_manager().set_tracking_enabled(enabled)


def load_preferences() -> UserPreferences:
    """Load user preferences."""
    return get_privacy_manager().load_preferences()


def save_preferences(prefs: UserPreferences):
    """Save user preferences."""
    get_privacy_manager().save_preferences(prefs)
