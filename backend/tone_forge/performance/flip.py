"""Auto-Flip — a new beat built from the song's own DNA, one tap.

The chop-and-flip a producer does by hand: pick the drums, pick a bassline,
pick a stab, lay a pattern. Every ingredient already exists per song — the
hits table gives the drum exemplars AND the song's own 16th-note pattern
per class, the musical graph ranks the melodic material, the drum-kit render
cache supplies cleaned one-shots. This module assembles them into a
``kind=flip`` SamplePack whose ``defaultSequence`` is a ready-to-play
SequencerPattern, so activating the pack drops a playable flip on the grid.

The pattern is the song's own groove DNA, not a canned template: each drum
track's steps come from histogramming that class's hits onto a 16-slot bar
grid across the whole song — the flip swings like the record because it IS
the record's pattern, re-voiced. Bass rides the kick slots (the oldest trick
in the book), stabs syncopate against the snare.

Wire format notes: ``defaultSequence`` must decode as the Swift-synthesized
Codable of SequencerPattern/SequencerTrack/SequencerStep/ChopReference —
enum-with-associated-values encodes as {"case": {label: value}}. UUIDs are
uppercase strings; the pattern id is deterministic per pack so re-activation
re-saves the same pattern (idempotent by id) instead of accumulating copies.
"""
from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional, Tuple

from .drum_kit import DRUM_HITS_RESULT_KEY, _CLASS_HEX, _CLASS_LABEL

logger = logging.getLogger(__name__)

FLIP_VERSION = 1

# Drum pads on the flip grid, with fallbacks when a class is absent.
_FLIP_DRUM_SLOTS: List[Tuple[str, Tuple[str, ...]]] = [
    ("kick", ("kick", "tom", "perc")),
    ("snare", ("snare", "perc", "tom")),
    ("hat_closed", ("hat_closed", "hat_open", "cymbal")),
    ("perc", ("perc", "hat_open", "tom", "cymbal")),
]

# Step activation thresholds: a slot fires when the class hits it in at
# least this fraction of bars. Kick/snare are the skeleton (loose gate);
# hats fire densely anyway so a tighter gate keeps the pattern breathing.
_STEP_GATE = {"kick": 0.35, "snare": 0.35, "hat_closed": 0.5, "perc": 0.5}


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                          "toneforge:flip:" + ":".join(parts))).upper()


def _bar_grid(result: Dict) -> Optional[List[float]]:
    downs = [float(d) for d in (result.get("downbeats_s") or [])
             if isinstance(d, (int, float))]
    return downs if len(downs) >= 2 else None


def _class_step_map(hits: List[Dict], cls: str, downs: List[float]):
    """(counts[16], velocity[16], n_bars) — where this class lands on the
    16-slot bar grid across the song."""
    counts = [0] * 16
    vel_sum = [0.0] * 16
    n_bars = len(downs) - 1
    for h in hits:
        if h["cls"] != cls:
            continue
        t = float(h["t"])
        # Locate the bar containing t (linear scan is fine: hits are sorted
        # and n_bars is small; keep it simple over bisect bookkeeping).
        for i in range(n_bars):
            a, b = downs[i], downs[i + 1]
            if a <= t < b and b > a:
                slot = int(((t - a) / (b - a)) * 16.0 + 0.5) % 16
                counts[slot] += 1
                vel_sum[slot] += float(h.get("strength", 1.0))
                break
    vels = [vel_sum[i] / counts[i] if counts[i] else 0.0 for i in range(16)]
    return counts, vels, max(n_bars, 1)


def _steps_for(counts, vels, n_bars: int, gate: float) -> List[Dict]:
    """16 SequencerStep dicts. Velocity from the class's own dynamics;
    probability < 1 on borderline slots re-creates the song's bar-to-bar
    variation instead of a rigid loop."""
    steps = []
    for i in range(16):
        freq = counts[i] / n_bars
        if freq >= gate:
            steps.append({
                "velocity": round(max(0.25, min(1.0, vels[i])), 3),
                "probability": round(min(1.0, max(0.5, freq)), 3),
            })
        else:
            steps.append({"velocity": 0, "probability": 1.0})
    return steps


