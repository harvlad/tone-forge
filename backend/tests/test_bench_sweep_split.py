"""``bench.sweep --split`` end-to-end tests (M2.5).

Verifies the `--split` CLI flag and `splits=` kwarg on
``run_sweep``:

* The same splits filter is applied to the auto-baseline and every
  candidate run (so corpus deltas are apples-to-apples).
* The baseline and every candidate RunRecord records the splits
  filter on its on-disk JSON.
* The CLI flag parses and propagates.

Uses synthetic single-fixture corpora plus a 2-point YAML sweep
space so the test runs in a few seconds.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest


def _write_minimal_wav(path: Path, *, sr: int, seconds: float) -> None:
    n = int(sr * seconds)
    t = np.arange(n) / sr
    sig = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ) / 3.0
    pcm = (np.clip(sig, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _write_fixture(
    fixtures_dir: Path,
    *,
    name: str,
    audio_path: Path,
    duration_s: float,
    split: str,
) -> None:
    payload = {
        "schema_version": 2,
        "split": split,
        "duration_s": duration_s,
        "regions": [
            {"start": 0.0, "end": duration_s, "label": "C"},
        ],
        "regression_floor_triad_relaxed": 0.0,
        "source_audio_other_stem": str(audio_path),
    }
    (fixtures_dir / f"{name}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _build_split_corpus(tmp_path: Path) -> Path:
    """Corpus with one test fixture and one train fixture."""
    pytest.importorskip("librosa")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()

    for name, split in (("test_a", "test"), ("train_a", "train")):
        wav = audio_dir / f"{name}.wav"
        _write_minimal_wav(wav, sr=22050, seconds=1.0)
        _write_fixture(
            fixtures, name=name, audio_path=wav,
            duration_s=1.0, split=split,
        )
    return fixtures


def _write_tiny_space(path: Path) -> None:
    """Two-candidate grid that won't reject candidates spuriously."""
    path.write_text(
        "strategy: grid\n"
        "acceptance:\n"
        "  corpus_metric: wcsr_triad_relaxed_mean\n"
        "  corpus_must_strictly_improve: false\n"
        "  max_per_fixture_drop_pp: 100.0\n"
        "  max_runtime_factor: 100.0\n"
        "  max_memory_factor: 100.0\n"
        "parameters:\n"
        "  diatonic_bias: {type: float, range: [0.10, 0.15], step: 0.05}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# run_sweep(splits=...)
# ---------------------------------------------------------------------------


def test_run_sweep_split_test_isolates_test_fixtures(tmp_path: Path) -> None:
    """`splits=('test',)` runs baseline + candidates over test only."""
    from bench.sweep import run_sweep
    from bench.store import load_run_record

    fixtures = _build_split_corpus(tmp_path)
    space = tmp_path / "space.yaml"
    _write_tiny_space(space)
    out = tmp_path / "sweep_out"

    summary = run_sweep(
        space,
        output_dir=out,
        corpus_dir=fixtures,
        splits=("test",),
    )
    assert summary["n_candidates"] == 2

    # Baseline ran over the test split only.
    baseline = load_run_record(out / "baseline.json")
    assert baseline.splits == ("test",)
    assert baseline.corpus.n_fixtures == 1
    assert set(baseline.per_fixture) == {"test_a"}

    # Every candidate ran over the same test split.
    for i in range(summary["n_candidates"]):
        cand = load_run_record(out / f"candidate_{i:04d}.json")
        assert cand.splits == ("test",)
        assert set(cand.per_fixture) == {"test_a"}


def test_run_sweep_no_split_loads_all(tmp_path: Path) -> None:
    """Without splits=, the baseline + candidates load every fixture."""
    from bench.sweep import run_sweep
    from bench.store import load_run_record

    fixtures = _build_split_corpus(tmp_path)
    space = tmp_path / "space.yaml"
    _write_tiny_space(space)
    out = tmp_path / "sweep_out"

    summary = run_sweep(
        space,
        output_dir=out,
        corpus_dir=fixtures,
    )
    baseline = load_run_record(out / "baseline.json")
    assert baseline.splits is None
    assert baseline.corpus.n_fixtures == 2
    assert summary["n_candidates"] == 2

    cand0 = load_run_record(out / "candidate_0000.json")
    assert cand0.splits is None
    assert cand0.corpus.n_fixtures == 2


def test_run_sweep_empty_split_raises(tmp_path: Path) -> None:
    """A split filter that excludes every fixture surfaces the error."""
    from bench.sweep import run_sweep

    fixtures = _build_split_corpus(tmp_path)
    space = tmp_path / "space.yaml"
    _write_tiny_space(space)

    with pytest.raises(ValueError, match="no fixtures match split filter"):
        run_sweep(
            space,
            output_dir=tmp_path / "sweep_out",
            corpus_dir=fixtures,
            splits=("holdout",),
        )


# ---------------------------------------------------------------------------
# CLI (--split flag)
# ---------------------------------------------------------------------------


def test_sweep_cli_split_flag_parses() -> None:
    from bench.sweep import _build_argparser

    ap = _build_argparser()
    args = ap.parse_args(["x.yaml", "--split", "test"])
    assert args.split == ["test"]
    args2 = ap.parse_args([
        "x.yaml", "--split", "test", "--split", "val",
    ])
    assert args2.split == ["test", "val"]
    args3 = ap.parse_args(["x.yaml"])
    assert args3.split is None


def test_sweep_cli_split_invalid_choice_rejected() -> None:
    from bench.sweep import _build_argparser

    ap = _build_argparser()
    with pytest.raises(SystemExit):
        ap.parse_args(["x.yaml", "--split", "bogus"])


def test_sweep_cli_main_propagates_split(tmp_path: Path) -> None:
    """`python -m bench.sweep ... --split test` writes RunRecords
    whose `splits` field is `("test",)`."""
    from bench import sweep as sw
    from bench.store import load_run_record

    fixtures = _build_split_corpus(tmp_path)
    space = tmp_path / "space.yaml"
    _write_tiny_space(space)
    out = tmp_path / "sweep_out"

    rc = sw.main([
        str(space),
        "--corpus", str(fixtures),
        "--output", str(out),
        "--split", "test",
    ])
    assert rc == 0

    baseline = load_run_record(out / "baseline.json")
    assert baseline.splits == ("test",)
    cand0 = load_run_record(out / "candidate_0000.json")
    assert cand0.splits == ("test",)
