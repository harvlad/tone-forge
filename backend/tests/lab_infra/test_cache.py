"""The dangerous failure modes: cache identity, invalidation, resume."""
from __future__ import annotations

import pandas as pd
import pytest

from lab import cache, runner, schema


def _stem(fake_corpus, i=0):
    return fake_corpus[fake_corpus["split"] == "validation"].iloc[i]


def test_cache_hit_prevents_inference(fake_corpus, fake_adapter):
    row = _stem(fake_corpus)
    df1 = runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == 1
    assert len(df1) == 2
    df2 = runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == 1, "second call must be a pure cache hit"
    pd.testing.assert_frame_equal(df1, df2)


def test_changed_audio_invalidates(fake_corpus, fake_adapter):
    row = _stem(fake_corpus).copy()
    runner.get_predictions("fake_model", row)
    row["audio_hash"] = "deadbeef" * 8
    runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == 2


def test_changed_checkpoint_invalidates(fake_corpus, fake_adapter):
    row = _stem(fake_corpus)
    runner.get_predictions("fake_model", row)
    fake_adapter.checkpoint = "ckpt-B"
    runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == 2


def test_changed_inference_config_invalidates(fake_corpus, fake_adapter, monkeypatch):
    row = _stem(fake_corpus)
    runner.get_predictions("fake_model", row)
    monkeypatch.setattr(fake_adapter, "inference_config",
                        lambda self: {"api": "fake", "threshold": 0.7})
    runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == 2


def test_eval_tolerance_change_does_not_invalidate(fake_corpus, fake_adapter):
    """Changing onset tolerance recomputes matching, never inference."""
    from lab import analysis
    stems = fake_corpus[fake_corpus["split"] == "validation"]
    for _, row in stems.iterrows():
        runner.get_predictions("fake_model", row)
    calls_after_inference = fake_adapter.calls
    r100 = analysis.evaluate_model("fake_model", stems, onset_tolerance=0.1)
    r50 = analysis.evaluate_model("fake_model", stems, onset_tolerance=0.05)
    assert fake_adapter.calls == calls_after_inference
    assert r100["n_stems_evaluated"] == len(stems)
    assert r50["n_stems_evaluated"] == len(stems)


def test_interrupted_run_resumes_missing_only(fake_corpus, fake_adapter):
    stems = fake_corpus[fake_corpus["split"] == "validation"]
    # simulate crash: only first 2 stems processed
    for _, row in stems.head(2).iterrows():
        runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == 2
    missing = runner.missing_stems("fake_model", stems)
    assert len(missing) == len(stems) - 2
    for _, row in missing.iterrows():
        runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == len(stems)
    assert len(runner.missing_stems("fake_model", stems)) == 0


def test_corrupt_cache_treated_as_miss(fake_corpus, fake_adapter):
    row = _stem(fake_corpus)
    runner.get_predictions("fake_model", row)
    from lab.adapters import get_adapter
    key = runner.stem_prediction_key(get_adapter("fake_model"), row)
    pq, _ = cache._paths("fake_model", key)
    pq.write_bytes(b"not parquet")
    assert cache.load("fake_model", key) is None
    runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == 2


def test_failure_recorded_and_retried(fake_corpus, fake_adapter, monkeypatch):
    row = _stem(fake_corpus)

    def boom(self, audio_path):
        type(self).calls += 1
        raise RuntimeError("OOM")

    monkeypatch.setattr(fake_adapter, "predict", boom)
    assert runner.get_predictions("fake_model", row) is None
    from lab.adapters import get_adapter
    key = runner.stem_prediction_key(get_adapter("fake_model"), row)
    meta = cache.load_meta("fake_model", key)
    assert meta["status"] == "failed"
    assert "OOM" in meta["error"]
    assert not cache.has("fake_model", key), "failure is not a cache hit"
    # retried on next run
    missing = runner.missing_stems("fake_model", stems=fake_corpus[
        fake_corpus["stem_id"] == row["stem_id"]])
    assert len(missing) == 1


def test_invalid_timing_detected(fake_corpus, fake_adapter):
    """The historical 1.21x timing-stretch bug class must be caught."""
    row = _stem(fake_corpus)  # duration 1.0s
    fake_adapter.notes_to_return = [
        {"pitch": 60, "onset": 100.0, "offset": 101.0}]  # way past 1.5x duration
    assert runner.get_predictions("fake_model", row) is None
    from lab.adapters import get_adapter
    key = runner.stem_prediction_key(get_adapter("fake_model"), row)
    assert cache.load_meta("fake_model", key)["status"] == "failed"


def test_empty_predictions_are_valid_cache_entries(fake_corpus, fake_adapter):
    row = _stem(fake_corpus)
    fake_adapter.notes_to_return = []
    df = runner.get_predictions("fake_model", row)
    assert df is not None and len(df) == 0
    assert fake_adapter.calls == 1
    runner.get_predictions("fake_model", row)
    assert fake_adapter.calls == 1, "empty result must still be a cache hit"


def test_force_regenerates_but_preserves_old_entry(fake_corpus, fake_adapter):
    row = _stem(fake_corpus)
    runner.get_predictions("fake_model", row)
    runner.get_predictions("fake_model", row, force=True)
    assert fake_adapter.calls == 2
    from lab import config
    invalids = list((config.PREDICTIONS_DIR / "fake_model").glob("*.invalid.*"))
    assert invalids, "invalidated entries are moved aside, not deleted"


def test_duplicate_prediction_detection():
    df = schema.normalize_predictions([
        {"pitch": 60, "onset": 1.0, "offset": 2.0},
        {"pitch": 60, "onset": 1.0, "offset": 2.0},
        {"pitch": 62, "onset": 1.5, "offset": 2.0},
    ])
    dupes = schema.find_duplicates(df)
    assert len(dupes) == 2


def test_pitch_range_validation():
    df = schema.normalize_predictions([{"pitch": 200, "onset": 0.1, "offset": 0.2}])
    with pytest.raises(schema.InvalidPredictionError):
        schema.validate_predictions(df)
