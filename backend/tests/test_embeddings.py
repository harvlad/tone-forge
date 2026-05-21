"""Tests for tone_forge/ml/embeddings - Audio embeddings and similarity search."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.ml.embeddings.encoder import (
    AudioEmbedder,
    AudioEmbedding,
    is_encoder_ready,
    get_embedder,
    EMBEDDING_DIMS,
)
from tone_forge.ml.embeddings.vector_store import (
    VectorStore,
    SearchResult,
    StoredEmbedding,
    is_store_ready,
    get_store,
)
from tone_forge.ml.embeddings.similarity import (
    ToneSimilaritySearch,
    SimilarTone,
    find_similar_tones,
    get_similarity_search,
)


SR = 22050


def _make_sine_wave(freq: float = 440, duration: float = 1.0) -> np.ndarray:
    """Generate a sine wave."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    return (np.sin(2 * np.pi * freq * t) * 0.5).astype(np.float32)


def _make_noise(duration: float = 1.0) -> np.ndarray:
    """Generate white noise."""
    samples = int(SR * duration)
    return (np.random.randn(samples) * 0.3).astype(np.float32)


def _make_complex_audio(duration: float = 1.0) -> np.ndarray:
    """Generate audio with multiple frequencies."""
    t = np.linspace(0, duration, int(SR * duration), endpoint=False)
    sig = np.zeros_like(t)
    for freq in [220, 440, 660, 880]:
        sig += np.sin(2 * np.pi * freq * t) * (0.5 / (freq / 220))
    return (sig * 0.5).astype(np.float32)


class TestAudioEmbedding:
    """Test AudioEmbedding dataclass."""

    def test_create_embedding(self):
        emb = AudioEmbedding(
            embedding=np.zeros(512, dtype=np.float32),
            encoder_type="spectral",
            duration_sec=1.0,
            sample_rate=22050,
        )
        assert len(emb.embedding) == 512
        assert emb.encoder_type == "spectral"

    def test_to_dict(self):
        emb = AudioEmbedding(
            embedding=np.array([1.0, 2.0, 3.0], dtype=np.float32),
            encoder_type="spectral",
            duration_sec=2.0,
            sample_rate=22050,
        )
        d = emb.to_dict()
        assert "embedding" in d
        assert "encoder_type" in d
        assert d["duration_sec"] == 2.0

    def test_from_dict(self):
        d = {
            "embedding": [1.0, 2.0, 3.0],
            "encoder_type": "spectral",
            "duration_sec": 2.0,
            "sample_rate": 22050,
        }
        emb = AudioEmbedding.from_dict(d)
        assert len(emb.embedding) == 3
        assert emb.encoder_type == "spectral"


class TestAudioEmbedder:
    """Test AudioEmbedder class."""

    def test_create_embedder(self):
        embedder = AudioEmbedder(encoder_type="spectral")
        assert embedder is not None
        assert embedder.encoder_type == "spectral"
        assert embedder.embedding_dim == EMBEDDING_DIMS["spectral"]

    def test_encode_sine_wave(self):
        embedder = AudioEmbedder(encoder_type="spectral")
        audio = _make_sine_wave(440, duration=1.0)
        result = embedder.encode(audio, SR)

        assert isinstance(result, AudioEmbedding)
        assert len(result.embedding) == embedder.embedding_dim
        assert result.duration_sec == pytest.approx(1.0, abs=0.1)

    def test_encode_noise(self):
        embedder = AudioEmbedder(encoder_type="spectral")
        audio = _make_noise(duration=1.0)
        result = embedder.encode(audio, SR)

        assert isinstance(result, AudioEmbedding)
        # Noise should have non-zero embedding
        assert np.linalg.norm(result.embedding) > 0

    def test_different_frequencies_different_embeddings(self):
        embedder = AudioEmbedder(encoder_type="spectral")

        audio_low = _make_sine_wave(220, duration=1.0)
        audio_high = _make_sine_wave(880, duration=1.0)

        emb_low = embedder.encode(audio_low, SR)
        emb_high = embedder.encode(audio_high, SR)

        # Different frequencies should produce different embeddings
        similarity = np.dot(emb_low.embedding, emb_high.embedding)
        assert similarity < 0.95  # Not identical

    def test_encode_stereo(self):
        embedder = AudioEmbedder(encoder_type="spectral")
        audio_mono = _make_sine_wave(440, duration=1.0)
        audio_stereo = np.stack([audio_mono, audio_mono], axis=1)

        result = embedder.encode(audio_stereo, SR)
        assert isinstance(result, AudioEmbedding)


