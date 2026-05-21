"""Reference library for retrieval-augmented reconstruction.

Indexes historical tone analyses to provide context for new analyses:
- Store tone descriptors with embeddings
- Retrieve similar historical tones
- Augment new descriptors with retrieved context

Privacy-first: Only metadata and embeddings stored, never audio.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

logger = logging.getLogger(__name__)

# Default storage location
DEFAULT_LIBRARY_PATH = Path.home() / ".toneforge" / "reference_library.db"


@dataclass
class ToneReference:
    """A reference tone in the library."""

    reference_id: str
    descriptor_hash: str

    # Core descriptor summary (not full descriptor for privacy)
    amp_family: str
    gain_level: float
    cab_config: str
    effect_types: List[str]

    # Classification
    genre: Optional[str] = None
    subgenre: Optional[str] = None
    archetype: Optional[str] = None

    # Quality indicators
    confidence: float = 0.0
    user_rating: Optional[float] = None  # 1-5 stars
    was_exported: bool = False

    # Embedding
    embedding: Optional[np.ndarray] = None

    # Metadata
    created_at: str = ""
    source: str = "analysis"  # "analysis", "user_upload", "preset"

    # Context
    tags: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "descriptor_hash": self.descriptor_hash,
            "amp_family": self.amp_family,
            "gain_level": self.gain_level,
            "cab_config": self.cab_config,
            "effect_types": self.effect_types,
            "genre": self.genre,
            "subgenre": self.subgenre,
            "archetype": self.archetype,
            "confidence": self.confidence,
            "user_rating": self.user_rating,
            "was_exported": self.was_exported,
            "created_at": self.created_at,
            "source": self.source,
            "tags": self.tags,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToneReference":
        return cls(
            reference_id=d["reference_id"],
            descriptor_hash=d["descriptor_hash"],
            amp_family=d.get("amp_family", ""),
            gain_level=d.get("gain_level", 0.5),
            cab_config=d.get("cab_config", ""),
            effect_types=d.get("effect_types", []),
            genre=d.get("genre"),
            subgenre=d.get("subgenre"),
            archetype=d.get("archetype"),
            confidence=d.get("confidence", 0.0),
            user_rating=d.get("user_rating"),
            was_exported=d.get("was_exported", False),
            created_at=d.get("created_at", ""),
            source=d.get("source", "analysis"),
            tags=d.get("tags", []),
            notes=d.get("notes", ""),
        )


@dataclass
class RetrievalResult:
    """Result from retrieval query."""

    reference: ToneReference
    similarity: float
    match_reasons: List[str] = field(default_factory=list)


class ReferenceLibrary:
    """Library of historical tone references for retrieval.

    Stores tone descriptors and embeddings to enable similarity search
    and retrieval-augmented reconstruction.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        embedding_dim: int = 128,
    ):
        """Initialize the library.

        Args:
            db_path: Path to SQLite database
            embedding_dim: Dimension of embeddings
        """
        self.db_path = db_path or DEFAULT_LIBRARY_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_dim = embedding_dim
        self._init_database()

    def _init_database(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            conn.executescript("""
                -- Reference tones
                CREATE TABLE IF NOT EXISTS tone_refs (
                    reference_id TEXT PRIMARY KEY,
                    descriptor_hash TEXT NOT NULL,
                    amp_family TEXT,
                    gain_level REAL,
                    cab_config TEXT,
                    effect_types TEXT,  -- JSON array
                    genre TEXT,
                    subgenre TEXT,
                    archetype TEXT,
                    confidence REAL,
                    user_rating REAL,
                    was_exported INTEGER DEFAULT 0,
                    embedding BLOB,
                    created_at TEXT,
                    source TEXT,
                    tags TEXT,  -- JSON array
                    notes TEXT,
                    UNIQUE(descriptor_hash)
                );

                -- Similarity cache
                CREATE TABLE IF NOT EXISTS similarity_cache (
                    reference_id_a TEXT,
                    reference_id_b TEXT,
                    similarity REAL,
                    computed_at TEXT,
                    PRIMARY KEY (reference_id_a, reference_id_b)
                );

                -- Indexes
                CREATE INDEX IF NOT EXISTS idx_refs_genre ON tone_refs(genre);
                CREATE INDEX IF NOT EXISTS idx_refs_amp ON tone_refs(amp_family);
                CREATE INDEX IF NOT EXISTS idx_refs_exported ON tone_refs(was_exported);
                CREATE INDEX IF NOT EXISTS idx_refs_rating ON tone_refs(user_rating);
            """)
            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def add_reference(
        self,
        descriptor: Dict[str, Any],
        embedding: Optional[np.ndarray] = None,
        genre: Optional[str] = None,
        subgenre: Optional[str] = None,
        archetype: Optional[str] = None,
        confidence: float = 0.0,
        source: str = "analysis",
        tags: Optional[List[str]] = None,
    ) -> str:
        """Add a reference tone to the library.

        Args:
            descriptor: ToneDescriptor as dict
            embedding: Optional pre-computed embedding
            genre: Detected/assigned genre
            subgenre: Detected subgenre
            archetype: Assigned archetype
            confidence: Analysis confidence
            source: Source type
            tags: User tags

        Returns:
            Reference ID
        """
        # Create hash and ID
        descriptor_hash = self._hash_descriptor(descriptor)
        reference_id = f"ref_{descriptor_hash[:12]}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # Extract summary from descriptor
        amp_data = descriptor.get("amp", {})
        cab_data = descriptor.get("cab", {})
        effects_data = descriptor.get("effects", {})

        amp_family = amp_data.get("family", "")
        gain_level = amp_data.get("gain", 0.5)
        cab_config = cab_data.get("configuration", "")
        effect_types = list(effects_data.keys())

        # Serialize embedding
        embedding_blob = None
        if embedding is not None:
            embedding_blob = embedding.astype(np.float32).tobytes()

        with self._get_connection() as conn:
            try:
                conn.execute("""
                    INSERT INTO tone_refs (
                        reference_id, descriptor_hash, amp_family, gain_level,
                        cab_config, effect_types, genre, subgenre, archetype,
                        confidence, embedding, created_at, source, tags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    reference_id,
                    descriptor_hash,
                    amp_family,
                    gain_level,
                    cab_config,
                    json.dumps(effect_types),
                    genre,
                    subgenre,
                    archetype,
                    confidence,
                    embedding_blob,
                    datetime.now().isoformat(),
                    source,
                    json.dumps(tags or []),
                ))
                conn.commit()
                logger.debug("Added reference %s", reference_id)
                return reference_id
            except sqlite3.IntegrityError:
                # Already exists
                logger.debug("Reference already exists for hash %s", descriptor_hash)
                return self._get_reference_by_hash(descriptor_hash).reference_id

    def get_reference(self, reference_id: str) -> Optional[ToneReference]:
        """Get a reference by ID.

        Args:
            reference_id: Reference identifier

        Returns:
            ToneReference or None
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tone_refs WHERE reference_id = ?",
                (reference_id,)
            ).fetchone()

            if row:
                return self._row_to_reference(row)
            return None

    def _get_reference_by_hash(self, descriptor_hash: str) -> Optional[ToneReference]:
        """Get reference by descriptor hash."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tone_refs WHERE descriptor_hash = ?",
                (descriptor_hash,)
            ).fetchone()

            if row:
                return self._row_to_reference(row)
            return None

    def search_similar(
        self,
        query_embedding: np.ndarray,
        k: int = 5,
        min_similarity: float = 0.3,
        genre_filter: Optional[str] = None,
        exclude_hash: Optional[str] = None,
    ) -> List[RetrievalResult]:
        """Search for similar references by embedding.

        Args:
            query_embedding: Query embedding vector
            k: Number of results
            min_similarity: Minimum cosine similarity
            genre_filter: Filter by genre
            exclude_hash: Exclude this descriptor hash

        Returns:
            List of RetrievalResult sorted by similarity
        """
        results = []

        with self._get_connection() as conn:
            # Build query
            query = "SELECT * FROM tone_refs WHERE embedding IS NOT NULL"
            params = []

            if genre_filter:
                query += " AND genre = ?"
                params.append(genre_filter)

            if exclude_hash:
                query += " AND descriptor_hash != ?"
                params.append(exclude_hash)

            rows = conn.execute(query, params).fetchall()

        # Compute similarities
        query_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-8)

        for row in rows:
            embedding_blob = row['embedding']
            if not embedding_blob:
                continue

            ref_embedding = np.frombuffer(embedding_blob, dtype=np.float32)
            ref_norm = ref_embedding / (np.linalg.norm(ref_embedding) + 1e-8)

            similarity = float(np.dot(query_norm, ref_norm))

            if similarity >= min_similarity:
                reference = self._row_to_reference(row)
                reference.embedding = ref_embedding

                results.append(RetrievalResult(
                    reference=reference,
                    similarity=similarity,
                    match_reasons=[f"Cosine similarity: {similarity:.3f}"],
                ))

        # Sort by similarity and take top k
        results.sort(key=lambda x: x.similarity, reverse=True)
        return results[:k]

    def search_by_attributes(
        self,
        amp_family: Optional[str] = None,
        genre: Optional[str] = None,
        gain_range: Optional[Tuple[float, float]] = None,
        effect_types: Optional[List[str]] = None,
        min_rating: Optional[float] = None,
        exported_only: bool = False,
        limit: int = 10,
    ) -> List[ToneReference]:
        """Search references by attributes.

        Args:
            amp_family: Filter by amp family
            genre: Filter by genre
            gain_range: Filter by gain range (min, max)
            effect_types: Filter by having these effects
            min_rating: Minimum user rating
            exported_only: Only return exported references
            limit: Maximum results

        Returns:
            List of matching references
        """
        conditions = []
        params = []

        if amp_family:
            conditions.append("amp_family LIKE ?")
            params.append(f"%{amp_family}%")

        if genre:
            conditions.append("genre = ?")
            params.append(genre)

        if gain_range:
            conditions.append("gain_level >= ? AND gain_level <= ?")
            params.extend(gain_range)

        if effect_types:
            for eff in effect_types:
                conditions.append("effect_types LIKE ?")
                params.append(f'%"{eff}"%')

        if min_rating:
            conditions.append("user_rating >= ?")
            params.append(min_rating)

        if exported_only:
            conditions.append("was_exported = 1")

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"""
            SELECT * FROM tone_refs
            WHERE {where_clause}
            ORDER BY confidence DESC, user_rating DESC NULLS LAST
            LIMIT ?
        """
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_reference(row) for row in rows]

    def update_rating(self, reference_id: str, rating: float):
        """Update user rating for a reference.

        Args:
            reference_id: Reference ID
            rating: Rating (1-5)
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tone_refs SET user_rating = ? WHERE reference_id = ?",
                (rating, reference_id)
            )
            conn.commit()

    def mark_exported(self, reference_id: str):
        """Mark a reference as exported.

        Args:
            reference_id: Reference ID
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tone_refs SET was_exported = 1 WHERE reference_id = ?",
                (reference_id,)
            )
            conn.commit()

    def add_tags(self, reference_id: str, tags: List[str]):
        """Add tags to a reference.

        Args:
            reference_id: Reference ID
            tags: Tags to add
        """
        ref = self.get_reference(reference_id)
        if not ref:
            return

        existing_tags = set(ref.tags)
        existing_tags.update(tags)

        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tone_refs SET tags = ? WHERE reference_id = ?",
                (json.dumps(list(existing_tags)), reference_id)
            )
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Get library statistics.

        Returns:
            Dictionary with stats
        """
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM tone_refs").fetchone()[0]
            with_embedding = conn.execute(
                "SELECT COUNT(*) FROM tone_refs WHERE embedding IS NOT NULL"
            ).fetchone()[0]
            exported = conn.execute(
                "SELECT COUNT(*) FROM tone_refs WHERE was_exported = 1"
            ).fetchone()[0]
            rated = conn.execute(
                "SELECT COUNT(*) FROM tone_refs WHERE user_rating IS NOT NULL"
            ).fetchone()[0]

            # Genre distribution
            genre_dist = {}
            for row in conn.execute("""
                SELECT genre, COUNT(*) as count FROM tone_refs
                WHERE genre IS NOT NULL
                GROUP BY genre ORDER BY count DESC
            """).fetchall():
                genre_dist[row['genre']] = row['count']

            # Amp distribution
            amp_dist = {}
            for row in conn.execute("""
                SELECT amp_family, COUNT(*) as count FROM tone_refs
                WHERE amp_family IS NOT NULL AND amp_family != ''
                GROUP BY amp_family ORDER BY count DESC LIMIT 10
            """).fetchall():
                amp_dist[row['amp_family']] = row['count']

            return {
                "total_references": total,
                "with_embeddings": with_embedding,
                "exported_count": exported,
                "rated_count": rated,
                "genre_distribution": genre_dist,
                "amp_distribution": amp_dist,
            }

    def delete_reference(self, reference_id: str):
        """Delete a reference.

        Args:
            reference_id: Reference ID
        """
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM tone_refs WHERE reference_id = ?",
                (reference_id,)
            )
            conn.execute(
                "DELETE FROM similarity_cache WHERE reference_id_a = ? OR reference_id_b = ?",
                (reference_id, reference_id)
            )
            conn.commit()

    def clear(self):
        """Clear all references."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM tone_refs")
            conn.execute("DELETE FROM similarity_cache")
            conn.commit()

    def _hash_descriptor(self, descriptor: Dict) -> str:
        """Create hash of descriptor."""
        normalized = json.dumps(descriptor, sort_keys=True)
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _row_to_reference(self, row: sqlite3.Row) -> ToneReference:
        """Convert database row to ToneReference."""
        embedding = None
        if row['embedding']:
            embedding = np.frombuffer(row['embedding'], dtype=np.float32)

        return ToneReference(
            reference_id=row['reference_id'],
            descriptor_hash=row['descriptor_hash'],
            amp_family=row['amp_family'] or "",
            gain_level=row['gain_level'] or 0.5,
            cab_config=row['cab_config'] or "",
            effect_types=json.loads(row['effect_types']) if row['effect_types'] else [],
            genre=row['genre'],
            subgenre=row['subgenre'],
            archetype=row['archetype'],
            confidence=row['confidence'] or 0.0,
            user_rating=row['user_rating'],
            was_exported=bool(row['was_exported']),
            embedding=embedding,
            created_at=row['created_at'] or "",
            source=row['source'] or "analysis",
            tags=json.loads(row['tags']) if row['tags'] else [],
            notes=row['notes'] or "",
        )


# Singleton instance
_library: Optional[ReferenceLibrary] = None


def get_library() -> ReferenceLibrary:
    """Get the singleton library instance.

    Returns:
        ReferenceLibrary instance
    """
    global _library
    if _library is None:
        _library = ReferenceLibrary()
    return _library
