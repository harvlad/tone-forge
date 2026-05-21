"""Feedback collection for translator retraining.

Collects user feedback signals:
- Selection events (user accepted a recommendation)
- Edit events (user modified a recommended block's parameters)
- Export events (user exported the final preset)
- Rejection events (user chose a different block)

This data is used to retrain the ranking model for better
personalized recommendations.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)

# Default storage location
DEFAULT_FEEDBACK_DIR = Path.home() / ".toneforge" / "feedback"


class FeedbackType(Enum):
    """Types of feedback events."""

    SELECTION = "selection"  # User accepted a block recommendation
    REJECTION = "rejection"  # User chose a different block
    EDIT = "edit"            # User modified block parameters
    EXPORT = "export"        # User exported the preset
    RATING = "rating"        # Explicit user rating


@dataclass
class FeedbackEvent:
    """A single feedback event from user interaction."""

    event_id: str
    event_type: FeedbackType
    timestamp: str
    session_id: str

    # Context
    descriptor_hash: str
    slot: str
    block_id: str

    # For selection/rejection
    was_top_recommendation: bool = False
    recommendation_rank: int = 0
    alternatives_shown: List[str] = field(default_factory=list)

    # For edits
    parameter_changes: Dict[str, Any] = field(default_factory=dict)
    edit_magnitude: float = 0.0  # How much was changed (0-1)

    # For ratings
    rating: Optional[float] = None  # 1-5 stars

    # For exports
    export_format: Optional[str] = None

    # Metadata
    user_id: Optional[str] = None
    platform: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage."""
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "FeedbackEvent":
        """Create from dictionary."""
        d = d.copy()
        d["event_type"] = FeedbackType(d["event_type"])
        return cls(**d)


@dataclass
class TrainingExample:
    """A training example extracted from feedback.

    Used to create labeled data for model retraining.
    """

    descriptor_hash: str
    slot: str
    block_id: str
    label: float  # 0-1 relevance score

    # Context for feature building
    was_selected: bool = False
    was_edited: bool = False
    edit_magnitude: float = 0.0
    was_exported: bool = False
    explicit_rating: Optional[float] = None

    def compute_label(self) -> float:
        """Compute training label from feedback signals.

        Higher label = better match for this descriptor+slot.
        """
        label = 0.0

        # Selection is a strong positive signal
        if self.was_selected:
            label += 0.5

        # Being exported without edits is very positive
        if self.was_exported and not self.was_edited:
            label += 0.4
        elif self.was_exported:
            label += 0.2

        # Edits are mixed - selected but needed changes
        if self.was_edited:
            # Larger edits = worse initial recommendation
            label -= self.edit_magnitude * 0.3

        # Explicit rating overrides
        if self.explicit_rating is not None:
            label = self.explicit_rating / 5.0

        return max(0.0, min(1.0, label))


