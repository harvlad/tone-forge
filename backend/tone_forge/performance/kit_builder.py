"""AutoKitBuilder — assemble a complete, immediately-performable Launchpad kit
from the graph's ranked PerformanceAssets, emitting the existing SamplePack
manifest shape so the current desktop/mobile Launchpad UI consumes it unchanged.

Kit design (the spec's example): a balanced 8-pad bank a user can perform the
song with right away — main riff, a variation, chord stab, bass groove, lead
phrase, transition, texture, ending — chosen best-first with role coverage and
skill-level filtering.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .graph import ContentType, MusicalGraph, PerformanceAsset

# Ideal 8-pad role layout (content types preferred per slot, best-first).
_KIT_SLOTS = [
    ("Main riff", [ContentType.RHYTHM_LOOP, ContentType.LEAD_LOOP, ContentType.CHORD_LOOP]),
    ("Variation", [ContentType.LEAD_LOOP, ContentType.RHYTHM_LOOP, ContentType.CHORD_LOOP]),
    ("Chord stab", [ContentType.CHORD_LOOP, ContentType.ONE_SHOT]),
    ("Bass groove", [ContentType.BASS_GROOVE]),
    ("Lead phrase", [ContentType.LEAD_LOOP, ContentType.CHORD_LOOP]),
    ("Transition", [ContentType.TRANSITION, ContentType.IMPACT, ContentType.PICKUP]),
    ("Texture", [ContentType.TEXTURE, ContentType.DRONE, ContentType.AMBIENT]),
    ("Ending", [ContentType.ENDING, ContentType.IMPACT, ContentType.ONE_SHOT]),
]

# --- Categorization + human labels so the user knows what each pad IS ---
# Category drives grouping + color in the grid; label is the descriptive name.
_ROLE_CATEGORY = {
    ContentType.RHYTHM_LOOP: "RHYTHM",
    ContentType.LEAD_LOOP: "LEAD",
    ContentType.CHORD_LOOP: "CHORDS",
    ContentType.BASS_GROOVE: "BASS",
    ContentType.TEXTURE: "TEXTURE",
    ContentType.DRONE: "TEXTURE",
    ContentType.AMBIENT: "TEXTURE",
    ContentType.IMPACT: "FX",
    ContentType.TRANSITION: "FX",
    ContentType.PICKUP: "FX",
    ContentType.ENDING: "FX",
    ContentType.ONE_SHOT: "STAB",
}
# stem wins for the strong instrument categories; content_type covers the rest.
_STEM_CATEGORY = {"drums": "DRUMS", "bass": "BASS", "vocals": "VOCAL"}
# Per-category accent (matches the desktop PadCategory palette) so every client
# renders the same grouped rack from colorHint — no per-client color logic.
_CATEGORY_HEX = {
    "DRUMS": "#EF4444", "BASS": "#22C55E", "CHORDS": "#F59E0B", "LEAD": "#F97316",
    "VOCAL": "#EC4899", "RHYTHM": "#3B82F6", "TEXTURE": "#06B6D4", "FX": "#A855F7",
    "STAB": "#8B5CF6", "SAMPLE": "#64748B",
}
_INSTRUMENT = {
    "drums": "Drums", "bass": "Bass", "vocals": "Vocal",
    "other": "Guitar", "guitar": "Guitar", "guitar_center": "Guitar",
    "guitar_sides": "Guitar", "guitar_left": "Guitar", "guitar_right": "Guitar",
    "piano": "Keys", "keys": "Keys",
}
_ROLE_WORD = {
    ContentType.RHYTHM_LOOP: "riff", ContentType.LEAD_LOOP: "lead",
    ContentType.CHORD_LOOP: "chords", ContentType.BASS_GROOVE: "groove",
    ContentType.TEXTURE: "texture", ContentType.DRONE: "pad",
    ContentType.AMBIENT: "pad", ContentType.IMPACT: "hit",
    ContentType.TRANSITION: "fill", ContentType.PICKUP: "pickup",
    ContentType.ENDING: "ending", ContentType.ONE_SHOT: "stab",
}


def _category_for(asset) -> str:
    cat = _STEM_CATEGORY.get(asset.stem)
    if cat:
        return cat
    return _ROLE_CATEGORY.get(asset.content_type, "SAMPLE")


def _section_at(sections, t: float) -> str:
    """Section label covering time ``t`` (verse/chorus/drop/…), or ''. sections
    is [(start_s, end_s, label), …]."""
    for start, end, label in (sections or []):
        if start <= t < end:
            return str(label or "").strip()
    return ""


def _descriptive_label(asset, sections) -> str:
    """A human 'what is this' name, INSTRUMENT-first so it stays readable
    when the grid tile truncates: '{Instrument} {role} {Section}' —
    e.g. 'Guitar riff Chorus', 'Bass groove Verse', 'Drums beat'. Section
    trails so two pads of the same instrument/role still differ on the
    second line without hiding the instrument up front."""
    inst = _INSTRUMENT.get(asset.stem, asset.stem.replace("_", " ").title())
    role = "beat" if asset.stem == "drums" else _ROLE_WORD.get(asset.content_type, "loop")
    sec = _section_at(sections, asset.pos.start_s)
    sec_txt = (" " + sec.title()) if sec and sec.lower() not in ("section", "") else ""
    return f"{inst} {role}{sec_txt}".strip()


# Skill → which assets are eligible (difficulty ceiling + loop preference).
_SKILL = {
    "beginner": dict(max_difficulty=0.5, require_loop=True),
    "intermediate": dict(max_difficulty=0.75, require_loop=False),
    "advanced": dict(max_difficulty=1.01, require_loop=False),
}


class AutoKitBuilder:
    def build(
        self,
        graph: MusicalGraph,
        skill: str = "intermediate",
        pads: int = 8,
        pack_name: Optional[str] = None,
        sections: Optional[List] = None,
    ) -> Dict:
        rule = _SKILL.get(skill, _SKILL["intermediate"])
        # A pad must be actually usable: loopable OR a decent-scoring one-shot.
        usable = [a for a in graph.ranked_assets() if a.loop_confidence > 0.2 or a.performance_score > 0.4]
        pool = [
            a for a in usable
            if a.difficulty <= rule["max_difficulty"] and (a.loopable or not rule["require_loop"])
        ]
        if len(pool) < pads:  # skill filter starved the kit → widen
            pool = usable or graph.ranked_assets()

        # De-dupe near-identical material: keep the best asset per pattern so a
        # riff that repeats 15× doesn't take 8 pads.
        pool = self._one_per_pattern(pool)

        chosen: List[PerformanceAsset] = []
        used_ids: set = set()
        used_patterns: set = set()
        stem_counts: Dict[str, int] = {}
        for _slot_name, prefs in _KIT_SLOTS[:pads]:
            pick = self._best_for(pool, prefs, used_ids, used_patterns, stem_counts)
            if pick:
                chosen.append(pick)
                self._mark(pick, used_ids, used_patterns, stem_counts)
        # top up remaining slots with the next best unused (still diverse) assets
        for a in pool:
            if len(chosen) >= pads:
                break
            if a.id in used_ids or (a.pattern_id and a.pattern_id in used_patterns):
                continue
            chosen.append(a); self._mark(a, used_ids, used_patterns, stem_counts)

        return self._to_sample_pack(
            graph, chosen, pack_name or f"{graph.song_id} — Auto Kit", skill, sections or [])

    def _one_per_pattern(self, pool: List[PerformanceAsset]) -> List[PerformanceAsset]:
        best: Dict[str, PerformanceAsset] = {}
        loose: List[PerformanceAsset] = []
        for a in pool:  # pool already ranked best-first
            if a.pattern_id:
                if a.pattern_id not in best:
                    best[a.pattern_id] = a
            else:
                loose.append(a)
        merged = list(best.values()) + loose
        merged.sort(key=lambda a: a.performance_score, reverse=True)
        return merged

    def _best_for(self, pool, prefs, used_ids, used_patterns, stem_counts) -> Optional[PerformanceAsset]:
        # prefer preferred content type, then highest score, penalizing a stem
        # already used a lot (diversity) and avoiding repeated patterns.
        for ct in prefs:
            best, best_key = None, -1.0
            for a in pool:
                if a.id in used_ids or (a.pattern_id and a.pattern_id in used_patterns):
                    continue
                if a.content_type != ct:
                    continue
                key = a.performance_score - 0.15 * stem_counts.get(a.stem, 0)
                if key > best_key:
                    best, best_key = a, key
            if best:
                return best
        return None

    def _mark(self, a, used_ids, used_patterns, stem_counts):
        used_ids.add(a.id)
        if a.pattern_id:
            used_patterns.add(a.pattern_id)
        stem_counts[a.stem] = stem_counts.get(a.stem, 0) + 1

    def _to_sample_pack(self, graph, assets, name, skill, sections=None) -> Dict:
        """Emit the frozen SamplePack manifest shape (SamplePack.swift):
        packId/name/family/pads[] with per-pad loop region + loopScore so the
        app can honor real seamless loops."""
        # Loop lookup so a pad can carry the OPTIMIZED loop seam (LoopAnalyzer's
        # crossfaded [optimized_start_s, optimized_end_s]) instead of the raw
        # phrase bounds — the app loops that tighter sub-region for a clean seam.
        loops_by_id = {}
        for lp in (getattr(graph, "loops", ()) or ()):
            if getattr(lp, "id", None):
                loops_by_id[lp.id] = lp

        pads = []
        for idx, a in enumerate(assets):
            q_start = a.pos.start_s
            q_end = a.pos.end_s
            # Loop region: the analyzer's optimized seam when it repaired one,
            # else the phrase bounds. stemSlice stays the full phrase (initial
            # play region); loopStart/End is the sub-region to cycle.
            loop_start, loop_end = q_start, q_end
            lp = loops_by_id.get(getattr(a, "source_id", None))
            qual = getattr(lp, "quality", None) if lp else None
            os_ = getattr(qual, "optimized_start_s", None) if qual else None
            oe_ = getattr(qual, "optimized_end_s", None) if qual else None
            if isinstance(os_, (int, float)) and isinstance(oe_, (int, float)) and oe_ > os_:
                loop_start, loop_end = os_, oe_
            # The analyzer's per-seam crossfade measurement (ms). The app
            # prefers this over its coarse loopScore→ms fallback when > 0.
            xfade_ms = getattr(qual, "crossfade_ms", None) if qual else None
            pads.append(
                {
                    "padIdx": idx,
                    # Descriptive 'what is this' name (section+instrument+role),
                    # not a generic slot — so the user knows each pad instantly.
                    "name": _descriptive_label(a, sections),
                    "category": _category_for(a),
                    "family": _family_for(a.content_type),
                    # Category color so every client shows a grouped, color-coded
                    # rack straight from colorHint (no per-client color logic).
                    "colorHint": _CATEGORY_HEX.get(_category_for(a), a.color_hint),
                    "stemSlice": {"stemRole": a.stem, "startSec": q_start, "endSec": q_end},
                    # performance-intelligence additive fields (app reads if present):
                    "loopStartSec": round(loop_start, 4),
                    "loopEndSec": round(loop_end, 4),
                    "loopScore": round(a.loop_confidence, 3),
                    **({"crossfadeMs": round(float(xfade_ms), 2)}
                       if isinstance(xfade_ms, (int, float)) and xfade_ms > 0 else {}),
                    "loopable": a.loopable,
                    "contentType": a.content_type.value,
                    "performanceScore": round(a.performance_score, 3),
                    "difficulty": round(a.difficulty, 3),
                    "defaultQuantize": "1 bar" if a.pos.length_bars >= 1 else "1/2",
                }
            )
        return {
            "manifestVersion": 2,
            "packId": f"auto-{graph.song_id}-{skill}",
            "name": name,
            "family": "mixed",     # valid SampleFamily
            "paletteHint": "song",
            "pads": pads,
            # provenance is a STRING on the wire (SamplePack.provenance: String?).
            # Emitting a dict here made JSONDecoder fail the ENTIRE kit with a
            # typeMismatch ("data isn't in the correct format") — the pads never
            # reached the app. Keep it a compact human/debuggable string.
            "provenance": (
                f"performance_intelligence graph={graph.graph_hash} "
                f"module={graph.module_version} skill={skill}"
            ),
        }


def _family_for(ct: ContentType) -> str:
    # Map content type → a valid SampleFamily raw value (SamplePack.swift):
    # pads/percussion/textures/stabs/bass/fx/vocals/mixed.
    return {
        ContentType.RHYTHM_LOOP: "percussion", ContentType.BASS_GROOVE: "bass",
        ContentType.LEAD_LOOP: "stabs", ContentType.CHORD_LOOP: "stabs",
        ContentType.TEXTURE: "textures", ContentType.DRONE: "textures",
        ContentType.AMBIENT: "textures", ContentType.ONE_SHOT: "stabs",
        ContentType.IMPACT: "fx", ContentType.TRANSITION: "fx",
        ContentType.PICKUP: "fx", ContentType.ENDING: "fx",
    }.get(ct, "mixed")
