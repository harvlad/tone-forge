"""Unit coverage for ``scripts/render_chain_references.py``.

The script is the upstream of every bundled
``<chain_id>.fingerprint.json``: it consumes a directory of rendered
reference WAVs and writes the JSON files that
``test_monitor_fingerprints.py`` then validates at the data boundary.

This file pins the *producer* side of that contract — the script's
output, when given a successful feature extraction, conforms to the
schema the catalog loader requires. That closes the loop: the
producer can't drift away from the schema, and the consumer
(``guitar_catalog._load_entry``) can't drift away from the producer,
because both sides are gated.

Three slices:

  1. **Pure helpers.** ``_resolve_targets``, ``_find_audio_for_chain``,
     ``_read_existing_source`` — small, no-IO-required surfaces; one
     test per branch.
  2. **``_render_fingerprint`` shape.** Mock the audio feature
     extractor so the test doesn't depend on librosa / numpy / a real
     WAV. Assert the JSON shape carries the eight feature keys, the
     validity mask, the provenance fields, and the YAML-side
     ``display_name`` / ``family`` baked in (which is what keeps the
     tone -> monitor import boundary closed; see commit ``c6ff8d1``).
  3. **``render`` round-trip.** End-to-end: synthesize a WAV-shaped
     placeholder on disk + mock the extractor, run ``render`` into a
     tmp out-dir, then parse the written JSON back through
     ``guitar_catalog._load_entry`` to assert the producer's output
     satisfies the consumer's schema. Also covers the
     missing-audio-dir failure mode and the per-chain WAV-missing
     skip.

Not covered here (intentionally):

  * The librosa-backed ``_extract_query_fingerprint`` path. Exercising
    real audio belongs in a heavier integration test; the script's
    contract with the catalog is structural, and structural drift is
    what this file is meant to catch.
  * The CLI argparse surface. ``main()`` is a thin wrapper around
    ``render()``; testing argparse would be testing stdlib.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Tuple

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import render_chain_references as rcr
from tone_forge.monitor.loader import list_chain_ids
from tone_forge.tone import guitar_catalog as gc


# A real chain id we can exercise without ambiguity. The bank ships at
# least this many; if it ever doesn't, the loader-side tests fail
# first.
_KNOWN_CHAIN_ID = "tfc.clean_strat"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_resolve_targets_defaults_to_full_bank() -> None:
    """Passing ``None`` should mean "every chain in the bank"."""
    assert rcr._resolve_targets(None) == list_chain_ids()


def test_resolve_targets_accepts_known_id() -> None:
    assert rcr._resolve_targets([_KNOWN_CHAIN_ID]) == [_KNOWN_CHAIN_ID]


def test_resolve_targets_rejects_unknown_id() -> None:
    """An unknown id should fail loudly — silent skip would mask
    typos in the operator's invocation."""
    with pytest.raises(SystemExit) as exc:
        rcr._resolve_targets(["tfc.does_not_exist"])
    assert "Unknown chain id" in str(exc.value)


def test_find_audio_prefers_wav(tmp_path: Path) -> None:
    """Even though multiple extensions are accepted, ``.wav`` is the
    canonical render output and should resolve first when present."""
    (tmp_path / f"{_KNOWN_CHAIN_ID}.wav").write_bytes(b"")
    (tmp_path / f"{_KNOWN_CHAIN_ID}.flac").write_bytes(b"")
    found = rcr._find_audio_for_chain(tmp_path, _KNOWN_CHAIN_ID)
    assert found is not None
    assert found.suffix == ".wav"


def test_find_audio_accepts_aiff_and_flac(tmp_path: Path) -> None:
    """The operator may render through any of the accepted formats —
    don't force a re-encode just because Connect bounced to AIFF."""
    for ext in (".aif", ".aiff", ".flac"):
        chain_dir = tmp_path / ext.lstrip(".")
        chain_dir.mkdir()
        (chain_dir / f"{_KNOWN_CHAIN_ID}{ext}").write_bytes(b"")
        found = rcr._find_audio_for_chain(chain_dir, _KNOWN_CHAIN_ID)
        assert found is not None
        assert found.suffix == ext


def test_find_audio_returns_none_when_missing(tmp_path: Path) -> None:
    """No matching file -> ``None`` (caller logs and skips)."""
    assert rcr._find_audio_for_chain(tmp_path, _KNOWN_CHAIN_ID) is None


def test_read_existing_source_none_when_file_missing(tmp_path: Path) -> None:
    """First-render case: no prior JSON at the output path."""
    assert rcr._read_existing_source(tmp_path / "nope.json") is None


def test_read_existing_source_reports_placeholder(tmp_path: Path) -> None:
    out_path = tmp_path / "fp.json"
    out_path.write_text(json.dumps({"source": rcr.SOURCE_PLACEHOLDER}))
    assert rcr._read_existing_source(out_path) == rcr.SOURCE_PLACEHOLDER


def test_read_existing_source_reports_rendered(tmp_path: Path) -> None:
    out_path = tmp_path / "fp.json"
    out_path.write_text(json.dumps({"source": rcr.SOURCE_RENDERED}))
    assert rcr._read_existing_source(out_path) == rcr.SOURCE_RENDERED


def test_read_existing_source_malformed_json_returns_none(tmp_path: Path) -> None:
    """If the file is corrupt, treat it as "no prior render" rather
    than crashing the operator's batch — they'd lose progress on
    other chains in the same invocation."""
    out_path = tmp_path / "fp.json"
    out_path.write_text("{not valid json")
    assert rcr._read_existing_source(out_path) is None


# ---------------------------------------------------------------------------
# _render_fingerprint shape
# ---------------------------------------------------------------------------


