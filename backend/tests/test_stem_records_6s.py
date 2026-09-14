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


# --- Synth stem (6s `other` residual) ----------------------------------------
#
# Under 6s the `other` stem is the synth/strings/pad residual (guitar + piano
# already pulled out) and ear-checks well as a synth proxy. It is surfaced as
# its own `synth` role instead of being dropped or mislabelled "Guitar". A
# mid/side split of it was tried and rejected (it only mono-collapses centred
# synth, degrading quality without separating anything).

GUITAR_SPLIT = {"guitar_center": "/t/g_c.wav", "guitar_sides": "/t/g_s.wav"}


def test_6s_other_residual_is_the_synth_role():
    """The 6s `other` residual surfaces as role=synth, not harmonic/Guitar."""
    recs = _build_stem_records(SIX, "unknown", {}, {})
    roles = _roles(recs)
    assert roles["demucs.other"] == "synth"
    # Display name reads "Synth", and guitar/piano keep their own roles.
    other = next(r for r in recs if r["id"] == "demucs.other")
    assert other["display_name"] == "Synth"
    assert roles["demucs.guitar"] == "harmonic"
    assert roles["demucs.piano"] == "keys"


def test_four_stem_other_stays_harmonic():
    """4-stem `other` IS the guitar bucket -- it must NOT become synth."""
    roles = _roles(_build_stem_records(FOUR, "guitar", {}, {}))
    assert roles["demucs.other"] == "harmonic"


def test_guitar_split_and_synth_coexist_under_6s():
    """Guitar's pan-split parts route to demucs.other.* (guitar family); the
    residual `other` is a separate synth stem. Neither the old guitar-doubling
    nor the dropped synth survives.
    """
    roles = _roles(_build_stem_records(SIX, "unknown", GUITAR_SPLIT, {}))
    assert roles["demucs.other.center"] == "harmonic"   # guitar part
    assert roles["demucs.other.sides"] == "harmonic"    # guitar part
    assert roles["demucs.other"] == "synth"             # residual, distinct id
    assert "demucs.guitar" not in roles                 # base guitar replaced


def test_synth_residual_stays_in_wire_dict_under_6s():
    """The name-keyed legacy dict keeps `other` (its role is carried by the
    records); guitar parts replace the guitar slot, not `other`."""
    out = _build_stems_dict(SIX, "unknown", GUITAR_SPLIT)
    assert "g_c.wav" in out["guitar_center"]
    assert "other.wav" in out["other"]   # residual survives
    assert "guitar" not in out           # guitar replaced by its split
