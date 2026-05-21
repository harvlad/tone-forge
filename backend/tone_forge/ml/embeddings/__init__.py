"""Audio embeddings for similarity search and retrieval.

Uses CLAP or OpenL3 to generate semantic audio embeddings that capture
tonal characteristics. Embeddings are stored in a FAISS vector store
for efficient similarity search.
"""
from __future__ import annotations

from .encoder import AudioEmbedder, is_encoder_ready, get_embedder
from .vector_store import VectorStore, is_store_ready, get_store
from .similarity import ToneSimilaritySearch, find_similar_tones

__all__ = [
    "AudioEmbedder",
    "is_encoder_ready",
    "get_embedder",
    "VectorStore",
    "is_store_ready",
    "get_store",
    "ToneSimilaritySearch",
    "find_similar_tones",
]
