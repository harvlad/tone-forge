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
# Kit-pad sample window CAP (seconds) — memory bound per slice, matching
# StemSlice.maxChopDurationSec on the clients. The actual window is the
# asset's bar-aligned span, truncated to whole bars under this cap: a fixed
# 8 s cut ignored the phrase's musical boundaries, so loopStartSec/EndSec
# described a region the loop metrics were never measured on.
_SAMPLE_LEN_SEC = 8.0

# Absolute audibility floor (plain RMS ≈ −40 dBFS). loop_confidence rewards a
# steady head==tail seam, which a near-silent sustain aces — so quiet residue
# sailed through the usable gate and onto pads. Energy lives on the Phrase,
# not the asset, so the builder resolves it via source_id (loop → phrase).
_ENERGY_FLOOR = 0.01

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
# Grid layout row order: pads are GROUPED by category in the final padIdx
# assignment (drums together, then bass, then harmonic material, then
# lead/vocal, then texture/FX) instead of landing in raw ranking order and
# scattering categories across the grid. Backend-side so every surface —
# mobile 4x4, desktop 8x8, plugin — inherits the same grouped rack.
_CATEGORY_GROUP_ORDER = {
    "DRUMS": 0, "BASS": 1, "CHORDS": 2, "RHYTHM": 3, "LEAD": 4, "VOCAL": 5,
    "TEXTURE": 6, "STAB": 7, "FX": 8, "SAMPLE": 9,
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
        usage: Optional[Dict] = None,
    ) -> Dict:
        rule = _SKILL.get(skill, _SKILL["intermediate"])

        # Usage feedback fold: what the user actually plays outranks
        # what the analyzer guessed. Bounded nudges (tanh) so usage
        # can bias ranking but never swamp audio quality: +0.15 max
        # for repeated plays, -0.20 max for repeated instant-kills.
        import math

        def _score(a) -> float:
            s = a.performance_score
            u = (usage or {}).get(a.id)
            if isinstance(u, dict):
                s += 0.15 * math.tanh(float(u.get("play", 0)) / 5.0)
                s -= 0.20 * math.tanh(float(u.get("skip", 0)) / 3.0)
            return s
        self._score = _score

        # Audibility: assets carry no energy, so look it up on the source
        # phrase (asset.source_id is a loop id or a phrase id). Unknown energy
        # passes — legacy/synthetic graphs without phrases must not be muted.
        # Audibility per phrase = its loudest BAR, not the whole-phrase mean:
        # a 4-bar phrase whose content lives in bar 4 must count as audible
        # (the window picker in _to_sample_pack lands the pad on that bar).
        phrase_energy = {p.id: (max(p.bar_energies) if getattr(p, "bar_energies", ()) else p.energy)
                         for p in (getattr(graph, "phrases", ()) or ())}
        loop_phrase = {lp.id: lp.phrase_id
                       for lp in (getattr(graph, "loops", ()) or ())}

        def _audible(a) -> bool:
            e = phrase_energy.get(loop_phrase.get(a.source_id, a.source_id))
            return e is None or e >= _ENERGY_FLOOR
        self._audible = _audible

        # A pad must be actually usable: loopable OR a decent-scoring one-shot
        # — AND audible. loop_confidence/performance_score both reward steady
        # material, so a whisper-quiet sustain cleared them; the energy floor
        # is the only term that can veto on level alone.
        usable = [
            a for a in graph.ranked_assets()
            if _audible(a) and (a.loop_confidence > 0.2 or a.performance_score > 0.4)
        ]
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

        # Anchor slot: the STEADIEST drum groove from a body section.
        # Generic ranking kept picking outro/intro drum phrases ("only a
        # drum outro which isn't a consistent beat") — a kit needs one
        # drums pad that just plays through. Steadiness proxy =
        # loop_confidence (seamless/regular) weighted over raw score,
        # with intro/outro/ending material heavily penalized.
        #
        # Drawn from ALL ranked assets, not from `pool`. The anchor used to
        # select post-gate, which made it a no-op in exactly the case it was
        # written for: `usable` cuts on loop_confidence/performance_score, the
        # two numbers percussion scored worst on, so the drums were already
        # gone by the time the anchor looked for them and the kit came back
        # with no drum pad at all. A song that HAS drums gets a drums pad; the
        # gate still governs the seven generic slots below.
        # The anchor bypasses the score gates by design, but not the energy
        # floor — a near-silent drums stem anchoring pad 0 is the exact defect
        # the floor exists for.
        drum_pool = self._one_per_pattern(
            [a for a in graph.ranked_assets() if a.stem == "drums" and _audible(a)]
        )
        if drum_pool:
            def _groove_key(a) -> float:
                sec = _section_at(sections or [], a.pos.start_s).lower()
                boundary = any(w in sec for w in
                               ("intro", "outro", "ending", "transition"))
                return (2.0 * a.loop_confidence + _score(a)
                        - (1.5 if boundary else 0.0))
            anchor = max(drum_pool, key=_groove_key)
            chosen.append(anchor)
            self._mark(anchor, used_ids, used_patterns, stem_counts)

        for _slot_name, prefs in _KIT_SLOTS[:pads]:
            if len(chosen) >= pads:
                break  # the drum anchor may already occupy a slot
            pick = self._best_for(pool, prefs, used_ids, used_patterns, stem_counts)
            if pick:
                chosen.append(pick)
                self._mark(pick, used_ids, used_patterns, stem_counts)
        # Top up remaining slots, re-ranking every pick with the same
        # stem-diversity penalty _best_for uses. The old plain rank scan let
        # one loud stem sweep the kit (a 16-pad rap kit came back 10/16
        # vocals); the growing per-stem penalty hands later slots to the
        # next-best other stems instead.
        while len(chosen) < pads:
            best, best_key = None, float("-inf")
            for a in pool:
                if a.id in used_ids or (a.pattern_id and a.pattern_id in used_patterns):
                    continue
                # `pool` may be the relaxed starvation pool (raw ranked
                # assets), so the top-up must re-check audibility or it
                # re-seats exactly the near-silent slices the gate excluded.
                if not _audible(a):
                    continue
                key = self._score(a) - 0.15 * stem_counts.get(a.stem, 0)
                if key > best_key:
                    best, best_key = a, key
            if best is None:
                break
            chosen.append(best); self._mark(best, used_ids, used_patterns, stem_counts)
        if not chosen:
            # Total starvation (every asset under the energy floor): a quiet
            # kit beats an empty one — relax the floor, keep the ranking.
            for a in pool[:pads]:
                chosen.append(a); self._mark(a, used_ids, used_patterns, stem_counts)

        # Layout pass, AFTER selection: group pads by category in a stable,
        # musical row order (drums → bass → chords → riffs → lead/vocal →
        # texture/FX). The sort is stable, so relative rank within a category
        # is preserved, and the drum-groove anchor keeps pad 0 — it was chosen
        # first and DRUMS is row 0. Selection/ranking above is untouched.
        chosen.sort(key=lambda a: _CATEGORY_GROUP_ORDER.get(
            _category_for(a), len(_CATEGORY_GROUP_ORDER)))

        from tone_forge import pad_usage as _pu
        return self._to_sample_pack(
            # Human name — song_id is an analysis hash, never show it in UI.
            graph, chosen, pack_name or "Auto Kit", skill, sections or [],
            use_digest=_pu.digest(usage))

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
        merged.sort(key=self._score, reverse=True)
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
                if not self._audible(a):  # relaxed pool can hold silent slices
                    continue
                key = self._score(a) - 0.15 * stem_counts.get(a.stem, 0)
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

    def _to_sample_pack(self, graph, assets, name, skill, sections=None,
                        use_digest="0") -> Dict:
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
        phrases_by_id = {p.id: p for p in (getattr(graph, "phrases", ()) or ())}

        # LOCAL bar length per asset, derived from the phrase's own real-grid
        # span — NOT the constant song tempo. The grid's real downbeats drift
        # a few dozen ms per bar against any constant BPM (Doomsday: 7.570 s
        # of real 3-bar drums vs 7.545 s at the constant tempo), and a
        # constant-tempo window cuts short of the real downbeat: the wrap
        # lands in the pre-beat gap and the loop audibly pauses. The phrase
        # bounds ARE real downbeats (PhraseAnalyzer snaps to the grid), so
        # whole-local-bar windows wrap exactly on the beat.
        beats_per_bar = int((getattr(graph, "time_signature", None) or (4, 4))[0] or 4)
        tempo = float(getattr(graph, "grid_tempo_bpm", 0.0) or 0.0)
        const_bar_s = beats_per_bar * 60.0 / tempo if tempo > 0 else 0.0

        pads = []
        for idx, a in enumerate(assets):
            lp = loops_by_id.get(getattr(a, "source_id", None))
            qual = getattr(lp, "quality", None) if lp else None
            span = a.pos.end_s - a.pos.start_s
            n_bars_total = max(1, round((getattr(a.pos, "length_beats", 0.0) or 0.0)
                                        / beats_per_bar)) if span > 0 else 1
            local_bar_s = span / n_bars_total if span > 0 else const_bar_s
            # Pad window = the asset's actual bar-aligned span, preferring the
            # analyzer's optimized seam window when it measured one — that is
            # the region loop_confidence/crossfade_ms were computed on. The
            # user can still shorten/extend the window in the chop editor.
            opt_s = getattr(qual, "optimized_start_s", None) if qual else None
            opt_e = getattr(qual, "optimized_end_s", None) if qual else None
            # The analyzer's zero-crossing nudges make optimized windows a few
            # dozen ms off whole bars; a non-integer-bar window breaks the
            # shared phase-lock cycle. Only take it when it IS whole local
            # bars (±10 ms) and fits the cap.
            use_opt = False
            if opt_s is not None and opt_e is not None and float(opt_e) > float(opt_s):
                opt_len = float(opt_e) - float(opt_s)
                if opt_len <= _SAMPLE_LEN_SEC + 1e-6 and local_bar_s > 0:
                    n = round(opt_len / local_bar_s)
                    use_opt = n >= 1 and abs(opt_len - n * local_bar_s) <= 0.010
            if use_opt:
                q_start, q_end = float(opt_s), float(opt_e)
            else:
                q_start, q_end = a.pos.start_s, a.pos.end_s
            # Keep the 8 s memory cap, but truncate in whole LOCAL bars —
            # never below one bar (a very slow song's single bar may exceed
            # the cap, and a fractional-bar cut breaks the phase-lock cycle).
            # When the phrase carries a per-bar energy profile, place the
            # truncated window on the loudest contiguous bar run — a blind
            # head-keep exported near-silence when a phrase's content sat in
            # its tail (silent-head phrases read as audible on phrase RMS).
            if q_end - q_start > _SAMPLE_LEN_SEC + 1e-6:
                if local_bar_s > 0:
                    cap_bars = max(1, int(_SAMPLE_LEN_SEC / local_bar_s))
                    n_bars = max(1, int(round((q_end - q_start) / local_bar_s)))
                    # Truncate to a bar count that DIVIDES the phrase, not
                    # the largest that fits: a 4-bar groove capped at 3 bars
                    # wraps bar 3 → bar 1, skipping the fill bar that leads
                    # back into the "1" — an audible dead spot at every wrap
                    # ("in time but not seamless", measured as a 110 ms
                    # energy hole vs 60 ms at ordinary bar boundaries). A
                    # 2-bar cut of the same groove is pattern-coherent.
                    # Phrases whose only fitting divisor is 1 bar (prime
                    # counts > cap) keep the largest-fit cut — a 1-bar loop
                    # of a 5-bar phrase is no more coherent than 3 bars and
                    # loses content.
                    k = max((d for d in range(1, cap_bars + 1)
                             if n_bars % d == 0), default=1)
                    if k == 1 and cap_bars > 1 and n_bars > 1:
                        k = min(cap_bars, n_bars)
                    ph = phrases_by_id.get(getattr(lp, "phrase_id", None) or a.source_id)
                    be = tuple(getattr(ph, "bar_energies", ()) or ()) if ph else ()
                    j = 0
                    if n_bars > k and len(be) >= n_bars:
                        power = [e * e for e in be[:n_bars]]
                        sums = [sum(power[i:i + k]) for i in range(n_bars - k + 1)]
                        j = max(range(len(sums)), key=sums.__getitem__)
                    q_start = q_start + j * local_bar_s
                    q_end = q_start + k * local_bar_s
                else:  # no tempo → the fixed cap is the only bound available
                    q_end = q_start + _SAMPLE_LEN_SEC
            # Loop the whole exported window (full-slice loop).
            loop_start, loop_end = q_start, q_end
            # Carry the analyzer's per-seam crossfade measurement when present
            # (the app prefers it over its coarse loopScore→ms fallback). When
            # the window was bar-truncated away from the measured loop this is
            # approximate — clients clamp it to 8–30 ms, which is acceptable.
            xfade_ms = getattr(qual, "crossfade_ms", None) if qual else None
            pads.append(
                {
                    "padIdx": idx,
                    # Stable graph-asset id: the feedback loop keys
                    # play/skip events on this, not the pad slot.
                    "assetId": a.id,
                    # Descriptive 'what is this' name (section+instrument+role),
                    # not a generic slot — so the user knows each pad instantly.
                    "name": _descriptive_label(a, sections),
                    "category": _category_for(a),
                    "family": _family_for(a.content_type),
                    # Category color so every client shows a grouped, color-coded
                    # rack straight from colorHint (no per-client color logic).
                    "colorHint": _CATEGORY_HEX.get(_category_for(a), a.color_hint),
                    "stemSlice": {"stemRole": a.stem, "startSec": round(q_start, 4), "endSec": round(q_end, 4)},
                    # performance-intelligence additive fields (app reads if present):
                    "loopStartSec": round(loop_start, 4),
                    "loopEndSec": round(loop_end, 4),
                    "loopScore": round(a.loop_confidence, 3),
                    **({"crossfadeMs": round(float(xfade_ms), 2)}
                       if isinstance(xfade_ms, (int, float)) and xfade_ms > 0 else {}),
                    # Loop only what can survive a seam: force-looping
                    # genuinely seam-hostile material audibly stuttered, so
                    # below the usable gate's own loop threshold a pad plays
                    # as a one-shot. Deliberately NOT the classifier's 0.55 —
                    # that flips far too many pads out of layering.
                    "loopable": a.loop_confidence >= 0.2,
                    "contentType": a.content_type.value,
                    "performanceScore": round(a.performance_score, 3),
                    "difficulty": round(a.difficulty, 3),
                    "defaultQuantize": "1 bar",
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
            # kit=… versions the BUILDER logic (drum-groove anchor etc.)
            # separately from the graph — it feeds the export zip-cache
            # key, so bumping it invalidates stale cached kits.
            # kit=5: category-grouped pad layout (padIdx rows by category).
            # kit=6: bar-aligned pad windows, energy floor, honest loopable.
            "provenance": (
                f"performance_intelligence graph={graph.graph_hash} "
                f"module={graph.module_version} kit=6 use={use_digest} "
                f"skill={skill}"
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