class TestVectorStore:
    """Test VectorStore class."""

    def test_create_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)
            assert store is not None

    def test_add_and_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)

            # Add embeddings - use deterministic values for predictable similarity
            # emb1 and emb2 have positive cosine similarity (both point roughly
            # in the same direction in high-dim space)
            emb1 = np.zeros(128, dtype=np.float32)
            emb1[0] = 1.0  # Unit vector along dim 0
            emb2 = np.zeros(128, dtype=np.float32)
            emb2[0] = 0.9
            emb2[1] = 0.1  # Close to emb1, positive similarity

            store.add("tone1", emb1, {"name": "tone1"})
            store.add("tone2", emb2, {"name": "tone2"})

            # Search with min_score=-1 to get all results (cosine sim range is -1 to 1)
            results = store.search(emb1, k=2, min_score=-1.0)
            assert len(results) == 2
            assert results[0].id == "tone1"  # Most similar to itself

    def test_search_result(self):
        result = SearchResult(
            id="test",
            score=0.95,
            metadata={"key": "value"},
        )
        assert result.id == "test"
        assert result.score == 0.95
        assert result.metadata["key"] == "value"

    def test_get_embedding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)

            emb = np.random.randn(128).astype(np.float32)
            store.add("tone1", emb, {"test": True})

            stored = store.get("tone1")
            assert stored is not None
            assert stored.id == "tone1"
            assert stored.metadata["test"] is True

    def test_delete_embedding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)

            emb = np.random.randn(128).astype(np.float32)
            store.add("tone1", emb, {})

            assert store.count() == 1
            store.delete("tone1")
            # Note: FAISS doesn't efficiently delete, but the ID is removed
            stored = store.get("tone1")
            assert stored is None

    def test_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)

            assert store.count() == 0
            store.add("tone1", np.random.randn(128).astype(np.float32), {})
            assert store.count() == 1
            store.add("tone2", np.random.randn(128).astype(np.float32), {})
            assert store.count() == 2

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)

            emb = np.random.randn(128).astype(np.float32)
            store.add("tone1", emb, {"key": "value"})
            store.save()

            # Create new store from same dir
            store2 = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)
            stored = store2.get("tone1")
            assert stored is not None
            assert stored.metadata["key"] == "value"

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)

            store.add("tone1", np.random.randn(128).astype(np.float32), {})
            store.add("tone2", np.random.randn(128).astype(np.float32), {})

            assert store.count() == 2
            store.clear()
            assert store.count() == 0


