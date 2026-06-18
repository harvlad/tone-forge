"""Per-section guidance-mode classifier (chord vs riff vs lead).

Consumes per-stem ``SectionFeatures`` for one section, returns one
``GuidanceDecision`` describing what kind of practice guidance the JAM
UI should render for that section:

    "chord" → chord ribbon
    "riff"  → riff lane (tab + repeat markers)
    "lead"  → lead phrase lane (sparse landmark notes)

The chord detector is unchanged by this module. The classifier merely
decides whether to *display* chord output (always available, always
computed upstream) per section. Sections classified as riff or lead
suppress the chord ribbon for that window in favour of a more honest
guidance surface.

Threshold defaults are calibrated to the synthetic fixtures in
``tests/test_section_features.py``; see ``GuidanceThresholds`` field
docstrings for provenance.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import List, Literal, Sequence

from tone_forge.analysis.section_features import SectionFeatures


GuidanceModeStr = Literal["chord", "riff", "lead"]


@dataclass(frozen=True)
class GuidanceThresholds:
    """Tunable thresholds for the classifier.

    Frozen so a single instance can be reused across the pipeline and
    so that an accidental in-place mutation in tests can't shift
    behaviour for sibling sections. Provenance: each default below
    was set so the synthetic fixtures in ``test_section_features.py``
    land in their expected band with margin ≥ 0.10 to the nearest
    competing class.
    """

    # Aggregator floors.
    voiced_floor: float = 0.1
    """Sections with ``voiced_frame_ratio`` below this default to
    chord with confidence 0.0. Real-section silence shouldn't win
    a guidance vote."""

    tie_margin: float = 0.08
    """If the top two scores fall within this margin, the section
    falls back to chord. Defaults to a small buffer so well-defined
    riffs/leads still win, but a borderline call goes to the safer
    label."""

    # Per-stem score weights. The decision rule mixes signals
    # multiplicatively where one signal alone is insufficient; the
    # coefficient on chord_density below sets how much the chord
    # detector's view is allowed to influence a per-stem call.
    chord_density_weight: float = 0.5
    """Coefficient on chord_density inside score_chord. 0.5 means a
    chord-rate of 1.0/sec adds 0.5 to the chord score on top of the
    polyphony component."""


@dataclass(frozen=True)
class GuidanceDecision:
    """Aggregated per-section decision."""

    mode: GuidanceModeStr
    confidence: float
    reason: str
    # The stem that contributed the most weight to the aggregator vote
    # (i.e. argmax of ``voiced_frame_ratio × duration_s`` across
    # non-silent stems; ties broken by ``note_count``). Persists in the
    # bundle so the JAM UI can render the riff/lead lane from the
    # right stem's notes without re-running the hard-coded preference
    # walk (``guitar → other → piano → bass → vocals``). Empty string
    # when no stems are present or every stem is silent.
    dominant_stem: str = ""


def _score_chord(sf: SectionFeatures, t: GuidanceThresholds) -> float:
    raw = sf.polyphony_score + t.chord_density_weight * sf.chord_density_per_s
    return float(max(0.0, min(1.0, raw)))


def _score_riff(sf: SectionFeatures, _t: GuidanceThresholds) -> float:
    raw = sf.monophonic_ratio * sf.repetition_score
    return float(max(0.0, min(1.0, raw)))


def _score_lead(sf: SectionFeatures, _t: GuidanceThresholds) -> float:
    # Engine fix #8: discount the lead score by the section's
    # pitch-class diversity. A monophonic stem with a high
    # lead-activity score that nevertheless visits only a handful of
    # pitch classes (e.g. a chord-shaped bass riff like SLTS verse
    # walking F/Bb/Ab/Db) is not a *lead* — leads carry tunes that
    # span more of the chromatic alphabet. Multiplying by
    # ``pitch_class_diversity`` (Shannon entropy / log(12), in [0,1])
    # leaves real leads ~unchanged (varied melodies have diversity
    # near 1.0) while collapsing the chord-rooted-riff false-positive.
    raw = sf.monophonic_ratio * sf.lead_activity_score * sf.pitch_class_diversity
    return float(max(0.0, min(1.0, raw)))


def _classify_stem(
    sf: SectionFeatures, thresholds: GuidanceThresholds
) -> tuple[GuidanceModeStr, float]:
    """Per-stem (mode, confidence) inside one section."""
    if sf.voiced_frame_ratio < thresholds.voiced_floor:
        return "chord", 0.0
    candidates: List[tuple[GuidanceModeStr, float]] = [
        ("chord", _score_chord(sf, thresholds)),
        ("riff", _score_riff(sf, thresholds)),
        ("lead", _score_lead(sf, thresholds)),
    ]
    candidates.sort(key=lambda t: t[1], reverse=True)
    top, second = candidates[0], candidates[1]
    if top[1] - second[1] < thresholds.tie_margin:
        # Tie → prefer chord as the safe default. Confidence is
        # still the top score so the aggregator can express "I'm
        # not really sure" downstream.
        return "chord", top[1]
    return top[0], top[1]


def classify_section(
    per_stem_features: Sequence[SectionFeatures],
    thresholds: GuidanceThresholds = GuidanceThresholds(),
) -> GuidanceDecision:
    """Aggregate per-stem features into one ``GuidanceDecision``.

    Weights each stem by ``voiced_frame_ratio * duration_s`` so silent
    stems can't outvote loud ones, and within each non-silent stem the
    vote weight is further scaled by per-stem confidence — confident
    riffs beat lukewarm chord guesses.

    Empty input returns a "chord, 0.0" default so upstream callers
    that hand in an empty stems set degrade gracefully rather than
    raising.
    """
    if not per_stem_features:
        return GuidanceDecision(
            mode="chord", confidence=0.0, reason="empty", dominant_stem=""
        )

    per_stem_decisions = [_classify_stem(sf, thresholds) for sf in per_stem_features]

    # Dominant stem = argmax over voiced_frame_ratio * duration_s, ties
    # broken by note_count, then by stem_name for determinism. Computed
    # over *all* per-stem features (not just the winning-mode ones) so
    # the JAM UI gets a usable stem even when the section votes ``chord``
    # — the chord ribbon doesn't need the field, but the lead-tab lane
    # may still render when the user toggles "show tab anyway".
    dominant_sf = max(
        per_stem_features,
        key=lambda sf: (
            sf.voiced_frame_ratio * max(sf.duration_s, 0.0),
            sf.note_count,
            sf.stem_name,
        ),
    )
    dominant_stem_name = (
        dominant_sf.stem_name
        if dominant_sf.voiced_frame_ratio * max(dominant_sf.duration_s, 0.0) > 0.0
        else ""
    )

    vote: dict[GuidanceModeStr, float] = defaultdict(float)
    for sf, (mode, conf) in zip(per_stem_features, per_stem_decisions):
        weight = sf.voiced_frame_ratio * max(sf.duration_s, 0.0)
        vote[mode] += weight * conf

    total = sum(vote.values())
    if total <= 0.0:
        return GuidanceDecision(
            mode="chord",
            confidence=0.0,
            reason="all_silent: " + ", ".join(sf.stem_name for sf in per_stem_features),
            dominant_stem=dominant_stem_name,
        )

    winning_mode: GuidanceModeStr = max(vote.items(), key=lambda kv: kv[1])[0]
    winning_conf = vote[winning_mode] / total

    reason_parts = [
        f"{sf.stem_name}={mode}({conf:.2f})"
        for sf, (mode, conf) in zip(per_stem_features, per_stem_decisions)
    ]
    reason = f"{winning_mode}: " + ", ".join(reason_parts)
    return GuidanceDecision(
        mode=winning_mode,
        confidence=float(winning_conf),
        reason=reason,
        dominant_stem=dominant_stem_name,
    )


__all__ = [
    "GuidanceThresholds",
    "GuidanceDecision",
    "GuidanceModeStr",
    "classify_section",
]
