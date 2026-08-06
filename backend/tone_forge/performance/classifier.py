"""Content-type classification + playable ranking.

Turn a (stem, phrase/loop, pattern) into a PerformanceAsset with:
  * a ContentType so a user instantly knows how to use the pad,
  * a performance_score (0..1) so the Launchpad shows the best material first,
  * a difficulty (0..1).

Pure logic over already-computed features (onset density, energy, loop
confidence, recurrence, length) — no audio, unit-testable.
"""
from __future__ import annotations

from typing import Optional

from .graph import ContentType, Loop, LoopQuality, Pattern, PerformanceAsset, Phrase

# Launchpad-ish palette by content type (color_hint the app already understands).
_COLOR = {
    ContentType.RHYTHM_LOOP: "#ff6b3d",
    ContentType.LEAD_LOOP: "#ffd23d",
    ContentType.CHORD_LOOP: "#8b6dff",
    ContentType.BASS_GROOVE: "#3d9bff",
    ContentType.TEXTURE: "#3dd6c4",
    ContentType.DRONE: "#5a5a7a",
    ContentType.IMPACT: "#ff3d6b",
    ContentType.TRANSITION: "#b03dff",
    ContentType.ONE_SHOT: "#ffffff",
    ContentType.PICKUP: "#9dff3d",
    ContentType.ENDING: "#8a8a8a",
    ContentType.AMBIENT: "#3d6dff",
    ContentType.UNKNOWN: "#666666",
}


def classify(
    stem: str,
    phrase: Phrase,
    loop: Optional[Loop] = None,
    pattern: Optional[Pattern] = None,
) -> ContentType:
    q = loop.quality if loop else LoopQuality()
    onset = phrase.onset_density
    length_bars = phrase.pos.length_bars
    loopable = q.confidence >= 0.55

    if stem == "drums":
        if length_bars >= 1 and loopable:
            return ContentType.RHYTHM_LOOP
        return ContentType.ONE_SHOT if length_bars < 1 else ContentType.RHYTHM_LOOP
    if stem == "bass":
        return ContentType.BASS_GROOVE if loopable else ContentType.ONE_SHOT
    # pitched melodic/harmonic stems (other/guitar/vocals/piano)
    if not loopable and length_bars < 1:
        return ContentType.ONE_SHOT
    if onset < 0.4:  # sparse/sustained
        if phrase.energy < 0.02:
            return ContentType.AMBIENT
        return ContentType.DRONE if length_bars >= 2 else ContentType.TEXTURE
    if onset >= 1.5:  # busy → lead line
        return ContentType.LEAD_LOOP
    # moderate activity, held chords → chord loop
    return ContentType.CHORD_LOOP


def difficulty(stem: str, phrase: Phrase) -> float:
    """0 easy .. 1 hard — activity + register drive it (for adaptive skill)."""
    d = min(1.0, 0.25 + 0.35 * phrase.onset_density)
    if stem in ("other", "guitar", "guitar_left", "guitar_right"):
        d += 0.1  # lead/guitar harder
    if phrase.pos.length_bars >= 8:
        d += 0.1
    return float(min(1.0, d))


def performance_score(
    phrase: Phrase,
    loop: Optional[Loop],
    pattern: Optional[Pattern],
    content_type: ContentType,
) -> float:
    """Playable ranking: loop quality + musical usefulness + repetition +
    clarity/energy. Best material floats to the top of the Launchpad."""
    q = loop.quality if loop else LoopQuality()
    loop_q = q.confidence
    # repetition: repeated material is usually the best pad content
    rep = 0.0
    if pattern:
        rep = min(1.0, (pattern.recurrence_count - 1) / 6.0)
    # clarity/energy: audible but not clipping
    energy = phrase.energy
    clarity = 1.0 - abs(min(energy, 0.3) - 0.15) / 0.15  # sweet spot ~0.15
    clarity = max(0.0, clarity)
    # usefulness by type (loops > one-shots for "perform the song")
    useful = {
        ContentType.RHYTHM_LOOP: 1.0, ContentType.CHORD_LOOP: 0.95,
        ContentType.BASS_GROOVE: 0.9, ContentType.LEAD_LOOP: 0.9,
        ContentType.TEXTURE: 0.6, ContentType.DRONE: 0.55,
        ContentType.AMBIENT: 0.5, ContentType.ONE_SHOT: 0.5,
        ContentType.IMPACT: 0.6, ContentType.TRANSITION: 0.55,
        ContentType.PICKUP: 0.5, ContentType.ENDING: 0.45,
        ContentType.UNKNOWN: 0.3,
    }.get(content_type, 0.4)

    score = 0.4 * loop_q + 0.25 * rep + 0.2 * useful + 0.15 * clarity
    return float(max(0.0, min(1.0, score)))


def build_asset(
    stem: str,
    phrase: Phrase,
    loop: Optional[Loop] = None,
    pattern: Optional[Pattern] = None,
) -> PerformanceAsset:
    ct = classify(stem, phrase, loop, pattern)
    q = loop.quality if loop else LoopQuality()
    return PerformanceAsset(
        source_id=(loop.id if loop else phrase.id),
        stem=stem,
        pos=phrase.pos,
        content_type=ct,
        performance_score=performance_score(phrase, loop, pattern, ct),
        difficulty=difficulty(stem, phrase),
        loopable=q.confidence >= 0.55,
        loop_confidence=q.confidence,
        color_hint=_COLOR.get(ct),
        label=f"{stem} {ct.value}",
        pattern_id=(pattern.id if pattern else None),
    ).with_id()