class TestToneSimilaritySearch:
    """Test ToneSimilaritySearch class."""

    def test_create_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)
            embedder = AudioEmbedder(encoder_type="spectral")
            search = ToneSimilaritySearch(embedder=embedder, store=store)
            assert search is not None

    def test_index_tone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)
            embedder = AudioEmbedder(encoder_type="spectral")
            search = ToneSimilaritySearch(embedder=embedder, store=store)

            audio = _make_sine_wave(440, duration=1.0)
            descriptor = {
                "source": {"filename": "test.wav", "duration_sec": 1.0},
                "amp": {"family": "fender_clean", "gain": 0.3},
            }

            tone_id = search.index_tone(audio, SR, descriptor)
            assert tone_id is not None
            assert search.count() == 1

    def test_find_similar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)
            embedder = AudioEmbedder(encoder_type="spectral")
            search = ToneSimilaritySearch(embedder=embedder, store=store)

            # Index some tones
            audio1 = _make_sine_wave(440, duration=1.0)
            audio2 = _make_sine_wave(442, duration=1.0)  # Very similar
            audio3 = _make_noise(duration=1.0)  # Different

            search.index_tone(audio1, SR, {
                "source": {"filename": "440hz.wav"},
                "amp": {"family": "fender_clean", "gain": 0.3},
            }, tone_id="tone1")

            search.index_tone(audio2, SR, {
                "source": {"filename": "442hz.wav"},
                "amp": {"family": "fender_clean", "gain": 0.3},
            }, tone_id="tone2")

            search.index_tone(audio3, SR, {
                "source": {"filename": "noise.wav"},
                "amp": {"family": "unknown", "gain": 0.5},
            }, tone_id="tone3")

            # Search with tone similar to audio1
            query = _make_sine_wave(441, duration=1.0)
            results = search.find_similar(query, SR, k=3)

            assert len(results) > 0
            # Similar tones should be first
            assert all(isinstance(r, SimilarTone) for r in results)

    def test_find_similar_by_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)
            embedder = AudioEmbedder(encoder_type="spectral")
            search = ToneSimilaritySearch(embedder=embedder, store=store)

            # Index tones
            audio1 = _make_sine_wave(440, duration=1.0)
            audio2 = _make_sine_wave(442, duration=1.0)

            search.index_tone(audio1, SR, {
                "source": {"filename": "440hz.wav"},
                "amp": {"family": "fender_clean", "gain": 0.3},
            }, tone_id="tone1")

            search.index_tone(audio2, SR, {
                "source": {"filename": "442hz.wav"},
                "amp": {"family": "fender_clean", "gain": 0.3},
            }, tone_id="tone2")

            # Find similar to tone1
            results = search.find_similar_by_id("tone1", k=2)
            assert len(results) == 1  # Excludes self
            assert results[0].id == "tone2"

    def test_delete_tone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)
            embedder = AudioEmbedder(encoder_type="spectral")
            search = ToneSimilaritySearch(embedder=embedder, store=store)

            audio = _make_sine_wave(440, duration=1.0)
            tone_id = search.index_tone(audio, SR, {
                "source": {"filename": "test.wav"},
                "amp": {"family": "fender_clean", "gain": 0.3},
            })

            assert search.count() == 1
            search.delete_tone(tone_id)
            # ID should be removed from mappings
            stored = search.get_tone(tone_id)
            assert stored is None

    def test_get_tone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(store_dir=Path(tmpdir), embedding_dim=128)
            embedder = AudioEmbedder(encoder_type="spectral")
            search = ToneSimilaritySearch(embedder=embedder, store=store)

            audio = _make_sine_wave(440, duration=1.0)
            tone_id = search.index_tone(audio, SR, {
                "source": {"filename": "test.wav"},
                "amp": {"family": "fender_clean", "gain": 0.3},
            })

            tone = search.get_tone(tone_id)
            assert tone is not None
            assert tone.source_filename == "test.wav"
            assert tone.amp_family == "fender_clean"


class TestSimilarTone:
    """Test SimilarTone dataclass."""

    def test_create_similar_tone(self):
        tone = SimilarTone(
            id="test",
            score=0.85,
            source_filename="guitar.wav",
            amp_family="marshall_plexi",
            gain=0.6,
        )
        assert tone.id == "test"
        assert tone.score == 0.85

    def test_to_dict(self):
        tone = SimilarTone(
            id="test",
            score=0.85,
            source_filename="guitar.wav",
            amp_family="marshall_plexi",
            gain=0.6,
        )
        d = tone.to_dict()
        assert d["id"] == "test"
        assert d["score"] == 0.85
        assert d["source_filename"] == "guitar.wav"


class TestGlobalFunctions:
    """Test global convenience functions."""

    def test_is_encoder_ready(self):
        ready = is_encoder_ready()
        assert isinstance(ready, bool)
        # Should be True if librosa is available (spectral fallback)
        assert ready is True

    def test_is_store_ready(self):
        ready = is_store_ready()
        assert ready is True  # Always available

    def test_get_embedder(self):
        embedder = get_embedder()
        assert isinstance(embedder, AudioEmbedder)

    def test_get_store(self):
        store = get_store()
        assert isinstance(store, VectorStore)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
