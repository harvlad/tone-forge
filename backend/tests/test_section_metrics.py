"""Tests for bench.section_metrics."""
import pytest

from bench.section_metrics import (
    boundary_f_measure,
    canonical_section_label,
    key_score,
    parse_key,
    section_label_accuracy,
    to_section_regions,
)


class TestCanonicalLabel:
    @pytest.mark.parametrize("raw,canon", [
        ("Chorus 2", "chorus"),
        ("Post-Chorus", "chorus"),
        ("Refrain", "chorus"),
        ("Pre-Chorus", "prechorus"),
        ("Guitar Solo", "instrumental"),
        ("Solo", "instrumental"),
        ("Verse 1", "verse"),
        ("Middle 8", "bridge"),
        ("SectionType.VERSE", "verse"),
        ("outro", "outro"),
        ("Coda", "outro"),
        ("weirdname", "weirdname"),
    ])
    def test_mapping(self, raw, canon):
        assert canonical_section_label(raw) == canon


class TestBoundaryF:
    REF = [(0, 10, "Intro"), (10, 30, "Verse"), (30, 50, "Chorus")]

    def test_perfect(self):
        p, r, f = boundary_f_measure(self.REF, self.REF, window_s=0.5)
        assert (p, r, f) == (1.0, 1.0, 1.0)

    def test_within_window(self):
        pred = [(0, 10.4, "a"), (10.4, 29.8, "b"), (29.8, 50, "c")]
        _, _, f = boundary_f_measure(pred, self.REF, window_s=0.5)
        assert f == 1.0
        _, _, f_strict = boundary_f_measure(pred, self.REF, window_s=0.1)
        assert f_strict == 0.0

    def test_over_segmentation_hits_precision(self):
        pred = [(0, 5, "x"), (5, 10, "x"), (10, 20, "x"),
                (20, 30, "x"), (30, 50, "x")]  # 4 boundaries, 2 correct
        p, r, f = boundary_f_measure(pred, self.REF, window_s=0.5)
        assert p == 0.5
        assert r == 1.0
        assert f == pytest.approx(2 / 3)

    def test_both_empty_is_perfect(self):
        assert boundary_f_measure([(0, 50, "a")], [(0, 50, "b")]) == (
            1.0, 1.0, 1.0)

    def test_one_to_one_matching(self):
        # Two predicted boundaries near one reference boundary: only
        # one may claim it.
        pred = [(0, 9.8, "a"), (9.8, 10.2, "b"), (10.2, 50, "c")]
        ref = [(0, 10, "x"), (10, 50, "y")]
        p, r, _ = boundary_f_measure(pred, ref, window_s=0.5)
        assert r == 1.0
        assert p == 0.5


class TestLabelAccuracy:
    def test_exact(self):
        ref = [(0, 10, "Verse 1"), (10, 20, "Chorus")]
        pred = [(0, 10, "verse"), (10, 20, "chorus")]
        assert section_label_accuracy(pred, ref, 20.0) == 1.0

    def test_shifted_partial(self):
        ref = [(0, 10, "Verse"), (10, 20, "Chorus")]
        pred = [(0, 12, "verse"), (12, 20, "chorus")]
        # verse matches 0-10 (10s) + chorus matches 12-20 (8s) = 18/20
        assert section_label_accuracy(pred, ref, 20.0) == pytest.approx(0.9)

    def test_adapts_arrangement_section_like(self):
        class FakeType:
            value = "chorus"

        class FakeSection:
            start_time = 0.0
            end_time = 10.0
            type = FakeType()

        assert to_section_regions([FakeSection()]) == [(0.0, 10.0, "chorus")]


class TestKeyScore:
    def test_parse(self):
        assert parse_key("A major") == (9, "major")
        assert parse_key("F# minor") == (6, "minor")
        assert parse_key("Bbm") == (10, "minor")
        assert parse_key("Db") == (1, "major")
        assert parse_key("H major") is None

    @pytest.mark.parametrize("pred,ref,score", [
        ("A major", "A major", 1.0),
        ("E major", "A major", 0.5),   # perfect fifth up
        ("D major", "A major", 0.5),   # fifth down
        ("F# minor", "A major", 0.3),  # relative minor
        ("C major", "A minor", 0.3),   # relative major
        ("A minor", "A major", 0.2),   # parallel
        ("B major", "A major", 0.0),
        ("", "A major", 0.0),
    ])
    def test_weights(self, pred, ref, score):
        assert key_score(pred, ref) == score