def _track(pack_id: str, pad_idx: int, name: str, steps: List[Dict],
           volume: float = 1.0) -> Dict:
    return {
        "id": _uuid_for(pack_id, "track", name),
        "chopRef": {"packPad": {"packId": pack_id, "padIdx": pad_idx}},
        "steps": steps,
        "volume": volume,
        "pan": 0,
        "isMuted": False,
        "isSoloed": False,
        "name": name,
    }


def _fixed_steps(slots: Dict[int, float], probability: float = 1.0) -> List[Dict]:
    return [{"velocity": slots.get(i, 0), "probability": probability if i in slots else 1.0}
            for i in range(16)]


def build_flip(entry_id: str, result: Dict,
               drum_sample_files: Optional[Dict[int, str]] = None) -> Dict:
    """kind=flip SamplePack + defaultSequence. Raises ValueError when the
    song has no hits table (the route backfills first, same as kind=drums)."""
    table = result.get(DRUM_HITS_RESULT_KEY)
    hits = table.get("hits") if isinstance(table, dict) else None
    if not hits:
        raise ValueError("No drum hits available for this song")

    pack_id = f"flip-{entry_id}"
    by_cls: Dict[str, List[Dict]] = {}
    for h in hits:
        by_cls.setdefault(h["cls"], []).append(h)
    for group in by_cls.values():
        group.sort(key=lambda h: h["strength"] * (0.4 + 0.6 * h["isolation"]),
                   reverse=True)

    # Map rendered drum-kit composites by class so flip pads reuse the SAME
    # cleaned files the Drum Kit serves (no second render).
    files_by_class: Dict[str, str] = {}
    for fname in (drum_sample_files or {}).values():
        cls = fname.split("_", 1)[1].rsplit("_v", 1)[0]
        files_by_class.setdefault(cls, fname)

    pads: List[Dict] = []
    drum_pad_idx: Dict[str, int] = {}
    for wanted, chain in _FLIP_DRUM_SLOTS:
        pick = None
        picked_cls = wanted
        for c in chain:
            if by_cls.get(c):
                pick, picked_cls = by_cls[c][0], c
                break
        if pick is None:
            continue
        idx = len(pads)
        drum_pad_idx[wanted] = idx
        fname = files_by_class.get(picked_cls)
        pads.append({
            "padIdx": idx,
            "name": _CLASS_LABEL[picked_cls],
            "category": "DRUMS",
            "family": "percussion",
            "colorHint": _CLASS_HEX[picked_cls],
            "stemSlice": {"stemRole": "drums",
                          "startSec": pick["t"], "endSec": pick["end"]},
            "loopable": False,
            "defaultQuantize": "off",
            **({"sampleUrl": f"/api/song/{entry_id}/drum-sample/{fname}"}
               if fname else {}),
        })

    # Melodic material from the graph's ranked assets (best bass groove,
    # two distinct chord loops, one lead). Degrades to a drums-only flip
    # when the song has no graph — still a playable beat.
    melodic_pads = _melodic_pads(entry_id, result, start_idx=len(pads))
    pads.extend(melodic_pads["pads"])

    if not pads:
        raise ValueError("No material to flip")

    tracks: List[Dict] = []
    downs = _bar_grid(result)
    if downs:
        for cls, pad_idx in drum_pad_idx.items():
            counts, vels, n_bars = _class_step_map(hits, cls, downs)
            steps = _steps_for(counts, vels, n_bars, _STEP_GATE[cls])
            if any(s["velocity"] > 0 for s in steps):
                tracks.append(_track(pack_id, pad_idx, _CLASS_LABEL[cls], steps))
    if not tracks and drum_pad_idx:
        # No downbeat grid: fall back to the least-wrong universal pattern
        # (four-on-the-floor kick, backbeat snare, 8th hats).
        if "kick" in drum_pad_idx:
            tracks.append(_track(pack_id, drum_pad_idx["kick"], "Kick",
                                 _fixed_steps({0: 1.0, 4: 0.9, 8: 1.0, 12: 0.9})))
        if "snare" in drum_pad_idx:
            tracks.append(_track(pack_id, drum_pad_idx["snare"], "Snare",
                                 _fixed_steps({4: 1.0, 12: 1.0})))
        if "hat_closed" in drum_pad_idx:
            tracks.append(_track(pack_id, drum_pad_idx["hat_closed"], "Hats",
                                 _fixed_steps({i: 0.7 for i in range(0, 16, 2)})))

    # Bass rides the kick; stabs answer the snare; lead is a sparse hook.
    kick_track = next((t for t in tracks if t["name"] == "Kick"), None)
    if melodic_pads["bass_idx"] is not None and kick_track:
        bass_steps = [
            {"velocity": 0.9 if s["velocity"] > 0 else 0, "probability": 1.0}
            for s in kick_track["steps"]]
        tracks.append(_track(pack_id, melodic_pads["bass_idx"], "Bass",
                             bass_steps, volume=0.9))
    for n, idx in enumerate(melodic_pads["chord_idxs"]):
        slot = 2 if n == 0 else 10  # syncopated answers around the backbeat
        tracks.append(_track(pack_id, idx, f"Stab {n + 1}",
                             _fixed_steps({slot: 0.8}), volume=0.85))
    if melodic_pads["lead_idx"] is not None:
        tracks.append(_track(pack_id, melodic_pads["lead_idx"], "Lead",
                             _fixed_steps({14: 0.6}, probability=0.5),
                             volume=0.8))

    pattern = {
        "id": _uuid_for(pack_id, f"v{FLIP_VERSION}"),
        "name": "Flip",
        "stepCount": 16,
        "tracks": tracks,
        "swing": 0,
        "isLooping": True,
    }

    return {
        "manifestVersion": 2,
        "packId": pack_id,
        "name": "Flip",
        "family": "mixed",
        "paletteHint": "song",
        "pads": pads,
        "defaultSequence": pattern,
        "provenance": (
            f"flip v{FLIP_VERSION} hits={len(hits)} "
            f"tracks={len(tracks)} pads={len(pads)}"
        ),
    }


