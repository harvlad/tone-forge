"""Tests for the M2.4 curator CLI (``python -m bench.corpus``).

Covers all three subcommands by calling ``bench.corpus.main`` directly
with synthetic argv lists (no subprocess overhead). Uses ``capsys`` to
capture stdout/stderr.

``add --measure-floor`` is exercised against a tiny synthetic WAV;
the actual detector still runs but on a short input that finishes
in well under a second.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench import corpus as corpus_cli
from bench.corpus import DEFAULT_FIXTURES_DIR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_v2(audio_rel: str | None = None) -> dict:
    payload: dict = {
        "duration_s": 10.0,
        "regions": [
            {"start": 0.0, "end": 5.0, "label": "C:maj"},
            {"start": 5.0, "end": 10.0, "label": "G:maj"},
        ],
        "regression_floor_triad_relaxed": 0.5,
        "schema_version": 2,
        "split": "test",
        "genre": "rock",
        "license": "first-party",
        "tags": ["synthetic"],
        "curated_by": "tester",
    }
    if audio_rel is not None:
        payload["source_audio_other_stem"] = audio_rel
    return payload


def _write_synthetic_wav(path: Path, duration_s: float = 1.0, sr: int = 22050) -> None:
    """Write a tiny mono sine WAV (440 Hz) suitable for detector smoke tests."""
    import numpy as np
    import soundfile as sf

    t = np.arange(int(sr * duration_s)) / sr
    y = (0.2 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    sf.write(str(path), y, sr)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_good_file_exits_zero(tmp_path: Path, capsys) -> None:
    p = tmp_path / "good.json"
    p.write_text(json.dumps(_minimal_v2()), encoding="utf-8")
    rc = corpus_cli.main(["validate", str(p)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "OK" in captured.out
    assert captured.err == ""


def test_validate_bad_file_exits_one_with_errors(tmp_path: Path, capsys) -> None:
    payload = _minimal_v2()
    payload["split"] = "production"  # not in vocab
    payload["regression_floor_triad_relaxed"] = 1.5  # out of range
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    rc = corpus_cli.main(["validate", str(p)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "not in" in captured.err
    assert "must be in [0, 1]" in captured.err


def test_validate_missing_file_exits_two(tmp_path: Path, capsys) -> None:
    rc = corpus_cli.main(["validate", str(tmp_path / "missing.json")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "not found" in captured.err


def test_validate_invalid_json_exits_one(tmp_path: Path, capsys) -> None:
    p = tmp_path / "notjson.json"
    p.write_text("this is not JSON", encoding="utf-8")
    rc = corpus_cli.main(["validate", str(p)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid JSON" in captured.err


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats_default_corpus_shows_fixtures(capsys) -> None:
    rc = corpus_cli.main(["stats"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "fixtures" in captured.out
    assert "pub_feed" in captured.out
    # All fixtures are split=test, license=first-party
    assert "test" in captured.out
    assert "first-party" in captured.out


def test_stats_json_mode_emits_machine_readable(capsys) -> None:
    rc = corpus_cli.main(["stats", "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["n_fixtures"] >= 22  # corpus may grow
    assert payload["splits"]["test"] >= 22
    assert payload["licenses"]["first-party"] >= 22
    assert payload["genres"]["rock"] == 1
    assert payload["genres"]["synth"] >= 21
    names = sorted(f["name"] for f in payload["fixtures"])
    assert "pub_feed" in names
    assert "demolition_warning" in names
    assert "jump_and_die" in names
    assert "let_s_make_it_pain" in names


def test_stats_split_filter(capsys) -> None:
    rc = corpus_cli.main(["stats", "--split", "train", "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["n_fixtures"] == 0  # no train fixtures yet
    assert payload["splits"] == {}


def test_stats_custom_fixtures_dir(tmp_path: Path, capsys) -> None:
    (tmp_path / "alpha.json").write_text(
        json.dumps(_minimal_v2()), encoding="utf-8"
    )
    rc = corpus_cli.main(
        ["stats", "--fixtures-dir", str(tmp_path), "--json"]
    )
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["n_fixtures"] == 1


def test_stats_missing_dir_exits_two(tmp_path: Path, capsys) -> None:
    rc = corpus_cli.main(
        ["stats", "--fixtures-dir", str(tmp_path / "nope")]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "does not exist" in captured.err


# ---------------------------------------------------------------------------
# add (no --measure-floor)
# ---------------------------------------------------------------------------


def test_add_copies_audio_and_writes_canonical_json(
    tmp_path: Path, capsys
) -> None:
    # Source fixture JSON + a tiny WAV
    src_json = tmp_path / "src.json"
    src_audio = tmp_path / "src_other.wav"
    src_json.write_text(json.dumps({**_minimal_v2(), "song": "My New Song"}), encoding="utf-8")
    _write_synthetic_wav(src_audio, duration_s=0.3)

    fixtures_dir = tmp_path / "fixtures"
    audio_dir = tmp_path / "audio"

    rc = corpus_cli.main(
        [
            "add",
            "--json", str(src_json),
            "--other", str(src_audio),
            "--fixtures-dir", str(fixtures_dir),
            "--audio-dir", str(audio_dir),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "added fixture" in captured.out

    # Slug derives from "My New Song" -> "my_new_song"
    assert (fixtures_dir / "my_new_song.json").exists()
    assert (audio_dir / "my_new_song" / "other.wav").exists()

    # The written JSON points at the canonical audio location
    written = json.loads((fixtures_dir / "my_new_song.json").read_text())
    assert written["source_audio_other_stem"].endswith(
        "my_new_song/other.wav"
    )
    # schema_version stamped (already 2 here but still present)
    assert written["schema_version"] == 2
    # added_at_unix stamped
    assert isinstance(written["added_at_unix"], int)
    assert written["added_at_unix"] > 0


def test_add_with_name_override(tmp_path: Path, capsys) -> None:
    src_json = tmp_path / "src.json"
    src_audio = tmp_path / "src.wav"
    src_json.write_text(json.dumps(_minimal_v2()), encoding="utf-8")
    _write_synthetic_wav(src_audio, duration_s=0.3)

    fixtures_dir = tmp_path / "fixtures"
    audio_dir = tmp_path / "audio"

    rc = corpus_cli.main(
        [
            "add",
            "--json", str(src_json),
            "--other", str(src_audio),
            "--name", "custom_slug",
            "--fixtures-dir", str(fixtures_dir),
            "--audio-dir", str(audio_dir),
        ]
    )
    assert rc == 0
    assert (fixtures_dir / "custom_slug.json").exists()
    assert (audio_dir / "custom_slug" / "other.wav").exists()


def test_add_with_bass_stem(tmp_path: Path, capsys) -> None:
    src_json = tmp_path / "src.json"
    src_other = tmp_path / "other.wav"
    src_bass = tmp_path / "bass.wav"
    src_json.write_text(json.dumps(_minimal_v2()), encoding="utf-8")
    _write_synthetic_wav(src_other, duration_s=0.3)
    _write_synthetic_wav(src_bass, duration_s=0.3)

    fixtures_dir = tmp_path / "fixtures"
    audio_dir = tmp_path / "audio"

    rc = corpus_cli.main(
        [
            "add",
            "--json", str(src_json),
            "--other", str(src_other),
            "--bass", str(src_bass),
            "--name", "withbass",
            "--fixtures-dir", str(fixtures_dir),
            "--audio-dir", str(audio_dir),
        ]
    )
    assert rc == 0
    assert (audio_dir / "withbass" / "bass.wav").exists()
    written = json.loads((fixtures_dir / "withbass.json").read_text())
    assert written["source_audio_bass_stem"].endswith("withbass/bass.wav")


def test_add_rejects_invalid_json(tmp_path: Path, capsys) -> None:
    src_json = tmp_path / "src.json"
    src_audio = tmp_path / "src.wav"
    bad = _minimal_v2()
    bad["split"] = "production"
    src_json.write_text(json.dumps(bad), encoding="utf-8")
    _write_synthetic_wav(src_audio, duration_s=0.3)

    rc = corpus_cli.main(
        [
            "add",
            "--json", str(src_json),
            "--other", str(src_audio),
            "--name", "x",
            "--fixtures-dir", str(tmp_path / "fx"),
            "--audio-dir", str(tmp_path / "au"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert "schema validation failed" in captured.err


def test_add_missing_json_exits_two(tmp_path: Path, capsys) -> None:
    src_audio = tmp_path / "src.wav"
    _write_synthetic_wav(src_audio, duration_s=0.3)
    rc = corpus_cli.main(
        [
            "add",
            "--json", str(tmp_path / "missing.json"),
            "--other", str(src_audio),
            "--name", "x",
            "--fixtures-dir", str(tmp_path / "fx"),
            "--audio-dir", str(tmp_path / "au"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "--json not found" in captured.err


def test_add_missing_audio_exits_two(tmp_path: Path, capsys) -> None:
    src_json = tmp_path / "src.json"
    src_json.write_text(json.dumps(_minimal_v2()), encoding="utf-8")
    rc = corpus_cli.main(
        [
            "add",
            "--json", str(src_json),
            "--other", str(tmp_path / "missing.wav"),
            "--name", "x",
            "--fixtures-dir", str(tmp_path / "fx"),
            "--audio-dir", str(tmp_path / "au"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "--other not found" in captured.err


# ---------------------------------------------------------------------------
# add --measure-floor
# ---------------------------------------------------------------------------


def test_add_measure_floor_pins_value(tmp_path: Path, capsys) -> None:
    """End-to-end: add a synthetic fixture, measure floor, verify
    the value is in ``[0.0, 1.0]`` and stored to two decimals.

    A pure-440Hz sine isn't a chord, so the measured WCSR could be
    near zero. We only assert that the floor is a well-formed
    rounded-down value, not a specific number.
    """
    # Reasonable duration so the detector has frames to chew on.
    src_audio = tmp_path / "src.wav"
    _write_synthetic_wav(src_audio, duration_s=4.0)

    src_json = tmp_path / "src.json"
    payload = _minimal_v2()
    # Regions must fit in duration_s = 10.0 (default in _minimal_v2)
    # but the actual audio is 4.0s. The detector trims internally;
    # WCSR is computed over the JSON's stated duration_s.
    src_json.write_text(json.dumps(payload), encoding="utf-8")

    rc = corpus_cli.main(
        [
            "add",
            "--json", str(src_json),
            "--other", str(src_audio),
            "--name", "synth1",
            "--measure-floor",
            "--fixtures-dir", str(tmp_path / "fx"),
            "--audio-dir", str(tmp_path / "au"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    written = json.loads((tmp_path / "fx" / "synth1.json").read_text())
    floor = written["regression_floor_triad_relaxed"]
    assert 0.0 <= floor <= 1.0
    # Rounded down to nearest 0.01: floor * 100 is an integer.
    assert abs(round(floor * 100.0) - floor * 100.0) < 1e-9


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def test_no_subcommand_exits_two(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        corpus_cli.main([])
    # argparse exits 2 on missing required positional.
    assert exc_info.value.code == 2


def test_unknown_subcommand_exits_two(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        corpus_cli.main(["bogus"])
    assert exc_info.value.code == 2


def test_main_corpus_dispatcher_route(capsys) -> None:
    """``python -m bench corpus stats`` should reach corpus.main."""
    from bench import __main__ as bench_main

    rc = bench_main.main(["corpus", "stats", "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["n_fixtures"] >= 22  # corpus may grow
