"""Tests for the Ableton Drum Rack kit exporter."""

import gzip
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tone_forge import ableton_kit_export as ake


def _write_tone(path: Path, seconds: float = 2.0, sr: int = 44100) -> None:
    t = np.linspace(0, seconds, int(seconds * sr), endpoint=False)
    sf.write(str(path), (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr)


# ---------------------------------------------------------------------------
# Slice rendering
# ---------------------------------------------------------------------------

def test_render_slice_cuts_expected_frames(tmp_path):
    stem = tmp_path / "stem.wav"
    _write_tone(stem, seconds=2.0)
    dest = tmp_path / "out.wav"

    meta = ake._render_slice(stem, 0.5, 1.5, dest)

    assert meta is not None
    frames, sr = meta
    assert sr == 44100
    assert frames == 44100  # exactly 1 second
    data, out_sr = sf.read(str(dest))
    assert out_sr == 44100
    assert len(data) == 44100


def test_render_slice_rejects_empty_window(tmp_path):
    stem = tmp_path / "stem.wav"
    _write_tone(stem, seconds=1.0)
    assert ake._render_slice(stem, 0.9, 0.9, tmp_path / "out.wav") is None


# ---------------------------------------------------------------------------
# Drum Rack XML
# ---------------------------------------------------------------------------

def _pad(idx: int, category: str = "DRUMS") -> dict:
    return {
        "name": f"Pad {idx}",
        "category": category,
        "sample_rel_path": f"Samples/{idx:02d} Pad.wav",
        "frames": 44100,
        "sample_rate": 44100,
        "file_size": 88244,
        "loop": True,
        "midi_note": 36 + idx,
    }


def test_adg_is_gzipped_parseable_xml_with_branches():
    adg = ake.build_drum_rack_adg("My Kit", [_pad(0), _pad(1, "BASS")])

    xml = gzip.decompress(adg).decode("utf-8")
    root = ET.fromstring(xml)

    assert root.tag == "Ableton"
    branches = root.findall(".//DrumBranchPreset")
    assert len(branches) == 2
    simplers = root.findall(".//OriginalSimpler")
    assert len(simplers) == 2


def test_adg_receiving_note_is_inverted():
    adg = ake.build_drum_rack_adg("Kit", [_pad(0)])  # midi_note 36
    root = ET.fromstring(gzip.decompress(adg))
    note = root.find(".//ZoneSettings/ReceivingNote")
    assert note.get("Value") == str(128 - 36)


def test_adg_sample_refs_are_user_library_relative():
    adg = ake.build_drum_rack_adg("Kit", [_pad(3)])
    root = ET.fromstring(gzip.decompress(adg))
    part = root.find(".//MultiSamplePart")
    rel = part.find("SampleRef/FileRef/RelativePath")
    assert rel.get("Value") == "Samples/03 Pad.wav"
    # Type 6 = relative to the User Library — the only mode verified to
    # resolve in Live 12 (type 3 leaves "Media files are missing").
    rel_type = part.find("SampleRef/FileRef/RelativePathType")
    assert rel_type.get("Value") == "6"


def test_adg_chain_colors_follow_category():
    adg = ake.build_drum_rack_adg("Kit", [_pad(0, "DRUMS"), _pad(1, "BASS")])
    root = ET.fromstring(gzip.decompress(adg))
    colors = [
        b.find("DocumentColorIndex").get("Value")
        for b in root.findall(".//BranchPresets/DrumBranchPreset")
    ]
    assert colors == [
        str(ake._CATEGORY_ABLETON_COLOR["DRUMS"]),
        str(ake._CATEGORY_ABLETON_COLOR["BASS"]),
    ]


def test_adg_branch_ids_are_unique():
    adg = ake.build_drum_rack_adg("Kit", [_pad(i) for i in range(4)])
    root = ET.fromstring(gzip.decompress(adg))
    ids = []
    for b in root.findall(".//BranchPresets/DrumBranchPreset"):
        ids.extend(el.get("Id") for el in b.iter() if el.get("Id") is not None)
    assert len(ids) == len(set(ids))


def test_adg_keeps_template_root_attrs():
    adg = ake.build_drum_rack_adg("Kit", [_pad(0)])
    root = ET.fromstring(gzip.decompress(adg))
    assert root.get("Creator", "").startswith("Ableton Live 12")


def test_adg_escapes_xml_in_names():
    pad = _pad(0)
    pad["name"] = 'Riff <A> & "B"'
    adg = ake.build_drum_rack_adg("Kit", [pad])
    root = ET.fromstring(gzip.decompress(adg))  # would raise on bad escaping
    assert root.find(".//DrumBranchPreset/Name").get("Value") == 'Riff <A> & "B"'


# ---------------------------------------------------------------------------
# SFZ fallback
# ---------------------------------------------------------------------------

def test_sfz_maps_keys_and_loops():
    sfz = ake.build_sfz([_pad(0), _pad(1)])
    assert "default_path=Samples/" in sfz
    assert "key=36" in sfz and "key=37" in sfz
    assert sfz.count("<region>") == 2
    assert "loop_mode=loop_continuous" in sfz


# ---------------------------------------------------------------------------
# Full pack assembly
# ---------------------------------------------------------------------------

def _fake_kit(pads: int = 2) -> dict:
    return {
        "manifestVersion": 2,
        "packId": "auto-test-intermediate",
        "name": "Auto Kit",
        "pads": [
            {
                "padIdx": i,
                "name": f"Groove {i}",
                "category": "DRUMS" if i % 2 == 0 else "BASS",
                "stemSlice": {"stemRole": "drums", "startSec": 0.25 * i, "endSec": 0.25 * i + 1.0},
                "loopStartSec": 0.25 * i,
                "loopEndSec": 0.25 * i + 1.0,
                "loopable": True,
            }
            for i in range(pads)
        ],
    }


def test_build_zip_end_to_end(tmp_path, monkeypatch):
    stem = tmp_path / "drums.wav"
    _write_tone(stem, seconds=3.0)
    result = {"stems_local": {"drums": str(stem)}, "tempo_bpm": 97.0}

    from tone_forge.performance import serve as perf_serve
    monkeypatch.setattr(
        perf_serve, "kit_payload", lambda *a, **k: _fake_kit(2)
    )

    data, filename = ake.build_ableton_kit_zip(
        "entry123", result, song_name="Tycho — Seven", pads=2
    )

    assert filename.endswith(".zip")
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    root = filename[:-len(".zip")]
    assert f"{root}/{root}.adg" in names
    assert f"{root}/{root}.sfz" in names
    assert f"{root}/README.txt" in names
    assert f"{root}/kit.json" in names
    samples = [n for n in names if "/Samples/" in n and n.endswith(".wav")]
    assert len(samples) == 2

    # .adg inside the zip is valid gzipped XML with 2 branches
    adg_xml = gzip.decompress(z.read(f"{root}/{root}.adg"))
    xml_root = ET.fromstring(adg_xml)
    assert len(xml_root.findall(".//DrumBranchPreset")) == 2

    # kit manifest is embedded for provenance
    kit = json.loads(z.read(f"{root}/kit.json"))
    assert kit["entryId"] == "entry123"
    assert len(kit["kit"]["pads"]) == 2

    # README carries the pad map + tempo
    readme = z.read(f"{root}/README.txt").decode()
    assert "97 BPM" in readme
    assert "Groove 0" in readme


def test_build_zip_raises_without_reachable_stems(tmp_path, monkeypatch):
    from tone_forge.performance import serve as perf_serve
    monkeypatch.setattr(perf_serve, "kit_payload", lambda *a, **k: _fake_kit(2))

    with pytest.raises(ValueError):
        ake.build_ableton_kit_zip(
            "entry123", {"stems_local": {}}, song_name="X", pads=2
        )


def test_build_zip_raises_on_empty_kit(monkeypatch):
    from tone_forge.performance import serve as perf_serve
    monkeypatch.setattr(perf_serve, "kit_payload", lambda *a, **k: {"pads": []})

    with pytest.raises(ValueError):
        ake.build_ableton_kit_zip("entry123", {}, song_name="X")
