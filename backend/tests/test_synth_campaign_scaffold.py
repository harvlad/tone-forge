"""Riley synth-campaign scaffolding.

Foundation for training a synth separator: no separator in the repo emits a
synth stem (not demucs 4 or 6, not AudioShake, not Music.ai), so a synth
target has to be manufactured. These are the pieces that made that
impossible to even express, and they run without an audio env.
"""
from __future__ import annotations

from pathlib import Path

from lab.factory.asset import Asset, Kind, Role
from lab.factory.coverage import DIMENSIONS, coverage_report
from lab.factory.sources import SlakhSource


def _slakh_tree(root: Path, tracks: int = 3) -> Path:
    """Minimal Slakh-shaped tree. iter_assets only resolves paths, so the
    files need to exist but not decode."""
    for i in range(tracks):
        d = root / f"Track{i:05d}"
        d.mkdir(parents=True)
        for name in ("guitar", "synth", "mixture"):
            (d / f"{name}.wav").write_bytes(b"")
    return root


# --------------------------------------------------------------------------
# Role.SYNTH
# --------------------------------------------------------------------------

def test_synth_is_its_own_role_not_folded_into_keys():
    """A saw pad and a Rhodes are not the same target.

    Without a distinct role the only home for synth was KEYS (wrong — that
    means a played keyboard) or OTHER (the bucket whose blending is the
    entire problem).
    """
    assert Role.SYNTH == "synth"
    assert Role.SYNTH != Role.KEYS
    assert Role.SYNTH != Role.OTHER


# --------------------------------------------------------------------------
# SlakhSource: role-parameterised
# --------------------------------------------------------------------------

def test_slakh_can_emit_synth_targets(tmp_path):
    """Slakh is MIDI-rendered against General MIDI classes, so its synth
    stems carry exact ground truth — the one registered dataset that can
    supply abundant synth targets. The stem name was hardcoded to guitar."""
    root = _slakh_tree(tmp_path / "slakh")
    src = SlakhSource(root, stem_role=Role.SYNTH)

    stems = [a for a in src.iter_assets() if a.kind == Kind.STEM]
    assert len(stems) == 3
    assert {a.role for a in stems} == {Role.SYNTH}
    assert all(Path(a.audio_path).name == "synth.wav" for a in stems)
    assert Role.SYNTH in src.capabilities().roles


def test_slakh_still_defaults_to_guitar(tmp_path):
    """Riley's original target. Existing callers pass no stem_role."""
    root = _slakh_tree(tmp_path / "slakh")
    stems = [a for a in SlakhSource(root).iter_assets() if a.kind == Kind.STEM]
    assert {a.role for a in stems} == {Role.GUITAR}
    assert all(Path(a.audio_path).name == "guitar.wav" for a in stems)


def test_slakh_stem_filename_is_overridable(tmp_path):
    """On-disk names depend on how a Slakh checkout was flattened, which
    this class had no way to express."""
    root = tmp_path / "slakh"
    d = root / "Track00000"
    d.mkdir(parents=True)
    (d / "S03.wav").write_bytes(b"")
    src = SlakhSource(root, stem_role=Role.SYNTH, stem_filename="S03")
    stems = [a for a in src.iter_assets() if a.kind == Kind.STEM]
    assert len(stems) == 1 and stems[0].role == Role.SYNTH


def test_mixture_emission_is_unaffected_by_the_stem_role(tmp_path):
    root = _slakh_tree(tmp_path / "slakh")
    mixes = [a for a in SlakhSource(root, stem_role=Role.SYNTH).iter_assets()
             if a.kind == Kind.MIXTURE]
    assert len(mixes) == 3 and {a.role for a in mixes} == {Role.MIX}


# --------------------------------------------------------------------------
# Coverage dimensions
# --------------------------------------------------------------------------