def _melodic_pads(entry_id: str, result: Dict, start_idx: int) -> Dict:
    """Best bass groove + two distinct chord loops + a lead from the graph.
    Empty (drums-only flip) when the song has no usable graph."""
    out = {"pads": [], "bass_idx": None, "chord_idxs": [], "lead_idx": None}
    try:
        from .graph import ContentType
        from .serve import graph_from_result

        g = graph_from_result(entry_id, result)
        assets = g.ranked_assets()
    except Exception:
        return out
    if not assets:
        return out

    def _best(ct, exclude_patterns=()):
        for a in assets:
            if a.content_type == ct and a.pattern_id not in exclude_patterns:
                return a
        return None

    def _pad(a, name, category, color, loopable):
        idx = start_idx + len(out["pads"])
        end = min(a.pos.end_s, a.pos.start_s + 8.0)
        out["pads"].append({
            "padIdx": idx,
            "name": name,
            "category": category,
            "family": "bass" if category == "BASS" else "stabs",
            "colorHint": color,
            "stemSlice": {"stemRole": a.stem,
                          "startSec": round(a.pos.start_s, 4),
                          "endSec": round(end, 4)},
            "loopable": loopable,
            "defaultQuantize": "1 bar" if loopable else "off",
            "assetId": a.id,
        })
        return idx

    bass = _best(ContentType.BASS_GROOVE)
    if bass:
        out["bass_idx"] = _pad(bass, "Bass", "BASS", "#22C55E", True)
    c1 = _best(ContentType.CHORD_LOOP)
    if c1:
        out["chord_idxs"].append(_pad(c1, "Stab 1", "CHORDS", "#F59E0B", False))
        c2 = _best(ContentType.CHORD_LOOP,
                   exclude_patterns=(c1.pattern_id,) if c1.pattern_id else ())
        if c2:
            out["chord_idxs"].append(
                _pad(c2, "Stab 2", "CHORDS", "#F59E0B", False))
    lead = _best(ContentType.LEAD_LOOP)
    if lead:
        out["lead_idx"] = _pad(lead, "Lead", "LEAD", "#F97316", False)
    return out
