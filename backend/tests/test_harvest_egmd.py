"""E-GMD harvester (Stage A) — GM map, coincidence rules, capping.

No network + no multi-GB download: exercises the pure MIDI->onset path
against a tiny checked-in fixture (backend/tests/fixtures/egmd/tiny.mid)
plus synthetic in-memory MIDIs. Feature extraction (Stage B) is Swift
and covered by HarvesterTests.swift.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pretty_midi = pytest.importorskip("pretty_midi")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import harvest_egmd as h  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "egmd"
TINY_MID = FIXTURES / "tiny.mid"
TINY_WAV = FIXTURES / "tiny.wav"


def _midi_with(notes, path: Path) -> Path:
    """Write a drum MIDI from (pitch, start) tuples (0.05s notes)."""
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=0, is_drum=True)
    for pitch, start in notes:
        inst.notes.append(
            pretty_midi.Note(velocity=100, pitch=pitch, start=start, end=start + 0.05)
        )
    pm.instruments.append(inst)
    pm.write(str(path))
    return path


# ---------------------------------------------------------------------------
# GM map
# ---------------------------------------------------------------------------


def test_gm_map_values_are_all_valid_roles():
    assert set(h.GM_NOTE_TO_ROLE.values()) <= set(h.DRUM_ROLES)
    # Every role except (optionally) some is reachable from the map.
    reachable = set(h.GM_NOTE_TO_ROLE.values())
    for core in ("kick", "snare", "closed_hat", "open_hat", "clap", "rim", "perc"):
        assert core in reachable, f"{core} unreachable from GM map"


def test_manifest_schema(tmp_path):
    rows = h.build_manifest_rows(TINY_MID, TINY_WAV)
    out = tmp_path / "m.csv"
    h.write_manifest(rows, out)
    lines = out.read_text().strip().splitlines()
    assert lines[0] == "wav_path,onset_sec,role"
    for line in lines[1:]:
        cols = line.split(",")
        assert len(cols) == 3
        assert cols[2] in h.DRUM_ROLES
        float(cols[1])  # onset parses


# ---------------------------------------------------------------------------
# Coincidence + unmapped rules (against the tiny fixture)
# ---------------------------------------------------------------------------


def test_fixture_default_rows():
    rows = h.build_manifest_rows(TINY_MID, TINY_WAV)
    got = [(round(r.onset_sec, 2), r.role) for r in rows]
    # kick@.10 snare@.30 hat@.50; .70 kick+snare skipped; .90 toms->one perc;
    # 1.10 cowbell(unmapped) skipped.
    assert got == [(0.10, "kick"), (0.30, "snare"), (0.50, "closed_hat"),
                   (0.90, "perc")]


def test_multi_role_coincidence_skipped(tmp_path):
    mid = _midi_with([(36, 0.5), (38, 0.5)], tmp_path / "a.mid")
    assert h.build_manifest_rows(mid, tmp_path / "a.wav") == []


def test_same_role_coincidence_merges_to_one(tmp_path):
    mid = _midi_with([(41, 0.5), (43, 0.5), (45, 0.5)], tmp_path / "b.mid")
    rows = h.build_manifest_rows(mid, tmp_path / "b.wav")
    assert len(rows) == 1
    assert rows[0].role == "perc"
    assert rows[0].onset_sec == pytest.approx(0.5)


def test_unmapped_skipped_by_default(tmp_path):
    mid = _midi_with([(56, 0.5)], tmp_path / "c.mid")  # cowbell
    assert h.build_manifest_rows(mid, tmp_path / "c.wav") == []


def test_unmapped_folded_into_perc(tmp_path):
    mid = _midi_with([(56, 0.5)], tmp_path / "d.mid")
    rows = h.build_manifest_rows(mid, tmp_path / "d.wav", unmapped="perc")
    assert [r.role for r in rows] == ["perc"]


def test_epsilon_separates_distinct_onsets(tmp_path):
    # 0.5 and 0.52 are >10ms apart -> two separate kick onsets.
    mid = _midi_with([(36, 0.5), (36, 0.52)], tmp_path / "e.mid")
    rows = h.build_manifest_rows(mid, tmp_path / "e.wav", epsilon=0.010)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# Per-role capping determinism
# ---------------------------------------------------------------------------


def _rows(role: str, count: int):
    return [h.ManifestRow(f"/w/{role}.wav", i * 0.25, role) for i in range(count)]


def test_cap_per_role_limits_and_is_deterministic():
    rows = _rows("kick", 100) + _rows("snare", 5)
    a = h._cap_per_role(rows, cap=10, seed=42)
    b = h._cap_per_role(rows, cap=10, seed=42)
    kicks = [r for r in a if r.role == "kick"]
    snares = [r for r in a if r.role == "snare"]
    assert len(kicks) == 10           # capped
    assert len(snares) == 5           # under cap, untouched
    assert a == b                     # deterministic across runs


def test_cap_per_role_seed_changes_selection():
    rows = _rows("kick", 100)
    a = h._cap_per_role(rows, cap=10, seed=1)
    b = h._cap_per_role(rows, cap=10, seed=2)
    assert a != b


# ---------------------------------------------------------------------------
# End-to-end harvest via --local-root (no download)
# ---------------------------------------------------------------------------


def test_harvest_local_root(tmp_path):
    rows = h.harvest(
        FIXTURES, epsilon=0.010, unmapped="skip", limit=0,
        max_per_role=0, seed=42,
    )
    roles = {r.role for r in rows}
    assert roles == {"kick", "snare", "closed_hat", "perc"}
    assert all(r.wav_path.endswith("tiny.wav") for r in rows)


# ---------------------------------------------------------------------------
# Streaming-subset member selection (pure; no network / no remotezip)
# ---------------------------------------------------------------------------


# A miniature of the real archive layout: an index CSV plus per-clip
# midi/audio members under the same top-level dir.
_NAMES = [
    "e-gmd-v1.0.0/e-gmd-v1.0.0.csv",
    "e-gmd-v1.0.0/drummer1/session1/1_a.midi",
    "e-gmd-v1.0.0/drummer1/session1/1_a.wav",
    "e-gmd-v1.0.0/drummer1/session1/2_b.midi",
    "e-gmd-v1.0.0/drummer1/session1/2_b.wav",
    "e-gmd-v1.0.0/drummer1/session1/3_c.midi",  # audio missing -> unusable
]


def test_find_index_name():
    assert h._find_index_name(_NAMES) == "e-gmd-v1.0.0/e-gmd-v1.0.0.csv"
    assert h._find_index_name(["a/b.wav", "a/c.midi"]) is None


def test_pairs_from_index_joins_and_limits():
    rows = [
        {"midi_filename": "drummer1/session1/1_a.midi",
         "audio_filename": "drummer1/session1/1_a.wav"},
        {"midi_filename": "drummer1/session1/2_b.midi",
         "audio_filename": "drummer1/session1/2_b.wav"},
        {"midi_filename": "drummer1/session1/3_c.midi",
         "audio_filename": "drummer1/session1/3_c.wav"},  # wav absent -> dropped
    ]
    pairs = h._pairs_from_index(
        "e-gmd-v1.0.0/e-gmd-v1.0.0.csv", rows, set(_NAMES), limit=0)
    assert pairs == [
        ("e-gmd-v1.0.0/drummer1/session1/1_a.midi",
         "e-gmd-v1.0.0/drummer1/session1/1_a.wav"),
        ("e-gmd-v1.0.0/drummer1/session1/2_b.midi",
         "e-gmd-v1.0.0/drummer1/session1/2_b.wav"),
    ]
    # limit bounds the count.
    assert len(h._pairs_from_index(
        "e-gmd-v1.0.0/e-gmd-v1.0.0.csv", rows, set(_NAMES), limit=1)) == 1


def test_pairs_from_stems_fallback():
    pairs = h._pairs_from_stems(_NAMES, limit=0)
    # 3_c has no audio sibling -> excluded; deterministic sorted order.
    assert pairs == [
        ("e-gmd-v1.0.0/drummer1/session1/1_a.midi",
         "e-gmd-v1.0.0/drummer1/session1/1_a.wav"),
        ("e-gmd-v1.0.0/drummer1/session1/2_b.midi",
         "e-gmd-v1.0.0/drummer1/session1/2_b.wav"),
    ]
    assert h._pairs_from_stems(_NAMES, limit=1) == pairs[:1]