def _asset(**md) -> Asset:
    return Asset(
        asset_id="a1", content_hash="h1", path="/t/s.wav",
        kind=Kind.STEM, role=Role.SYNTH, source_id="slakh2100",
        dataset_key="slakh2100", provenance={}, lineage=(), metadata=md,
    )


def test_synth_dimensions_are_reported():
    """Coverage drove the guitar campaign; synth needs its own axes or a
    'balanced' synth corpus could be 500 static saw pads."""
    for dim in ("synth_role", "oscillator", "brightness", "movement",
                "stereo_width"):
        assert dim in DIMENSIONS, f"missing synth coverage dimension: {dim}"


def test_synth_dimensions_bucket_from_descriptor_vocabulary():
    """Keys mirror SynthDescriptor, so a manufactured asset can be stamped
    from a real analysis instead of hand-typed tags."""
    a = _asset(synth_role="pad", oscillator="saw", brightness=0.8,
               movement=0.1, stereo_width=0.9)
    assert DIMENSIONS["synth_role"](a) == "pad"
    assert DIMENSIONS["oscillator"](a) == "saw"
    assert DIMENSIONS["brightness"](a) == "high"
    assert DIMENSIONS["movement"](a) == "low"
    # width buckets read as width, not as low/med/high
    assert DIMENSIONS["stereo_width"](a) == "wide"


def test_missing_synth_metadata_is_unknown_never_a_crash():
    """Guitar assets carry none of these keys and must still report."""
    a = _asset()
    for dim in ("synth_role", "oscillator", "brightness", "movement",
                "stereo_width"):
        assert DIMENSIONS[dim](a) == "unknown"


class _Catalog:
    def __init__(self, assets):
        self._a = assets

    def all(self):
        return self._a


def test_coverage_report_flags_a_one_sided_synth_pool():
    """The gap-finding that drives commissioning has to work on synth too:
    six saw pads and one square lead should flag the lead as sparse."""
    assets = [_asset(synth_role="pad", oscillator="saw") for _ in range(6)]
    assets.append(_asset(synth_role="lead", oscillator="square"))
    report = coverage_report(_Catalog(assets), sparse_threshold=3)

    assert report["dimensions"]["oscillator"] == {"saw": 6, "square": 1}
    sparse = {(s["dimension"], s["value"]) for s in report["sparse"]}
    assert ("synth_role", "lead") in sparse
    assert ("oscillator", "square") in sparse
    assert ("oscillator", "saw") not in sparse


# --------------------------------------------------------------------------
# VirtualStudio target role
# --------------------------------------------------------------------------

def _studio(tmp_path, **kw):
    """Real constructor; catalog/runner are only stored, never touched here."""
    from lab.factory.studio import VirtualStudio
    return VirtualStudio(catalog=None, runner=None, out_dir=tmp_path, **kw)


def test_studio_target_role_defaults_to_guitar(tmp_path):
    """The guitar path must not move.

    Corpora are content-addressed on their assets, so a changed target role
    (and the emitted filename derived from it) would silently invalidate
    every frozen corpus_hash — riley_corpus_v1.0, the promoted default,
    included. The default is the compatibility contract.
    """
    assert _studio(tmp_path).target_role == Role.GUITAR == "guitar"


def test_studio_accepts_a_synth_target(tmp_path):
    """A (mixture, synth_target) pair is now expressible at all. The mixing
    maths is untouched — a linear per-stem sum, so ground truth stays exact
    by construction whatever the target instrument is."""
    assert _studio(tmp_path, target_role=Role.SYNTH).target_role == "synth"


def test_studio_emitted_names_track_the_role(tmp_path):
    """Filename and provenance label both derive from the role, and both
    still read exactly as before for guitar."""
    assert f"{_studio(tmp_path).target_role}_target.wav" == "guitar_target.wav"
    assert f"{_studio(tmp_path).target_role}_in_mix" == "guitar_in_mix"
    synth = _studio(tmp_path, target_role=Role.SYNTH)
    assert f"{synth.target_role}_target.wav" == "synth_target.wav"
