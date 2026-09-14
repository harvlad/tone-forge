"""Auto-kit pad categorization for the htdemucs_6s synth residual.

Under 6s the residual `other` stem is a synth/strings/pad proxy (guitar +
piano already separated), so its pads must read "Synth" (SYNTH category),
not "Guitar lead". Under the 4-stem model `other` IS the guitar bucket and
must stay unchanged. The `residual_is_synth` flag (set on the graph from the
stem set) is what distinguishes them.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.performance.graph import ContentType, GridPos
from tone_forge.performance.kit_builder import (
    _CATEGORY_HEX,
    _category_for,
    _descriptive_label,
)


class _Asset:
    def __init__(self, stem, content_type):
        self.stem = stem
        self.content_type = content_type
        self.pos = GridPos(start_s=0.0, end_s=1.0, start_beat=0,
                           length_beats=4.0, start_bar=0, length_bars=1.0,
                           is_bar_aligned=True)


def test_6s_other_pads_are_synth():
    a = _Asset("other", ContentType.LEAD_LOOP)
    assert _category_for(a, residual_is_synth=True) == "SYNTH"
    assert _descriptive_label(a, [], residual_is_synth=True).startswith("Synth")
    # SYNTH has its own accent color, distinct from LEAD (guitar orange).
    assert _CATEGORY_HEX["SYNTH"] != _CATEGORY_HEX["LEAD"]


def test_4stem_other_stays_guitar():
    """No 6s residual → `other` is the guitar bucket; behaviour unchanged."""
    a = _Asset("other", ContentType.LEAD_LOOP)
    assert _category_for(a, residual_is_synth=False) == "LEAD"
    assert _descriptive_label(a, [], residual_is_synth=False).startswith("Guitar")


def test_synth_flag_never_touches_other_stems():
    """A real guitar/bass/drums stem keeps its category even under 6s."""
    assert _category_for(_Asset("guitar", ContentType.LEAD_LOOP), True) == "LEAD"
    assert _category_for(_Asset("bass", ContentType.BASS_GROOVE), True) == "BASS"
    assert _category_for(_Asset("drums", ContentType.RHYTHM_LOOP), True) == "DRUMS"


def test_synth_role_word_reads_naturally():
    """Pad/lead/chord content types compose sensible 'Synth <role>' labels."""
    assert _descriptive_label(_Asset("other", ContentType.DRONE), [], True) \
        .startswith("Synth pad")
    assert _descriptive_label(_Asset("other", ContentType.LEAD_LOOP), [], True) \
        .startswith("Synth lead")
