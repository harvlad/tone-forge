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

        return self._to_sample_pack(graph, chosen, pack_name or f"{graph.song_id} — Auto Kit", skill)

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

    def _to_sample_pack(self, graph, assets, name, skill) -> Dict:
        """Emit the frozen SamplePack manifest shape (SamplePack.swift):
        packId/name/family/pads[] with per-pad loop region + loopScore so the
        app can honor real seamless loops."""
        pads = []
        for idx, a in enumerate(assets):
            q_start = a.pos.start_s
            q_end = a.pos.end_s
            pads.append(
                {
                    "padIdx": idx,
                    "name": _KIT_SLOTS[idx][0] if idx < len(_KIT_SLOTS) else a.label,
                    "family": _family_for(a.content_type),
                    "colorHint": a.color_hint,
                    "stemSlice": {"stemRole": a.stem, "startSec": q_start, "endSec": q_end},
                    # performance-intelligence additive fields (app reads if present):
                    "loopStartSec": q_start,
                    "loopEndSec": q_end,
                    "loopScore": round(a.loop_confidence, 3),
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
