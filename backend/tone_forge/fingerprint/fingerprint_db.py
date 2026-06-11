"""Fingerprint database for storage and similarity search.

Provides:
- Storage of stem fingerprints
- Similarity search using embedding vectors
- Metadata querying
- Template matching
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

from .stem_fingerprint import StemFingerprint, fingerprint_similarity

logger = logging.getLogger(__name__)


class FingerprintDatabase:
    """In-memory fingerprint database with persistence.

    Stores fingerprints and enables fast similarity search
    using embedding vectors.
    """

    def __init__(self, storage_path: Optional[str] = None):
        """Initialize database.

        Args:
            storage_path: Path to JSON storage file (optional)
        """
        self.storage_path = Path(storage_path) if storage_path else None
        self.fingerprints: Dict[str, StemFingerprint] = {}
        self.metadata: Dict[str, Dict[str, Any]] = {}

        # Index for fast search
        self._embeddings: Optional[np.ndarray] = None
        self._ids: List[str] = []

        # Load if storage exists
        if self.storage_path and self.storage_path.exists():
            self.load()

    def add(
        self,
        fingerprint: StemFingerprint,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Add a fingerprint to the database.

        Args:
            fingerprint: StemFingerprint to add
            metadata: Optional metadata (artist, song, etc.)
        """
        stem_id = fingerprint.stem_id
        if not stem_id:
            stem_id = f"stem_{len(self.fingerprints)}"
            fingerprint.stem_id = stem_id

        self.fingerprints[stem_id] = fingerprint
        self.metadata[stem_id] = metadata or {}

        # Invalidate index
        self._embeddings = None

    def remove(self, stem_id: str):
        """Remove a fingerprint from the database.

        Args:
            stem_id: ID of fingerprint to remove
        """
        if stem_id in self.fingerprints:
            del self.fingerprints[stem_id]
            del self.metadata[stem_id]
            self._embeddings = None

    def get(self, stem_id: str) -> Optional[StemFingerprint]:
        """Get a fingerprint by ID.

        Args:
            stem_id: Fingerprint ID

        Returns:
            StemFingerprint or None
        """
        return self.fingerprints.get(stem_id)

    def search_similar(
        self,
        query: StemFingerprint,
        top_k: int = 10,
        min_similarity: float = 0.5,
        stem_type_filter: Optional[str] = None,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """Search for similar fingerprints.

        Args:
            query: Query fingerprint
            top_k: Maximum number of results
            min_similarity: Minimum similarity threshold
            stem_type_filter: Optional filter by stem type

        Returns:
            List of (stem_id, similarity, metadata) tuples
        """
        if len(self.fingerprints) == 0:
            return []

        # Build index if needed
        self._build_index()

        # Filter by stem type if specified
        if stem_type_filter:
            valid_ids = {
                sid for sid, fp in self.fingerprints.items()
                if fp.stem_type == stem_type_filter
            }
        else:
            valid_ids = set(self.fingerprints.keys())

        # Compute similarities
        query_embedding = query.embedding.reshape(1, -1)

        # Cosine similarity using dot product (embeddings are normalized)
        similarities = np.dot(self._embeddings, query_embedding.T).flatten()

        # Convert to 0-1 range
        similarities = (similarities + 1) / 2

        # Get top-k
        results = []
        sorted_indices = np.argsort(similarities)[::-1]

        for idx in sorted_indices:
            stem_id = self._ids[idx]

            if stem_id not in valid_ids:
                continue

            sim = similarities[idx]
            if sim < min_similarity:
                break

            results.append((
                stem_id,
                float(sim),
                self.metadata.get(stem_id, {}),
            ))

            if len(results) >= top_k:
                break

        return results

    def search_by_features(
        self,
        spectral_brightness: Optional[Tuple[float, float]] = None,
        attack_time_ms: Optional[Tuple[float, float]] = None,
        rhythmic_density: Optional[Tuple[float, float]] = None,
        stem_type: Optional[str] = None,
        **kwargs,
    ) -> List[Tuple[str, StemFingerprint]]:
        """Search by feature ranges.

        Args:
            spectral_brightness: (min, max) range
            attack_time_ms: (min, max) range
            rhythmic_density: (min, max) range
            stem_type: Filter by stem type
            **kwargs: Additional feature filters

        Returns:
            List of (stem_id, fingerprint) tuples
        """
        results = []

        for stem_id, fp in self.fingerprints.items():
            # Apply filters
            if stem_type and fp.stem_type != stem_type:
                continue

            if spectral_brightness:
                if not (spectral_brightness[0] <= fp.spectral_brightness <= spectral_brightness[1]):
                    continue

            if attack_time_ms:
                if not (attack_time_ms[0] <= fp.attack_time_ms <= attack_time_ms[1]):
                    continue

            if rhythmic_density:
                if not (rhythmic_density[0] <= fp.rhythmic_density <= rhythmic_density[1]):
                    continue

            results.append((stem_id, fp))

        return results

    def get_statistics(self) -> Dict[str, Any]:
        """Get database statistics.

        Returns:
            Dictionary with statistics
        """
        if len(self.fingerprints) == 0:
            return {
                "total_count": 0,
                "by_type": {},
                "avg_features": {},
            }

        # Count by type
        by_type: Dict[str, int] = {}
        for fp in self.fingerprints.values():
            by_type[fp.stem_type] = by_type.get(fp.stem_type, 0) + 1

        # Average features
        features = {
            "brightness": [],
            "attack_time": [],
            "rhythmic_density": [],
            "harmonic_density": [],
        }

        for fp in self.fingerprints.values():
            features["brightness"].append(fp.spectral_brightness)
            features["attack_time"].append(fp.attack_time_ms)
            features["rhythmic_density"].append(fp.rhythmic_density)
            features["harmonic_density"].append(fp.harmonic_density)

        avg_features = {k: float(np.mean(v)) for k, v in features.items() if v}

        return {
            "total_count": len(self.fingerprints),
            "by_type": by_type,
            "avg_features": avg_features,
        }

    def _build_index(self):
        """Build embedding index for fast search."""
        if self._embeddings is not None:
            return

        self._ids = list(self.fingerprints.keys())
        embeddings = [self.fingerprints[sid].embedding for sid in self._ids]

        self._embeddings = np.vstack(embeddings)

    def save(self, path: Optional[str] = None):
        """Save database to JSON file.

        Args:
            path: Path to save to (uses storage_path if not specified)
        """
        save_path = Path(path) if path else self.storage_path
        if not save_path:
            raise ValueError("No storage path specified")

        # Ensure directory exists
        save_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "fingerprints": {},
            "metadata": self.metadata,
        }

        for stem_id, fp in self.fingerprints.items():
            fp_dict = fp.to_dict()
            # Store embedding as list for JSON
            fp_dict["embedding"] = fp.embedding.tolist()
            data["fingerprints"][stem_id] = fp_dict

        with open(save_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved {len(self.fingerprints)} fingerprints to {save_path}")

    def load(self, path: Optional[str] = None):
        """Load database from JSON file.

        Args:
            path: Path to load from (uses storage_path if not specified)
        """
        load_path = Path(path) if path else self.storage_path
        if not load_path or not load_path.exists():
            return

        with open(load_path, "r") as f:
            data = json.load(f)

        self.fingerprints = {}
        for stem_id, fp_dict in data.get("fingerprints", {}).items():
            embedding = np.array(fp_dict.pop("embedding", np.zeros(128)))

            # Reconstruct fingerprint
            fp = StemFingerprint(
                stem_id=stem_id,
                stem_type=fp_dict.get("stem_type", "unknown"),
                duration_sec=fp_dict.get("duration_sec", 0),
                harmonic_density=fp_dict.get("spectral", {}).get("harmonic_density", 0),
                spectral_brightness=fp_dict.get("spectral", {}).get("brightness", 0),
                spectral_spread=fp_dict.get("spectral", {}).get("spread", 0),
                spectral_flatness=fp_dict.get("spectral", {}).get("flatness", 0),
                spectral_rolloff=fp_dict.get("spectral", {}).get("rolloff", 0),
                transient_shape=fp_dict.get("temporal", {}).get("transient_shape", "medium"),
                attack_time_ms=fp_dict.get("temporal", {}).get("attack_time_ms", 0),
                decay_character=fp_dict.get("temporal", {}).get("decay_character", "sustain"),
                release_time_ms=fp_dict.get("temporal", {}).get("release_time_ms", 0),
                vibrato_rate=fp_dict.get("modulation", {}).get("vibrato_rate"),
                vibrato_depth=fp_dict.get("modulation", {}).get("vibrato_depth"),
                chorus_depth=fp_dict.get("modulation", {}).get("chorus_depth"),
                filter_movement=fp_dict.get("modulation", {}).get("filter_movement", 0),
                rhythmic_density=fp_dict.get("rhythmic", {}).get("density", 0),
                syncopation=fp_dict.get("rhythmic", {}).get("syncopation", 0),
                regularity=fp_dict.get("rhythmic", {}).get("regularity", 0),
                stereo_width=fp_dict.get("spatial", {}).get("stereo_width", 0),
                saturation_amount=fp_dict.get("timbral", {}).get("saturation", 0),
                noise_amount=fp_dict.get("timbral", {}).get("noise", 0),
                sub_bass_presence=fp_dict.get("timbral", {}).get("sub_bass", 0),
                filter_envelope=fp_dict.get("filter_envelope"),
                embedding=embedding,
            )
            self.fingerprints[stem_id] = fp

        self.metadata = data.get("metadata", {})
        self._embeddings = None  # Invalidate index

        logger.info(f"Loaded {len(self.fingerprints)} fingerprints from {load_path}")


# Global database instance
_global_db: Optional[FingerprintDatabase] = None


def get_database(storage_path: Optional[str] = None) -> FingerprintDatabase:
    """Get or create global fingerprint database.

    Args:
        storage_path: Optional path to storage file

    Returns:
        FingerprintDatabase instance
    """
    global _global_db

    if _global_db is None:
        # Default storage location
        if storage_path is None:
            home = Path.home()
            storage_path = str(home / ".toneforge" / "fingerprints.json")

        _global_db = FingerprintDatabase(storage_path)

    return _global_db


def search_similar_stems(
    query: StemFingerprint,
    top_k: int = 10,
    min_similarity: float = 0.5,
) -> List[Tuple[str, float, Dict[str, Any]]]:
    """Search for similar stems in global database.

    Args:
        query: Query fingerprint
        top_k: Maximum number of results
        min_similarity: Minimum similarity threshold

    Returns:
        List of (stem_id, similarity, metadata) tuples
    """
    db = get_database()
    return db.search_similar(query, top_k, min_similarity)