def _stub_features() -> Tuple[np.ndarray, np.ndarray]:
    """An arbitrary but well-shaped feature pair for the mock."""
    n = len(gc._FEATURE_KEYS)
    vector = np.arange(n, dtype=float) * 0.1
    validity = np.ones(n, dtype=bool)
    return vector, validity


def test_render_fingerprint_returns_none_on_extractor_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contract with the orchestrator is "None means skip"; if
    the extractor failed (e.g. unreadable audio), the script must
    not write garbage."""
    monkeypatch.setattr(gc, "_extract_query_fingerprint", lambda _p: None)
    payload = rcr._render_fingerprint(
        _KNOWN_CHAIN_ID, "Display", "clean", Path("does_not_matter.wav")
    )
    assert payload is None


def test_render_fingerprint_emits_full_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON shape must carry every field the catalog loader and
    the monitor_fingerprints CI gate read: identity (chain_id,
    display_name, family), provenance (source, source_note,
    rendered_at, rendered_from), and measurement (features +
    feature_validity, both keyed by all eight ``_FEATURE_KEYS``)."""
    monkeypatch.setattr(
        gc, "_extract_query_fingerprint", lambda _p: _stub_features()
    )
    payload = rcr._render_fingerprint(
        _KNOWN_CHAIN_ID, "Clean Strat", "clean", Path("ref.wav")
    )
    assert payload is not None

    # Identity — these three are the user-facing contract.
    assert payload["chain_id"] == _KNOWN_CHAIN_ID
    assert payload["display_name"] == "Clean Strat"
    assert payload["family"] == "clean"

    # Provenance — the audit trail that distinguishes measured from
    # placeholder fingerprints. The catalog never reads these, but
    # operators do when triaging a drift.
    assert payload["source"] == rcr.SOURCE_RENDERED
    assert isinstance(payload["source_note"], str)
    assert "ref.wav" in payload["source_note"]
    assert payload["rendered_from"] == "ref.wav"
    assert payload["rendered_at"].endswith("Z")

    # Measurement — the actual fingerprint. All eight keys present
    # as floats; validity present as bools.
    features = payload["features"]
    assert isinstance(features, dict)
    assert set(features.keys()) == set(gc._FEATURE_KEYS)
    assert all(isinstance(v, float) for v in features.values())

    validity = payload["feature_validity"]
    assert isinstance(validity, dict)
    assert set(validity.keys()) == set(gc._FEATURE_KEYS)
    assert all(isinstance(v, bool) for v in validity.values())


# ---------------------------------------------------------------------------
# render() round-trip
# ---------------------------------------------------------------------------


def test_render_missing_audio_dir_returns_nonzero(tmp_path: Path) -> None:
    """Operator gave a bad path -> non-zero exit, no files written."""
    out_dir = tmp_path / "out"
    rc = rcr.render(
        audio_dir=tmp_path / "does_not_exist",
        out_dir=out_dir,
        chain_ids=[_KNOWN_CHAIN_ID],
    )
    assert rc == 1
    # Out dir not even created when the input was bad.
    assert not out_dir.exists()


def test_render_missing_wav_skips_chain_with_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The orchestrator processes all chains in one pass and reports
    every gap at the end. A missing WAV for one chain shouldn't
    crash; it should be logged and contribute to a non-zero exit
    so CI catches the gap."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    out_dir = tmp_path / "out"

    # Extractor would succeed, but the WAV isn't there to begin with.
    monkeypatch.setattr(
        gc, "_extract_query_fingerprint", lambda _p: _stub_features()
    )
    rc = rcr.render(
        audio_dir=audio_dir,
        out_dir=out_dir,
        chain_ids=[_KNOWN_CHAIN_ID],
    )
    assert rc == 1
    assert not (out_dir / f"{_KNOWN_CHAIN_ID}.fingerprint.json").exists()


def test_render_writes_json_that_passes_catalog_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a successful render must produce JSON the runtime
    catalog can parse. This is the single most important assertion
    in the file — it pins the producer/consumer schema contract
    that the bundled fingerprints in ``monitor/chains/`` depend on.
    """
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / f"{_KNOWN_CHAIN_ID}.wav").write_bytes(b"")  # presence only
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        gc, "_extract_query_fingerprint", lambda _p: _stub_features()
    )
    rc = rcr.render(
        audio_dir=audio_dir,
        out_dir=out_dir,
        chain_ids=[_KNOWN_CHAIN_ID],
    )
    assert rc == 0

    out_path = out_dir / f"{_KNOWN_CHAIN_ID}.fingerprint.json"
    assert out_path.is_file()

    # The acceptance check — feed the script's output back through
    # the catalog loader. If this throws, the producer drifted from
    # the consumer.
    entry = gc._load_entry(out_path)
    assert entry.chain_id == _KNOWN_CHAIN_ID
    assert entry.vector.shape == (len(gc._FEATURE_KEYS),)
    assert entry.validity.shape == (len(gc._FEATURE_KEYS),)


def test_render_dry_run_does_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--dry-run`` is the operator's safety net before committing
    to overwriting placeholders. It must print the payload, write
    nothing, and still return success."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / f"{_KNOWN_CHAIN_ID}.wav").write_bytes(b"")
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        gc, "_extract_query_fingerprint", lambda _p: _stub_features()
    )
    rc = rcr.render(
        audio_dir=audio_dir,
        out_dir=out_dir,
        chain_ids=[_KNOWN_CHAIN_ID],
        dry_run=True,
    )
    assert rc == 0
    assert not (out_dir / f"{_KNOWN_CHAIN_ID}.fingerprint.json").exists()
    captured = capsys.readouterr()
    assert _KNOWN_CHAIN_ID in captured.out
    assert rcr.SOURCE_RENDERED in captured.out
