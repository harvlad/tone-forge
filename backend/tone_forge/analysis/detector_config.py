"""DetectorConfig dataclass for parameter sweeps and benchmark runs.

Centralises every numeric lever the chord detector exposes. The
default instance reproduces the hardcoded constants currently
inline in ``chord_detector.py`` bit-for-bit: passing
``DetectorConfig()`` (or omitting the argument entirely) to
``detect_chords_from_audio`` is by construction equivalent to the
pre-M1 call signature.

Two opt-in fields default to no-op values:

* ``quality_switch_penalty`` (formerly REVERTED Stage 2.1)
* ``hcdf_snap_radius_frames`` (formerly REVERTED Stage 3.1)

Both were removed from production after corpus sweeps regressed
every fixture. They are re-exposed here as opt-in levers so the
sweep harness (``bench.sweep``) can re-evaluate them against a
larger corpus without further edits to the detector source.

See ``backend/bench/README.md`` for the sweep config schema that
populates these fields from YAML.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectorConfig:
    """Numeric levers consumed by ``detect_chords_from_audio``.

    Default values reproduce the hardcoded constants currently
    inline in ``chord_detector.py``. Passing ``DetectorConfig()``
    (or no config at all) keeps detector output bit-exact identical
    to pre-M1 behaviour.
    """

    # Emission-side
    cos_cutoff: float = 0.70                 # COS_CUTOFF (no-chord floor)
    diatonic_bias: float = 0.10              # DIATONIC_BIAS (emission multiplier)
    bass_root_bias: float = 0.05             # BASS_ROOT_BIAS (Phase 5 bass-routing)

    # Transition-side
    self_loop_bonus: float = 0.01            # SELF_LOOP_BONUS
    same_root_quality_bonus: float = 0.01    # SAME_ROOT_QUALITY_BONUS
    no_chord_penalty: float = -0.10          # NO_CHORD_PENALTY

    # Opt-in levers (default = no-op, matching production).
    # Re-enable previously REVERTED stages for sweep exploration.
    quality_switch_penalty: float = 0.0      # S2.1 reverted
    hcdf_snap_radius_frames: int = 0         # S3.1 reverted
