"""End-to-end smoke test for ``bench.benchmark``.

Builds a single fake fixture in a tmpdir (with a real WAV file
synthesised on the fly), runs ``run_benchmark`` against it under
the default ``DetectorConfig``, and verifies that the resulting
``RunRecord`` JSON has the expected schema + plausible values.

This is the M1.4 "the wiring works" test. It is intentionally
small (one fixture, ~2 seconds of audio) to keep CI fast; the four
real-audio fixtures already get end-to-end coverage from
``test_chord_eval_regression.py``.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest


def _write_minimal_wav(path: Path, *, sr: int, seconds: float) -> None:
    """Write a short sine wave to ``path`` as a 16-bit PCM mono WAV."""
    n = int(sr * seconds)
    t = np.arange(n) / sr
    # Simple C-major-ish chord (C E G) so the detector has *something*
    # to find. The smoke test does NOT assert on the detected label.
    sig = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ) / 3.0
    pcm = np.clip(sig, -1.0, 1.0)
    pcm = (pcm * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _write_minimal_fixture(
    fixtures_dir: Path, *, name: str, audio_path: Path, duration_s: float
) -> None:
    payload = {
        "duration_s": duration_s,
        "regions": [
            {"start": 0.0, "end": duration_s, "label": "C"},
        ],
        "regression_floor_triad_relaxed": 0.0,
        "source_audio_other_stem": str(audio_path),  # absolute path
    }
    (fixtures_dir / f"{name}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_run_benchmark_smoke(tmp_path: Path) -> None:
    pytest.importorskip("librosa")
    from bench.benchmark import run_benchmark
    from bench.store import load_run_record

    sr = 22050
    seconds = 2.0
    audio = tmp_path / "alpha.wav"
    _write_minimal_wav(audio, sr=sr, seconds=seconds)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_minimal_fixture(
        fixtures, name="alpha", audio_path=audio, duration_s=seconds
    )

    output = tmp_path / "run.json"
    record = run_benchmark(
        corpus_dir=fixtures,
        output_path=output,
        require_audio=True,
    )

    # ---- in-memory record looks right ----
    assert record.corpus.n_fixtures == 1
    assert set(record.per_fixture) == {"alpha"}
    fr = record.per_fixture["alpha"]
    assert 0.0 <= fr.wcsr_triad_relaxed <= 1.0
    assert 0.0 <= fr.wcsr_strict <= 1.0
    assert 0.0 <= fr.chord_error_rate <= 1.0
    assert 0.0 <= fr.boundary_iou_0p5 <= 1.0
    assert fr.region_stability_per_min >= 0.0
    assert 0.0 <= fr.expected_calibration_error <= 1.0
    assert fr.wall_seconds > 0.0
    assert fr.peak_rss_mb > 0.0
    assert record.wall_seconds_total >= fr.wall_seconds
    assert record.corpus.wall_seconds_mean == pytest.approx(fr.wall_seconds)
    assert record.corpus.peak_rss_mb_max == pytest.approx(fr.peak_rss_mb)

    # ---- config field contains the default DetectorConfig values ----
    from tone_forge.analysis.detector_config import DetectorConfig
    import dataclasses as _dc
    assert dict(record.config) == _dc.asdict(DetectorConfig())

    # ---- on-disk JSON round-trips ----
    assert output.exists()
    reloaded = load_run_record(output)
    assert reloaded.run_id == record.run_id
    assert reloaded.corpus.n_fixtures == 1
    assert reloaded.per_fixture["alpha"].wcsr_triad_relaxed == pytest.approx(
        fr.wcsr_triad_relaxed
    )

    # ---- top-level JSON has expected keys ----
    raw = json.loads(output.read_text(encoding="utf-8"))
    expected_keys = {
        "run_id", "timestamp_utc", "git_sha", "config", "corpus_dir",
        "per_fixture", "corpus", "wall_seconds_total",
        "rejection_reason", "parent_baseline_run_id", "extra",
    }
    assert expected_keys.issubset(raw.keys())
    assert raw["corpus_dir"] == str(fixtures)


def test_main_quiet_writes_run_record(tmp_path: Path) -> None:
    pytest.importorskip("librosa")
    from bench import benchmark as bm

    sr = 22050
    seconds = 1.5
    audio = tmp_path / "alpha.wav"
    _write_minimal_wav(audio, sr=sr, seconds=seconds)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_minimal_fixture(
        fixtures, name="alpha", audio_path=audio, duration_s=seconds
    )
    output = tmp_path / "run.json"

    rc = bm.main([
        "--corpus", str(fixtures),
        "--output", str(output),
        "--quiet",
    ])
    assert rc == 0
    assert output.exists()


def test_load_detector_config_unknown_field_raises(tmp_path: Path) -> None:
    from bench.benchmark import _load_detector_config

    p = tmp_path / "cfg.json"
    p.write_text('{"not_a_real_field": 0.5}', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown DetectorConfig field"):
        _load_detector_config(p)


def test_load_detector_config_known_field_round_trips(tmp_path: Path) -> None:
    from bench.benchmark import _load_detector_config
    from tone_forge.analysis.detector_config import DetectorConfig

    p = tmp_path / "cfg.json"
    p.write_text('{"diatonic_bias": 0.15}', encoding="utf-8")
    cfg = _load_detector_config(p)
    assert isinstance(cfg, DetectorConfig)
    assert cfg.diatonic_bias == pytest.approx(0.15)
    # Other fields keep defaults.
    assert cfg.cos_cutoff == pytest.approx(DetectorConfig().cos_cutoff)


def test_aggregate_empty_corpus_yields_zero_record() -> None:
    from bench.benchmark import _aggregate

    out = _aggregate({})
    assert out.n_fixtures == 0
    assert out.wcsr_triad_relaxed_mean == 0.0
    assert out.peak_rss_mb_max == 0.0