class FeedbackCollector:
    """Collects and stores user feedback for model retraining.

    Uses SQLite for persistent storage with efficient querying.
    """

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        session_id: Optional[str] = None,
    ):
        """Initialize the feedback collector.

        Args:
            storage_dir: Directory for feedback database
            session_id: Current session ID (auto-generated if not provided)
        """
        self.storage_dir = storage_dir or DEFAULT_FEEDBACK_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.storage_dir / "feedback.db"
        self.session_id = session_id or str(uuid.uuid4())

        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    descriptor_hash TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    block_id TEXT NOT NULL,
                    was_top_recommendation INTEGER DEFAULT 0,
                    recommendation_rank INTEGER DEFAULT 0,
                    alternatives_shown TEXT,
                    parameter_changes TEXT,
                    edit_magnitude REAL DEFAULT 0.0,
                    rating REAL,
                    export_format TEXT,
                    user_id TEXT,
                    platform TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_descriptor_slot
                ON feedback_events (descriptor_hash, slot)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_block
                ON feedback_events (block_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session
                ON feedback_events (session_id)
            """)

    def record(self, event: FeedbackEvent) -> str:
        """Record a feedback event.

        Args:
            event: The feedback event to record

        Returns:
            The event ID
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO feedback_events (
                    event_id, event_type, timestamp, session_id,
                    descriptor_hash, slot, block_id,
                    was_top_recommendation, recommendation_rank,
                    alternatives_shown, parameter_changes, edit_magnitude,
                    rating, export_format, user_id, platform
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id,
                event.event_type.value,
                event.timestamp,
                event.session_id,
                event.descriptor_hash,
                event.slot,
                event.block_id,
                1 if event.was_top_recommendation else 0,
                event.recommendation_rank,
                json.dumps(event.alternatives_shown),
                json.dumps(event.parameter_changes),
                event.edit_magnitude,
                event.rating,
                event.export_format,
                event.user_id,
                event.platform,
            ))

        logger.debug("Recorded feedback event: %s", event.event_id)
        return event.event_id

    def record_selection(
        self,
        descriptor_hash: str,
        slot: str,
        block_id: str,
        was_top_recommendation: bool = True,
        recommendation_rank: int = 1,
        alternatives: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Record a block selection event.

        Args:
            descriptor_hash: Hash of the descriptor
            slot: Slot type (amp, cab, etc.)
            block_id: Selected block ID
            was_top_recommendation: Whether this was the top recommendation
            recommendation_rank: Rank in the recommendation list
            alternatives: Other blocks that were shown
            user_id: Optional user ID

        Returns:
            Event ID
        """
        event = FeedbackEvent(
            event_id=str(uuid.uuid4()),
            event_type=FeedbackType.SELECTION,
            timestamp=datetime.now().isoformat(),
            session_id=self.session_id,
            descriptor_hash=descriptor_hash,
            slot=slot,
            block_id=block_id,
            was_top_recommendation=was_top_recommendation,
            recommendation_rank=recommendation_rank,
            alternatives_shown=alternatives or [],
            user_id=user_id,
        )
        return self.record(event)

    def record_edit(
        self,
        descriptor_hash: str,
        slot: str,
        block_id: str,
        parameter_changes: Dict[str, Any],
        edit_magnitude: float = 0.5,
        user_id: Optional[str] = None,
    ) -> str:
        """Record a block parameter edit event.

        Args:
            descriptor_hash: Hash of the descriptor
            slot: Slot type
            block_id: Edited block ID
            parameter_changes: Dict of parameter name -> (old_value, new_value)
            edit_magnitude: How much was changed (0-1)
            user_id: Optional user ID

        Returns:
            Event ID
        """
        event = FeedbackEvent(
            event_id=str(uuid.uuid4()),
            event_type=FeedbackType.EDIT,
            timestamp=datetime.now().isoformat(),
            session_id=self.session_id,
            descriptor_hash=descriptor_hash,
            slot=slot,
            block_id=block_id,
            parameter_changes=parameter_changes,
            edit_magnitude=edit_magnitude,
            user_id=user_id,
        )
        return self.record(event)

    def record_export(
        self,
        descriptor_hash: str,
        blocks: Dict[str, str],
        export_format: str,
        user_id: Optional[str] = None,
    ) -> List[str]:
        """Record an export event for all blocks in the preset.

        Args:
            descriptor_hash: Hash of the descriptor
            blocks: Dict mapping slot -> block_id
            export_format: Export format (axe_fx, helix, etc.)
            user_id: Optional user ID

        Returns:
            List of event IDs
        """
        event_ids = []

        for slot, block_id in blocks.items():
            event = FeedbackEvent(
                event_id=str(uuid.uuid4()),
                event_type=FeedbackType.EXPORT,
                timestamp=datetime.now().isoformat(),
                session_id=self.session_id,
                descriptor_hash=descriptor_hash,
                slot=slot,
                block_id=block_id,
                export_format=export_format,
                user_id=user_id,
            )
            event_ids.append(self.record(event))

        return event_ids

    def get_events_for_descriptor(
        self,
        descriptor_hash: str,
        slot: Optional[str] = None,
    ) -> List[FeedbackEvent]:
        """Get all feedback events for a descriptor.

        Args:
            descriptor_hash: Hash of the descriptor
            slot: Optional slot filter

        Returns:
            List of feedback events
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if slot:
                cursor = conn.execute("""
                    SELECT * FROM feedback_events
                    WHERE descriptor_hash = ? AND slot = ?
                    ORDER BY timestamp DESC
                """, (descriptor_hash, slot))
            else:
                cursor = conn.execute("""
                    SELECT * FROM feedback_events
                    WHERE descriptor_hash = ?
                    ORDER BY timestamp DESC
                """, (descriptor_hash,))

            return [self._row_to_event(row) for row in cursor]

    def get_events_for_block(
        self,
        block_id: str,
        limit: int = 100,
    ) -> List[FeedbackEvent]:
        """Get all feedback events for a block.

        Args:
            block_id: Block ID
            limit: Maximum number of events to return

        Returns:
            List of feedback events
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute("""
                SELECT * FROM feedback_events
                WHERE block_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (block_id, limit))

            return [self._row_to_event(row) for row in cursor]

    def _row_to_event(self, row: sqlite3.Row) -> FeedbackEvent:
        """Convert a database row to a FeedbackEvent."""
        return FeedbackEvent(
            event_id=row["event_id"],
            event_type=FeedbackType(row["event_type"]),
            timestamp=row["timestamp"],
            session_id=row["session_id"],
            descriptor_hash=row["descriptor_hash"],
            slot=row["slot"],
            block_id=row["block_id"],
            was_top_recommendation=bool(row["was_top_recommendation"]),
            recommendation_rank=row["recommendation_rank"],
            alternatives_shown=json.loads(row["alternatives_shown"] or "[]"),
            parameter_changes=json.loads(row["parameter_changes"] or "{}"),
            edit_magnitude=row["edit_magnitude"],
            rating=row["rating"],
            export_format=row["export_format"],
            user_id=row["user_id"],
            platform=row["platform"],
        )

    def generate_training_examples(
        self,
        min_events: int = 10,
    ) -> List[TrainingExample]:
        """Generate training examples from collected feedback.

        Aggregates feedback signals per (descriptor, slot, block) tuple
        to create labeled training data.

        Args:
            min_events: Minimum events required per example

        Returns:
            List of training examples
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Aggregate feedback per (descriptor, slot, block)
            cursor = conn.execute("""
                SELECT
                    descriptor_hash,
                    slot,
                    block_id,
                    COUNT(*) as event_count,
                    SUM(CASE WHEN event_type = 'selection' THEN 1 ELSE 0 END) as selections,
                    SUM(CASE WHEN event_type = 'edit' THEN 1 ELSE 0 END) as edits,
                    AVG(CASE WHEN event_type = 'edit' THEN edit_magnitude ELSE NULL END) as avg_edit_magnitude,
                    SUM(CASE WHEN event_type = 'export' THEN 1 ELSE 0 END) as exports,
                    AVG(rating) as avg_rating
                FROM feedback_events
                GROUP BY descriptor_hash, slot, block_id
                HAVING COUNT(*) >= ?
            """, (min_events,))

            examples = []
            for row in cursor:
                example = TrainingExample(
                    descriptor_hash=row["descriptor_hash"],
                    slot=row["slot"],
                    block_id=row["block_id"],
                    label=0.0,
                    was_selected=row["selections"] > 0,
                    was_edited=row["edits"] > 0,
                    edit_magnitude=row["avg_edit_magnitude"] or 0.0,
                    was_exported=row["exports"] > 0,
                    explicit_rating=row["avg_rating"],
                )
                example.label = example.compute_label()
                examples.append(example)

            return examples

    def get_stats(self) -> Dict[str, Any]:
        """Get feedback collection statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_events,
                    COUNT(DISTINCT session_id) as unique_sessions,
                    COUNT(DISTINCT descriptor_hash) as unique_descriptors,
                    COUNT(DISTINCT block_id) as unique_blocks,
                    SUM(CASE WHEN event_type = 'selection' THEN 1 ELSE 0 END) as selections,
                    SUM(CASE WHEN event_type = 'edit' THEN 1 ELSE 0 END) as edits,
                    SUM(CASE WHEN event_type = 'export' THEN 1 ELSE 0 END) as exports
                FROM feedback_events
            """)
            row = cursor.fetchone()

            return {
                "total_events": row[0],
                "unique_sessions": row[1],
                "unique_descriptors": row[2],
                "unique_blocks": row[3],
                "selections": row[4],
                "edits": row[5],
                "exports": row[6],
            }

    def clear(self) -> int:
        """Clear all feedback data.

        Returns:
            Number of events deleted
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM feedback_events")
            count = cursor.fetchone()[0]

            conn.execute("DELETE FROM feedback_events")

            logger.info("Cleared %d feedback events", count)
            return count


