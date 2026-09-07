"""Instrument Pack — one click turns a song into a playable sampler patch.

"Sample this song" as a verb: the zip contains the song's own cleaned drum
one-shots (median-stacked composites), its best BASS note, and its best
CHORD stab, wired into an .sfz instrument any SFZ sampler loads directly
(Sforzando, DecentSampler-via-convert, Ableton Sampler import):

  * drums   → one key per hit class from C1 (36) upward
  * bass    → keys 24–59, ONE root sample + pitch_keycenter — the SAMPLER
              transposes, which beats a fleet of server-side pitch-shifted
              renders at small intervals and costs nothing to build
  * stab    → keys 60–95, same single-root trick

Source picks reuse analysis that already exists: ``midi_stems.bass.notes``
scores the cleanest sustained bass note (duration × velocity × gap to the
next onset), the musical graph ranks the chord material, the drum-kit
render cache supplies the composites. Zip is cached by content identity
like the Ableton kit export.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PACK_VERSION = 1

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _note_name(midi: int) -> str:
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 2}"


def _cache_dir() -> Optional[Path]:
    raw = os.environ.get("TONEFORGE_KIT_CACHE")  # shares the kit-zip cache knob
    if raw == "0":
        return None
    d = Path(raw) if raw else Path.home() / ".toneforge" / "kit_zip_cache"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return None


def _pick_bass_note(result: Dict) -> Optional[Dict]:
    """Cleanest sustained bass note: long, confident, and ISOLATED — the
    gap to the next note is what keeps the sampler loop tail honest."""
    stems = result.get("midi_stems")
    bass = stems.get("bass") if isinstance(stems, dict) else None
    notes = bass.get("notes") if isinstance(bass, dict) else None
    if not notes:
        return None
    notes = sorted((n for n in notes
                    if isinstance(n.get("pitch"), int)
                    and isinstance(n.get("start"), (int, float))
                    and isinstance(n.get("end"), (int, float))
                    and n["end"] > n["start"]),
                   key=lambda n: float(n["start"]))
    if not notes:
        return None
    best, best_score = None, -1.0
    for i, n in enumerate(notes):
        dur = float(n["end"]) - float(n["start"])
        nxt = float(notes[i + 1]["start"]) if i + 1 < len(notes) else 1e9
        gap = max(0.0, nxt - float(n["end"]))
        vel = float(n.get("velocity", 64)) / 127.0
        score = min(dur, 2.0) * (0.5 + 0.5 * vel) * min(1.0, 0.2 + gap)
        if score > best_score:
            best, best_score = n, score
    return best


def _pick_chord_stab(entry_id: str, result: Dict) -> Optional[Tuple]:
    """(asset, root_pc) — the graph's best chord loop plus the chord root
    under its start, so the stab lands on a meaningful keycenter."""
    try:
        from tone_forge.performance.graph import ContentType
        from tone_forge.performance.serve import graph_from_result

        g = graph_from_result(entry_id, result)
        asset = next((a for a in g.ranked_assets()
                      if a.content_type == ContentType.CHORD_LOOP), None)
    except Exception:
        return None
    if asset is None:
        return None

    root_pc = 0
    for c in result.get("chords") or []:
        if not isinstance(c, dict):
            continue
        a = c.get("start_s", c.get("startSec", c.get("start")))
        b = c.get("end_s", c.get("endSec", c.get("end")))
        sym = c.get("symbol") or c.get("chord") or ""
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
                and a <= asset.pos.start_s < b and sym:
            from tone_forge.contribute_chops import _root_pc_of
            root_pc = _root_pc_of(sym) or 0
            break
    return asset, root_pc


def _render_faded_slice(np, sf, stem_path: Path, start_s: float, end_s: float,
                        dest: Path) -> bool:
    """Slice [start, end] out of a stem at native rate, edge fades, write."""
    try:
        y, sr = sf.read(str(stem_path), dtype="float32", always_2d=True)
    except Exception:
        return False
    y = y.mean(axis=1)
    i0, i1 = int(start_s * sr), min(int(end_s * sr), y.shape[0])
    if i1 - i0 < int(0.05 * sr):
        return False
    seg = y[i0:i1].astype(np.float64)
    peak = float(np.max(np.abs(seg)))
    if peak <= 0:
        return False
    seg = seg / peak * 0.89
    fi = min(int(0.003 * sr), seg.size)
    seg[:fi] *= np.linspace(0, 1, fi)
    fo = min(max(int(0.02 * sr), int(seg.size * 0.1)), seg.size)
    seg[-fo:] *= np.exp(np.linspace(0.0, -6.0, fo))
    try:
        sf.write(str(dest), seg.astype(np.float32), sr, subtype="PCM_16")
        return True
    except Exception:
        return False


def build_instrument_pack_zip(entry_id: str, result: Dict, *,
                              song_name: str) -> Tuple[bytes, str]:
    """Assemble the zip. Raises ValueError when nothing renderable exists.
    Heavy (stem fetch + slicing); run in the render pool."""
    import tempfile

    import numpy as np
    import soundfile as sf

    from tone_forge.ableton_kit_export import _safe_name
    from tone_forge.performance.drum_kit_render import (
        ensure_kit_job, load_manifest, sample_path)
    from tone_forge.stem_fetch import materialize_stems

    title = f"{_safe_name(song_name)} Instrument"

    # Drum composites: reuse (or backfill) the drum-kit render cache.
    files = load_manifest(entry_id)
    if files is None:
        _, files = ensure_kit_job(entry_id, result)

    bass_note = _pick_bass_note(result)
    stab = _pick_chord_stab(entry_id, result)

    cache = _cache_dir()
    key = hashlib.sha1(json.dumps({
        "e": entry_id, "v": PACK_VERSION, "files": files,
        "bass": bass_note, "stab": stab[0].id if stab else None,
    }, sort_keys=True, default=str).encode()).hexdigest()
    cache_file = cache / f"inst_{key}.zip" if cache else None
    if cache_file is not None and cache_file.exists() \
            and cache_file.stat().st_size > 0:
        return cache_file.read_bytes(), f"{title}.zip"

    with tempfile.TemporaryDirectory(prefix="toneforge_inst_") as td:
        scratch = Path(td)
        samples: List[Tuple[str, Path]] = []   # (zip name, local path)
        sfz_groups: List[str] = []
        manifest: Dict = {"source": "tone-forge", "entryId": entry_id,
                          "packVersion": PACK_VERSION, "songName": song_name,
                          "drums": [], "bass": None, "stab": None}

        # --- drums: one key per class from C1 up -------------------------
        note = 36
        for pad_idx in sorted(files or {}):
            p = sample_path(entry_id, files[pad_idx])
            if p is None:
                continue
            cls = files[pad_idx].split("_", 1)[1].rsplit("_v", 1)[0]
            zname = f"Samples/{files[pad_idx]}"
            samples.append((zname, p))
            sfz_groups.append(
                f"<region> sample={zname} key={note} "
                f"pitch_keycenter={note} loop_mode=one_shot")
            manifest["drums"].append(
                {"file": zname, "class": cls, "key": note,
                 "keyName": _note_name(note)})
            note += 1

        # --- bass: one root sample, sampler transposes 24–59 --------------
        stems_needed = ["bass"] if bass_note else []
        if stab:
            stems_needed.append(stab[0].stem)
        stems = materialize_stems(result, scratch, roles=stems_needed) \
            if stems_needed else {}

        if bass_note and stems.get("bass"):
            start = float(bass_note["start"])
            end = min(float(bass_note["end"]) + 0.25, start + 2.5)
            dest = scratch / "bass_root.wav"
            if _render_faded_slice(np, sf, stems["bass"], start, end, dest):
                pitch = int(bass_note["pitch"])
                samples.append(("Samples/bass_root.wav", dest))
                sfz_groups.append(
                    f"<region> sample=Samples/bass_root.wav lokey=24 hikey=59 "
                    f"pitch_keycenter={pitch}")
                manifest["bass"] = {"file": "Samples/bass_root.wav",
                                    "rootMidi": pitch,
                                    "rootName": _note_name(pitch)}

        # --- chord stab: 60–95 --------------------------------------------
        if stab and stems.get(stab[0].stem):
            asset, root_pc = stab
            start = float(asset.pos.start_s)
            end = min(float(asset.pos.end_s), start + 1.5)
            dest = scratch / "chord_stab.wav"
            if _render_faded_slice(np, sf, stems[asset.stem], start, end, dest):
                keycenter = 60 + root_pc
                samples.append(("Samples/chord_stab.wav", dest))
                sfz_groups.append(
                    f"<region> sample=Samples/chord_stab.wav lokey=60 hikey=95 "
                    f"pitch_keycenter={keycenter}")
                manifest["stab"] = {"file": "Samples/chord_stab.wav",
                                    "rootMidi": keycenter,
                                    "rootName": _note_name(keycenter)}

        if not samples:
            raise ValueError("No instrument material renderable for this song")

        sfz = "\n".join(
            ["// Generated by Tone Forge — jamn.app",
             f"// {song_name} as a playable instrument", "",
             "<control>", "default_path=", "", "<global>", "ampeg_release=0.35",
             ""] + sfz_groups) + "\n"
        readme = (
            f"{title}\nGenerated by Tone Forge - jamn.app\n\n"
            f"Load {title}.sfz in any SFZ sampler (e.g. Sforzando).\n"
            "  C1 and up : the song's own drums (cleaned one-shots)\n"
            "  C0-B2     : the song's bass, playable chromatically\n"
            "  C3-B5     : the song's chord stab, playable chromatically\n")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{title}/{title}.sfz", sfz)
            z.writestr(f"{title}/README.txt", readme)
            z.writestr(f"{title}/pack.json", json.dumps(manifest, indent=2))
            for zname, path in samples:
                z.write(str(path), f"{title}/{zname}")
        payload = buf.getvalue()
        if cache_file is not None:
            try:
                tmp = cache_file.with_suffix(".part")
                tmp.write_bytes(payload)
                tmp.rename(cache_file)
            except Exception:
                pass
        return payload, f"{title}.zip"


def instrument_pack_job(entry_id: str, result: Dict, song_name: str):
    """Render-pool entry point."""
    return build_instrument_pack_zip(entry_id, result, song_name=song_name)
