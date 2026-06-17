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

    # Stage 1.4 (Power-chord third-absence prior) — re-enabled with
    # persistence gating + key-conditioning to address the
    # demolition_warning false-positive regression that caused the
    # original Phase 3 attempt to be disabled:
    #
    #   * power_chord_third_ratio > 0 AND
    #   * power_chord_penalty > 0 AND
    #   * power_chord_third_min_streak >= 1
    #
    # together enable the prior. The streak gate requires the third
    # of a candidate root to be absent for N consecutive windows
    # before the maj/min emission is demoted, ruling out the
    # transient-attack false positives that demoted real triads in
    # the original failed attempt.
    #
    # power_chord_minor_key_only=True additionally restricts the
    # prior to songs whose detected key is minor with strength
    # >= 0.7 — rock idiom is minor-key power chords, and gating on
    # the post-tie-break key avoids firing the prior on songs
    # outside the idiom.
    #
    # Production callsites (``bench.benchmark``, ``bench.corpus``,
    # the existing chord-detector regression tests) pass a default
    # config → all four fields are zero/False → emission behaviour
    # is bit-exact identical to pre-S1.4. Only the chord-lane stage
    # (``analysis.chords.detect_chords_with_key``) opts in.
    power_chord_third_ratio: float = 0.0
    power_chord_penalty: float = 0.0
    power_chord_third_min_streak: int = 0
    power_chord_minor_key_only: bool = False
