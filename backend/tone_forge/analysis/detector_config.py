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

    # Stage 1.4.2 — post-Viterbi power-chord substitution. Stage 1.4.1
    # demotes maj/min cells during emission scoring but the dyad/triad
    # mass asymmetry means the triad templates still usually win.
    # This complementary pass re-scores each emitted region against
    # the region-averaged chroma and substitutes the region's quality
    # to '5' when (a) the region's third bin is weak (third_ratio <
    # post_viterbi_third_ratio) AND (b) the power-5 template's raw
    # cosine is within ``post_viterbi_margin`` of the winning maj/min
    # cosine. The same key gate as Stage 1.4.1 applies: only fires
    # when ``power_chord_minor_key_only=True`` and the detected key
    # is minor with strength >= 0.7.
    #
    # Defaults to no-op (both = 0.0), so bench corpus stays bit-exact
    # and only chord-lane stage opts in.
    power_chord_post_viterbi_third_ratio: float = 0.0
    power_chord_post_viterbi_margin: float = 0.0

    # Round-2 Fix 1 — Spectral-shape ratio gate for post-Viterbi
    # power-chord substitution.
    #
    # The raw third-bin ratio (``power_chord_post_viterbi_third_ratio``)
    # is a magnitude test on a single chroma bin: on distorted guitar,
    # harmonic-distortion intermodulation products push energy into
    # the third bin so that real power chords straddle the 0.4 gate.
    # The shape ratio is a geometric property of the (root, 3rd, 5th,
    # 7th) chroma vector that is invariant under overtone inflation
    # because both numerator and denominator inflate together under
    # a diatonic tone stack:
    #
    #   harmonic_mass = root_bin + fifth_bin
    #   melodic_mass  = third_bin + seventh_bin
    #   shape_ratio   = harmonic_mass / (melodic_mass + eps)
    #
    # Triads (major, minor, 7) with all four bins carrying mass score
    # 1.0–1.5. Power chords (root+5th dominant; 3rd/7th only from
    # intermodulation residue) score >= 2.0. When
    # ``power_chord_shape_ratio_min > 0``, ``_substitute_power_chords_on_dyads``
    # gates substitution on ``shape_ratio >= power_chord_shape_ratio_min``.
    #
    # Defaults to 0.0 → gate disabled → bench corpus stays bit-exact.
    # Only chord-lane (``analysis.chords.detect_chords_with_key``) opts
    # in (with 2.0 typical). The chord-lane config also flips
    # ``power_chord_post_viterbi_third_ratio`` to 0.0 to remove the
    # old magnitude gate entirely; the shape-ratio and raw-cosine-margin
    # criteria carry the substitution decision alone.
    power_chord_shape_ratio_min: float = 0.0
