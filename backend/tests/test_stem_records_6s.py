"""htdemucs_6s stem plumbing.

6s emits `guitar` and `piano` on top of the 4-stem set. Both the role
records and the wire dict had 4-stem assumptions baked in that turned the
two extra stems into silent data loss, so they are pinned here.

These builders are pure (no torch/librosa), so this runs anywhere.
"""
from __future__ import annotations

from local_engine.analysis_worker import _build_stem_records, _build_stems_dict

SIX = {
    "drums": "/t/drums.wav",
    "bass": "/t/bass.wav",
    "vocals": "/t/vocals.wav",
    "guitar": "/t/guitar.wav",
    "piano": "/t/piano.wav",
    "other": "/t/other.wav",
}
FOUR = {k: v for k, v in SIX.items() if k not in ("guitar", "piano")}


def _roles(records):
    return {r["id"]: r["role"] for r in records}


def test_6s_guitar_and_piano_get_routable_roles():
    """Unmapped names fell through to UNKNOWN, which routes to no slot.

    guitar -> harmonic (in GUITAR_FAMILY_ROLES, so the user slot claims it);
    piano -> keys (deliberately NOT guitar-family).
    """
    roles = _roles(_build_stem_records(SIX, "guitar", {}, {}))
    assert roles["demucs.guitar"] == "harmonic"
    assert roles["demucs.piano"] == "keys"
    assert "unknown" not in roles.values(), f"unroutable stem: {roles}"


def test_four_stem_roles_are_unchanged():
    roles = _roles(_build_stem_records(FOUR, "guitar", {}, {}))
    assert roles == {
        "demucs.drums": "drums",
        "demucs.bass": "bass",
        "demucs.vocals": "vocals",
        "demucs.other": "harmonic",
    }


def test_legacy_rename_never_replaces_a_real_guitar_stem():
    """`other` must not be written into out["guitar"] under 6s.

    The legacy single-slot rename fires when detected_type == "guitar" and
    no pan-split ran. With 6s there is already a real guitar stem, so the
    rename silently swapped it for the residual bucket — and which one won
    came down to dict ordering.
    """
    out = _build_stems_dict(SIX, "guitar", {})
    assert "guitar.wav" in out["guitar"], f"guitar stem was clobbered: {out['guitar']}"
    assert "other.wav" in out["other"]
    assert "piano.wav" in out["piano"]


def test_legacy_rename_still_applies_under_the_four_stem_model():
    """The 4-stem path is untouched: `other` IS the guitar bucket there."""
    out = _build_stems_dict(FOUR, "guitar", {})
    assert "other.wav" in out["guitar"]
    assert "other" not in out
