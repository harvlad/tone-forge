"""Tests for the BTC adapter's pure parts (no model load, no audio).

Covers the label-mapping layer that turns BTC mir_eval-style class
labels into tone-forge chord symbols, and the class-index tables that
mirror the upstream vocabulary. Model inference itself is exercised
by ``scripts.analysis_eval --chords btc``.
"""
import pytest

from tone_forge.analysis.btc_chords import (
    _idx_to_label,
    btc_label_to_symbol,
)
from tone_forge.analysis.chord_eval import normalise_symbol


class TestLabelToSymbol:
    @pytest.mark.parametrize("label,expected", [
        ("C", "C"),
        ("A:min", "Am"),
        ("F#:min7", "F#m7"),
        ("B:maj7", "Bmaj7"),
        ("D:7", "D7"),
        ("G:dim", "Gdim"),
        ("G:dim7", "Gdim7"),
        ("E:aug", "Eaug"),
        ("A:sus2", "Asus2"),
        ("A:sus4", "Asus4"),
        # Collapsed qualities (outside the tone-forge vocab)
        ("C:min6", "Cm"),
        ("C:maj6", "C"),
        ("C:minmaj7", "Cm"),
        ("F#:hdim7", "F#dim"),
    ])
    def test_mapping(self, label, expected):
        assert btc_label_to_symbol(label) == expected

    @pytest.mark.parametrize("label", ["N", "X", ""])
    def test_no_chord_labels_yield_none(self, label):
        assert btc_label_to_symbol(label) is None

    def test_every_mapped_symbol_is_parsable(self):
        """Every non-N/X class in both vocabs must survive
        chord_eval.normalise_symbol — otherwise WCSR scoring would
        raise on real predictions."""
        for vocab in ("majmin", "large_voca"):
            for label in _idx_to_label(vocab):
                symbol = btc_label_to_symbol(label)
                if symbol is not None:
                    normalise_symbol(symbol)  # raises on unparsable


class TestIdxTables:
    def test_majmin_table(self):
        labels = _idx_to_label("majmin")
        assert len(labels) == 25
        assert labels[0] == "C"
        assert labels[1] == "C:min"
        assert labels[24] == "N"

    def test_large_voca_table(self):
        labels = _idx_to_label("large_voca")
        assert len(labels) == 170
        assert labels[169] == "N"
        assert labels[168] == "X"
        # Upstream convention: quality index 1 (maj) renders bare root.
        assert labels[1] == "C"
        assert labels[0] == "C:min"
        assert labels[14] == "C#:min"
