"""Vector store for audio embeddings using FAISS.

Provides efficient similarity search over audio embeddings stored
locally in a FAISS index. Supports persistence to disk for fast
startup and incremental updates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any
import json
import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)

# Try to import FAISS
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    faiss = None


# Default storage directory
DEFAULT_STORE_DIR = Path.home() / ".toneforge" / "embeddings"


@dataclass
class SearchResult:
    """Result from similarity search."""
    id: str
    score: float  # Cosine similarity (higher = more similar)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StoredEmbedding:
    """Embedding stored in the vector store."""
    id: str
    embedding: np.ndarray
    metadata: Dict[str, Any] = field(default_factory=dict)


class VectorStore:
    """FAISS-backed vector store for audio embeddings.

    Features:
    - Efficient approximate nearest neighbor search
    - Persistence to disk
    - Metadata storage alongside embeddings
    - Thread-safe operations
    - Graceful fallback to brute-force search if FAISS unavailable
    """

    def __init__(
        self,
        store_dir: Optional[Path] = None,
        embedding_dim: int = 512,
        index_type: str = "flat",
    ):
        """Initialize the vector store.

        Args:
            store_dir: Directory for persistent storage
            embedding_dim: Dimension of embeddings
            index_type: FAISS index type ("flat", "ivf", "hnsw")
        """
        self.store_dir = Path(store_dir) if store_dir else DEFAULT_STORE_DIR
        self.embedding_dim = embedding_dim
        self.index_type = index_type

        self._index = None
        self._id_to_idx: Dict[str, int] = {}
        self._idx_to_id: Dict[int, str] = {}
        self._metadata: Dict[str, Dict] = {}
        self._embeddings: List[np.ndarray] = []
        self._lock = threading.Lock()
        self._ready = False

    def _ensure_ready(self) -> None:
        """Ensure the store is ready for use."""
        if self._ready:
            return

        with self._lock:
            if self._ready:
                return

            # Try to load from disk
            if self._load():
                logger.info(f"Loaded vector store with {len(self._id_to_idx)} embeddings")
            else:
                # Create new index
                self._create_index()
                logger.info("Created new vector store")

            self._ready = True

    def _create_index(self) -> None:
        """Create a new FAISS index."""
        if not FAISS_AVAILABLE:
            logger.warning("FAISS not available, using brute-force search")
            self._index = None
            return

        if self.index_type == "flat":
            # Exact search with L2 distance
            self._index = faiss.IndexFlatIP(self.embedding_dim)  # Inner product for cosine similarity
        elif self.index_type == "ivf":
            # Approximate search with inverted file index
            quantizer = faiss.IndexFlatIP(self.embedding_dim)
            self._index = faiss.IndexIVFFlat(quantizer, self.embedding_dim, 100)
        elif self.index_type == "hnsw":
            # Approximate search with HNSW
            self._index = faiss.IndexHNSWFlat(self.embedding_dim, 32)
        else:
            self._index = faiss.IndexFlatIP(self.embedding_dim)

    def add(
        self,
        id: str,
        embedding: np.ndarray,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Add an embedding to the store.

        Args:
            id: Unique identifier for the embedding
            embedding: The embedding vector
            metadata: Optional metadata to store
        """
        self._ensure_ready()

        with self._lock:
            # Normalize embedding for cosine similarity
            embedding = embedding.astype(np.float32)
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm

            # Remove existing if present
            if id in self._id_to_idx:
                self._remove_internal(id)

            # Add to index
            idx = len(self._embeddings)
            self._embeddings.append(embedding)
            self._id_to_idx[id] = idx
            self._idx_to_id[idx] = id
            self._metadata[id] = metadata or {}

            if self._index is not None:
                self._index.add(embedding.reshape(1, -1))

    def search(
        self,
        query: np.ndarray,
        k: int = 5,
        min_score: float = 0.0,
    ) -> List[SearchResult]:
        """Search for similar embeddings.

        Args:
            query: Query embedding vector
            k: Number of results to return
            min_score: Minimum similarity score (0-1)

        Returns:
            List of SearchResults sorted by similarity
        """
        self._ensure_ready()

        if len(self._embeddings) == 0:
            return []

        # Normalize query
        query = query.astype(np.float32)
        norm = np.linalg.norm(query)
        if norm > 0:
            query = query / norm

        with self._lock:
            k = min(k, len(self._embeddings))

            if self._index is not None and FAISS_AVAILABLE:
                # Use FAISS for search
                scores, indices = self._index.search(query.reshape(1, -1), k)
                scores = scores[0]
                indices = indices[0]
            else:
                # Brute-force search
                embeddings = np.array(self._embeddings)
                scores = embeddings @ query
                indices = np.argsort(-scores)[:k]
                scores = scores[indices]

            results = []
            for score, idx in zip(scores, indices):
                if idx < 0 or idx >= len(self._idx_to_id):
                    continue

                id = self._idx_to_id[idx]
                if score >= min_score:
                    results.append(SearchResult(
                        id=id,
                        score=float(score),
                        metadata=self._metadata.get(id, {}),
                    ))

            return results

    def get(self, id: str) -> Optional[StoredEmbedding]:
        """Get an embedding by ID.

        Args:
            id: Embedding ID

        Returns:
            StoredEmbedding or None if not found
        """
        self._ensure_ready()

        with self._lock:
            if id not in self._id_to_idx:
                return None

            idx = self._id_to_idx[id]
            return StoredEmbedding(
                id=id,
                embedding=self._embeddings[idx],
                metadata=self._metadata.get(id, {}),
            )

    def delete(self, id: str) -> bool:
        """Delete an embedding by ID.

        Args:
            id: Embedding ID

        Returns:
            True if deleted, False if not found
        """
        self._ensure_ready()

        with self._lock:
            if id not in self._id_to_idx:
                return False

            self._remove_internal(id)
            return True

    def _remove_internal(self, id: str) -> None:
        """Internal removal (caller must hold lock)."""
        # FAISS doesn't support efficient removal, so we rebuild
        idx = self._id_to_idx.pop(id)
        del self._idx_to_id[idx]
        self._metadata.pop(id, None)

        # For simplicity, mark as removed but don't rebuild index
        # Full rebuild happens on save/load cycle

    def count(self) -> int:
        """Return number of embeddings in store."""
        self._ensure_ready()
        return len(self._id_to_idx)

    def save(self) -> bool:
        """Save the store to disk.

        Returns:
            True if saved successfully
        """
        self._ensure_ready()

        try:
            self.store_dir.mkdir(parents=True, exist_ok=True)

            with self._lock:
                # Save embeddings
                embeddings_path = self.store_dir / "embeddings.npy"
                if self._embeddings:
                    np.save(str(embeddings_path), np.array(self._embeddings))

                # Save mappings and metadata
                meta_path = self.store_dir / "metadata.json"
                meta_data = {
                    "id_to_idx": self._id_to_idx,
                    "metadata": self._metadata,
                    "embedding_dim": self.embedding_dim,
                    "index_type": self.index_type,
                }
                with open(meta_path, "w") as f:
                    json.dump(meta_data, f)

                # Save FAISS index if available
                if self._index is not None and FAISS_AVAILABLE:
                    index_path = self.store_dir / "faiss.index"
                    faiss.write_index(self._index, str(index_path))

            logger.info(f"Saved vector store with {len(self._id_to_idx)} embeddings")
            return True

        except Exception as e:
            logger.error(f"Failed to save vector store: {e}")
            return False

    def _load(self) -> bool:
        """Load the store from disk.

        Returns:
            True if loaded successfully
        """
        meta_path = self.store_dir / "metadata.json"
        embeddings_path = self.store_dir / "embeddings.npy"

        if not meta_path.exists() or not embeddings_path.exists():
            return False

        try:
            # Load metadata
            with open(meta_path, "r") as f:
                meta_data = json.load(f)

            self._id_to_idx = meta_data["id_to_idx"]
            self._idx_to_id = {v: k for k, v in self._id_to_idx.items()}
            self._metadata = meta_data["metadata"]
            self.embedding_dim = meta_data.get("embedding_dim", 512)
            self.index_type = meta_data.get("index_type", "flat")

            # Load embeddings
            self._embeddings = list(np.load(str(embeddings_path)))

            # Load or rebuild FAISS index
            index_path = self.store_dir / "faiss.index"
            if index_path.exists() and FAISS_AVAILABLE:
                self._index = faiss.read_index(str(index_path))
            elif FAISS_AVAILABLE and self._embeddings:
                self._create_index()
                embeddings_array = np.array(self._embeddings).astype(np.float32)
                self._index.add(embeddings_array)

            return True

        except Exception as e:
            logger.error(f"Failed to load vector store: {e}")
            return False

    def clear(self) -> None:
        """Clear all embeddings from the store."""
        with self._lock:
            self._embeddings = []
            self._id_to_idx = {}
            self._idx_to_id = {}
            self._metadata = {}
            self._create_index()


# Global store instance
_store: Optional[VectorStore] = None


def get_store() -> VectorStore:
    """Get the global vector store instance."""
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def is_store_ready() -> bool:
    """Check if the vector store is available."""
    return True  # Always available (falls back to brute force)
