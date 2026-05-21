"""Tone similarity search using audio embeddings.

Provides high-level API for finding similar tones based on audio
embeddings. Integrates with the ToneDescriptor system for rich
retrieval of historical analyses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union, Any
import json
import logging
import uuid

import numpy as np

from .encoder import AudioEmbedder, AudioEmbedding, get_embedder
from .vector_store import VectorStore, SearchResult, get_store

logger = logging.getLogger(__name__)


@dataclass
class SimilarTone:
    """A similar tone found by similarity search."""
    id: str
    score: float  # 0-1, higher = more similar
    source_filename: Optional[str] = None
    amp_family: Optional[str] = None
    gain: Optional[float] = None
    descriptor: Optional[Dict] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "score": self.score,
            "source_filename": self.source_filename,
            "amp_family": self.amp_family,
            "gain": self.gain,
            "descriptor": self.descriptor,
        }


@dataclass
class IndexedTone:
    """A tone indexed in the similarity system."""
    id: str
    embedding: np.ndarray
    source_filename: str
    amp_family: str
    gain: float
    descriptor: Dict = field(default_factory=dict)
    created_at: Optional[str] = None


class ToneSimilaritySearch:
    """Find similar tones using semantic audio embeddings.

    This class provides:
    - Indexing of analyzed tones with their embeddings
    - Similarity search to find tones similar to a query
    - Integration with ToneDescriptor for rich metadata

    Usage:
        search = ToneSimilaritySearch()

        # Index a new tone
        search.index_tone(
            audio=audio_array,
            sr=22050,
            descriptor=tone_descriptor.to_dict(),
        )

        # Find similar tones
        similar = search.find_similar(query_audio, k=5)
    """

    def __init__(
        self,
        embedder: Optional[AudioEmbedder] = None,
        store: Optional[VectorStore] = None,
    ):
        """Initialize the similarity search.

        Args:
            embedder: AudioEmbedder to use (uses global if None)
            store: VectorStore to use (uses global if None)
        """
        self.embedder = embedder or get_embedder()
        self.store = store or get_store()

    def index_tone(
        self,
        audio: np.ndarray,
        sr: int,
        descriptor: Dict,
        tone_id: Optional[str] = None,
    ) -> str:
        """Index a tone for similarity search.

        Args:
            audio: Audio signal (mono or stereo)
            sr: Sample rate
            descriptor: ToneDescriptor as dict
            tone_id: Optional ID (generated if not provided)

        Returns:
            The tone ID
        """
        if tone_id is None:
            tone_id = str(uuid.uuid4())

        # Generate embedding
        embedding = self.embedder.encode(audio, sr)

        # Extract key metadata from descriptor
        source = descriptor.get("source", {})
        amp = descriptor.get("amp", {})

        metadata = {
            "source_filename": source.get("filename", "unknown"),
            "amp_family": amp.get("family", "unknown"),
            "gain": amp.get("gain", 0.5),
            "duration_sec": source.get("duration_sec", 0),
            "descriptor": descriptor,
        }

        # Store in vector store
        self.store.add(tone_id, embedding.embedding, metadata)

        logger.info(f"Indexed tone {tone_id}: {metadata['source_filename']}")
        return tone_id

    def index_from_file(
        self,
        path: Union[str, Path],
        descriptor: Dict,
        tone_id: Optional[str] = None,
        sr: int = 22050,
    ) -> str:
        """Index a tone from an audio file.

        Args:
            path: Path to audio file
            descriptor: ToneDescriptor as dict
            tone_id: Optional ID
            sr: Sample rate for loading

        Returns:
            The tone ID
        """
        embedding = self.embedder.encode_file(path, sr)

        if tone_id is None:
            tone_id = str(uuid.uuid4())

        source = descriptor.get("source", {})
        amp = descriptor.get("amp", {})

        metadata = {
            "source_filename": source.get("filename", Path(path).name),
            "amp_family": amp.get("family", "unknown"),
            "gain": amp.get("gain", 0.5),
            "duration_sec": source.get("duration_sec", 0),
            "descriptor": descriptor,
        }

        self.store.add(tone_id, embedding.embedding, metadata)

        logger.info(f"Indexed tone from file {tone_id}: {path}")
        return tone_id

    def find_similar(
        self,
        audio: np.ndarray,
        sr: int = 22050,
        k: int = 5,
        min_score: float = 0.3,
    ) -> List[SimilarTone]:
        """Find tones similar to query audio.

        Args:
            audio: Query audio signal
            sr: Sample rate
            k: Number of results to return
            min_score: Minimum similarity score

        Returns:
            List of SimilarTone results
        """
        # Generate embedding for query
        embedding = self.embedder.encode(audio, sr)

        # Search
        results = self.store.search(embedding.embedding, k=k, min_score=min_score)

        # Convert to SimilarTone objects
        similar = []
        for result in results:
            similar.append(SimilarTone(
                id=result.id,
                score=result.score,
                source_filename=result.metadata.get("source_filename"),
                amp_family=result.metadata.get("amp_family"),
                gain=result.metadata.get("gain"),
                descriptor=result.metadata.get("descriptor"),
            ))

        return similar

    def find_similar_by_embedding(
        self,
        embedding: np.ndarray,
        k: int = 5,
        min_score: float = 0.3,
    ) -> List[SimilarTone]:
        """Find similar tones using a pre-computed embedding.

        Args:
            embedding: Query embedding vector
            k: Number of results
            min_score: Minimum similarity

        Returns:
            List of SimilarTone results
        """
        results = self.store.search(embedding, k=k, min_score=min_score)

        similar = []
        for result in results:
            similar.append(SimilarTone(
                id=result.id,
                score=result.score,
                source_filename=result.metadata.get("source_filename"),
                amp_family=result.metadata.get("amp_family"),
                gain=result.metadata.get("gain"),
                descriptor=result.metadata.get("descriptor"),
            ))

        return similar

    def find_similar_by_id(
        self,
        tone_id: str,
        k: int = 5,
        min_score: float = 0.3,
    ) -> List[SimilarTone]:
        """Find tones similar to an indexed tone.

        Args:
            tone_id: ID of the query tone
            k: Number of results (excluding query)
            min_score: Minimum similarity

        Returns:
            List of SimilarTone results (excluding query)
        """
        stored = self.store.get(tone_id)
        if stored is None:
            return []

        # Search for k+1 to account for self-match
        results = self.store.search(stored.embedding, k=k + 1, min_score=min_score)

        # Filter out self-match
        similar = []
        for result in results:
            if result.id == tone_id:
                continue
            similar.append(SimilarTone(
                id=result.id,
                score=result.score,
                source_filename=result.metadata.get("source_filename"),
                amp_family=result.metadata.get("amp_family"),
                gain=result.metadata.get("gain"),
                descriptor=result.metadata.get("descriptor"),
            ))
            if len(similar) >= k:
                break

        return similar

    def delete_tone(self, tone_id: str) -> bool:
        """Delete a tone from the index.

        Args:
            tone_id: ID of the tone to delete

        Returns:
            True if deleted
        """
        return self.store.delete(tone_id)

    def count(self) -> int:
        """Return number of indexed tones."""
        return self.store.count()

    def save(self) -> bool:
        """Save the index to disk."""
        return self.store.save()

    def get_tone(self, tone_id: str) -> Optional[IndexedTone]:
        """Get an indexed tone by ID.

        Args:
            tone_id: Tone ID

        Returns:
            IndexedTone or None
        """
        stored = self.store.get(tone_id)
        if stored is None:
            return None

        return IndexedTone(
            id=stored.id,
            embedding=stored.embedding,
            source_filename=stored.metadata.get("source_filename", "unknown"),
            amp_family=stored.metadata.get("amp_family", "unknown"),
            gain=stored.metadata.get("gain", 0.5),
            descriptor=stored.metadata.get("descriptor", {}),
        )


# Global instance
_search: Optional[ToneSimilaritySearch] = None


def get_similarity_search() -> ToneSimilaritySearch:
    """Get the global similarity search instance."""
    global _search
    if _search is None:
        _search = ToneSimilaritySearch()
    return _search


def find_similar_tones(
    audio: np.ndarray,
    sr: int = 22050,
    k: int = 5,
    min_score: float = 0.3,
) -> List[SimilarTone]:
    """Convenience function to find similar tones.

    Args:
        audio: Query audio
        sr: Sample rate
        k: Number of results
        min_score: Minimum similarity

    Returns:
        List of SimilarTone results
    """
    search = get_similarity_search()
    return search.find_similar(audio, sr, k, min_score)