# Module-level singleton
_collector: Optional[FeedbackCollector] = None


def get_collector(
    storage_dir: Optional[Path] = None,
    session_id: Optional[str] = None,
) -> FeedbackCollector:
    """Get or create the global FeedbackCollector instance."""
    global _collector

    if _collector is None:
        _collector = FeedbackCollector(
            storage_dir=storage_dir,
            session_id=session_id,
        )

    return _collector


def record_selection(
    descriptor_hash: str,
    slot: str,
    block_id: str,
    was_top_recommendation: bool = True,
    recommendation_rank: int = 1,
    alternatives: Optional[List[str]] = None,
    user_id: Optional[str] = None,
) -> str:
    """Record a selection using the global collector."""
    return get_collector().record_selection(
        descriptor_hash=descriptor_hash,
        slot=slot,
        block_id=block_id,
        was_top_recommendation=was_top_recommendation,
        recommendation_rank=recommendation_rank,
        alternatives=alternatives,
        user_id=user_id,
    )


def record_edit(
    descriptor_hash: str,
    slot: str,
    block_id: str,
    parameter_changes: Dict[str, Any],
    edit_magnitude: float = 0.5,
    user_id: Optional[str] = None,
) -> str:
    """Record an edit using the global collector."""
    return get_collector().record_edit(
        descriptor_hash=descriptor_hash,
        slot=slot,
        block_id=block_id,
        parameter_changes=parameter_changes,
        edit_magnitude=edit_magnitude,
        user_id=user_id,
    )


def record_export(
    descriptor_hash: str,
    blocks: Dict[str, str],
    export_format: str,
    user_id: Optional[str] = None,
) -> List[str]:
    """Record an export using the global collector."""
    return get_collector().record_export(
        descriptor_hash=descriptor_hash,
        blocks=blocks,
        export_format=export_format,
        user_id=user_id,
    )
