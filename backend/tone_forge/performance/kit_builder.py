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
        pool = [
            a for a in graph.ranked_assets()
            if a.difficulty <= rule["max_difficulty"]
            and (a.loopable or not rule["require_loop"])
        ]
        # fall back to the full ranked list if the skill filter starves the kit
        if len(pool) < pads:
            pool = graph.ranked_assets()

        chosen: List[PerformanceAsset] = []
        used = set()
        # fill role slots best-first, avoiding dup patterns where possible
        for slot_name, prefs in _KIT_SLOTS[:pads]:
            pick = self._best_for(pool, prefs, used)
            if pick:
                chosen.append(pick)
                used.add(pick.id)
                if pick.pattern_id:
                    used.add(pick.pattern_id)
        # top up any empty slots with the next best unused assets
        for a in pool:
            if len(chosen) >= pads:
                break
            if a.id not in used:
                chosen.append(a); used.add(a.id)

        return self._to_sample_pack(graph, chosen, pack_name or f"{graph.song_id} — Auto Kit", skill)

    def _best_for(self, pool, prefs, used) -> Optional[PerformanceAsset]:
        # prefer an asset of a preferred content type, highest score, unused
        for ct in prefs:
            best = None
            for a in pool:
                if a.id in used or (a.pattern_id and a.pattern_id in used):
                    continue
                if a.content_type == ct and (best is None or a.performance_score > best.performance_score):
                    best = a
            if best:
                return best
        return None

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
            "family": "song",
            "paletteHint": "song",
            "pads": pads,
            "provenance": {
                "source": "performance_intelligence",
                "graph_hash": graph.graph_hash,
                "module_version": graph.module_version,
                "skill": skill,
            },
        }


def _family_for(ct: ContentType) -> str:
    return {
        ContentType.RHYTHM_LOOP: "drums", ContentType.BASS_GROOVE: "bass",
        ContentType.LEAD_LOOP: "lead", ContentType.CHORD_LOOP: "chord",
        ContentType.TEXTURE: "texture", ContentType.DRONE: "texture",
        ContentType.AMBIENT: "texture", ContentType.ONE_SHOT: "oneshot",
        ContentType.IMPACT: "fx", ContentType.TRANSITION: "fx",
        ContentType.PICKUP: "fx", ContentType.ENDING: "fx",
    }.get(ct, "song")
