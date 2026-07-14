"""Tests for scripts.import_groundtruth (notation -> fixture JSON)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.import_groundtruth import (
    ParsedSong,
    build_fixture,
    merge_adjacent,
    parse_chart,
    parse_lab,
)
from bench.schema import validate_fixture_json


CHART = """\
song: Test Song
artist: Tester
bpm: 120
time: 4/4
offset: 1.0
key: A major

[Intro]
A5*2

[Chorus] x2
B5 A5

[Bridge] bpm=60
D5
"""


class TestChartParser:
    def test_bar_grid_timing(self):
        parsed = parse_chart(CHART)
        # 120 BPM 4/4 -> bar = 2.0s; offset 1.0
        # Intro: A5 bars 0-1 -> 1.0..5.0
        # Chorus x2: B5 A5 B5 A5 -> 5..7, 7..9, 9..11, 11..13
        # Bridge at 60 BPM -> bar = 4.0s -> 13..17
        chords = parsed.chords
        assert chords[0] == (1.0, 5.0, "A5")
        assert chords[1] == (5.0, 7.0, "B5")
        assert chords[2] == (7.0, 9.0, "A5")
        assert chords[3] == (9.0, 11.0, "B5")
        assert chords[4] == (11.0, 13.0, "A5")
        assert chords[5] == (13.0, 17.0, "D5")
        assert parsed.duration_s == pytest.approx(17.0)

    def test_sections_cover_grid(self):
        parsed = parse_chart(CHART)
        assert parsed.sections == [
            (1.0, 5.0, "Intro"),
            (5.0, 9.0, "Chorus"),
            (9.0, 13.0, "Chorus"),
            (13.0, 17.0, "Bridge"),
        ]

    def test_headers(self):
        parsed = parse_chart(CHART)
        assert parsed.song == "Test Song"
        assert parsed.tempo_bpm == 120.0
        assert parsed.key == "A major"
        assert parsed.offset_s == 1.0

    def test_dot_repeats_previous(self):
        parsed = parse_chart("bpm: 120\n[V]\nA5 . B5\n")
        assert [c[2] for c in parsed.chords] == ["A5", "A5", "B5"]

    def test_nc_excluded_from_fixture_regions(self):
        parsed = parse_chart("bpm: 120\n[V]\nA5 N.C. B5\n")
        data = build_fixture(parsed, split="train", curated_by=None)
        labels = [r["label"] for r in data["regions"]]
        assert labels == ["A5", "B5"]
        # Gap where N.C. was: B5 starts 2s after A5 ends.
        assert data["regions"][1]["start"] - data["regions"][0]["end"] == (
            pytest.approx(2.0)
        )

    def test_duration_clips_grid(self):
        parsed = parse_chart("bpm: 120\nduration: 3.0\n[V]\nA5*4\n")
        assert parsed.duration_s == 3.0
        assert parsed.chords == [(0.0, 3.0, "A5")]

    def test_comment_and_barline_ignored(self):
        parsed = parse_chart(
            "bpm: 120\n[V]\n| A5 | B5 |  # riff\n")
        assert [c[2] for c in parsed.chords] == ["A5", "B5"]

    def test_errors_are_line_numbered(self):
        with pytest.raises(ValueError, match="line 3"):
            parse_chart("bpm: 120\n[V]\nXyz9\n")
        with pytest.raises(ValueError, match="bpm"):
            parse_chart("[V]\nA5\n")
        with pytest.raises(ValueError, match="no chord tokens"):
            parse_chart("bpm: 120\n[V]\n")
        with pytest.raises(ValueError, match="no previous chord"):
            parse_chart("bpm: 120\n[V]\n. A5\n")

    def test_six_eight_bar_length(self):
        # 6/8 at 120 quarter-BPM -> 3 quarters/bar -> 1.5s bars.
        parsed = parse_chart("bpm: 120\ntime: 6/8\n[V]\nA5 B5\n")
        assert parsed.chords[0] == (0.0, 1.5, "A5")
        assert parsed.chords[1] == (1.5, 3.0, "B5")


class TestLabParser:
    def test_basic(self):
        regions = parse_lab("0.0 1.5 A\n1.5 3.0 F#m\n# comment\n\n")
        assert regions == [(0.0, 1.5, "A"), (1.5, 3.0, "F#m")]

    def test_rejects_bad_lines(self):
        with pytest.raises(ValueError, match="line 1"):
            parse_lab("0.0 A\n")
        with pytest.raises(ValueError, match="end <= start"):
            parse_lab("2.0 1.0 A\n")


class TestFixtureAssembly:
    def test_merge_adjacent(self):
        merged = merge_adjacent(
            [(0.0, 1.0, "A5"), (1.0, 2.0, "A5"), (2.0, 3.0, "B5")])
        assert merged == [(0.0, 2.0, "A5"), (2.0, 3.0, "B5")]

    def test_fixture_passes_schema(self):
        parsed = parse_chart(CHART)
        data = build_fixture(parsed, split="train", curated_by="matt")
        assert validate_fixture_json(data) == []
        assert data["split"] == "train"
        assert data["key"] == "A major"
        assert data["sections"][0]["label"] == "Intro"
        # Chorus block repeats merge into one region per chord pair,
        # not one giant region (B5/A5 alternate).
        assert len(data["regions"]) == 6

    def test_cli_roundtrip(self, tmp_path):
        chart = tmp_path / "song.chart"
        chart.write_text(CHART, encoding="utf-8")
        out = tmp_path / "song.json"
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.import_groundtruth",
             "--out", str(out), "--split", "train", "chart", str(chart)],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert proc.returncode == 0, proc.stderr
        data = json.loads(out.read_text(encoding="utf-8"))
        assert validate_fixture_json(data) == []
        assert data["song"] == "Test Song"
        assert len(data["sections"]) == 4
