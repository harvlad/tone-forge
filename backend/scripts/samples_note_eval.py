"""Note-level MIDI extraction eval on the first-party samples corpus.

Same scoring contract as ``scripts.slakh_eval`` (strict mir_eval
onset+pitch PRF via ``bench.note_metrics``, production
``extract_midi_hybrid`` per role) but ground truth comes from the
composed multitrack MIDI in ``samples/`` — the actual notes the songs
were rendered from. First-party, product-representative, and immune
to the annotation drift that made the retired baseline-captured chord
fixtures useless.

Role mapping (demucs-style, mirroring slakh_eval):

* audio stems: filename contains "bass" -> bass; contains "drum"
  -> drums; everything else (minus click track) summed -> other
* MIDI truth: ``is_drum`` -> drums; instrument name contains "bass"
  -> bass; remaining instruments -> other

Drums are scored onset-only (detected classes vs GM numbers don't
align pitch-wise).

Usage::

    python3 -m scripts.samples_note_eval                # all songs
    python3 -m scripts.samples_note_eval --only jump_and_die
    python3 -m scripts.samples_note_eval --max-dur 60 --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.slakh_eval import (  # noqa: E402
    _decode_extracted_notes,
    _score_role,
)

SAMPLES_DIR = _BACKEND_ROOT.parent / "samples"
ROLES = ("bass", "drums", "other")


def _slugify(dirname: str) -> str:
    name = re.sub(r"^\d+\s*-\s*", "", dirname)
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()


def _stem_role(path: Path) -> Optional[str]:
    n = path.name.lower()
    if "clicktrack" in n:
        return None
    if "bass" in n:
        return "bass"
    if "drum" in n:
        return "drums"
    return "other"


def _gt_by_role(midi_path: Path, max_dur_s: float) -> Dict[str, List[dict]]:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(midi_path))
    out: Dict[str, List[dict]] = {r: [] for r in ROLES}
    # Stems are trimmed to the first sounding note across all tracks;
    # shift MIDI truth into audio time (see derive_chord_truth).
    offset = min(
        (i.notes[0].start for i in pm.instruments if i.notes), default=0.0
    )
    for inst in pm.instruments:
        name = inst.name.lower()
        if inst.is_drum or "drum" in name or "perc" in name:
            # pitched "Drums N" tracks render into the drum stems
            role = "drums"
        elif "bass" in name:
            role = "bass"
        else:
            role = "other"
        for n in inst.notes:
            start = n.start - offset
            end = n.end - offset
            if end <= 0 or (max_dur_s and start >= max_dur_s):
                continue
            out[role].append(
                {
                    "pitch": int(n.pitch),
                    "start": float(max(start, 0.0)),
                    "end": float(min(end, max_dur_s) if max_dur_s else end),
                }
            )
    for notes in out.values():
        notes.sort(key=lambda d: (d["start"], d["pitch"]))
    return out


def _sum_wavs(paths: List[Path], max_dur_s: float):
    import librosa

    sr_out = 44100
    mix = None
    for p in paths:
        y, _ = librosa.load(str(p), sr=sr_out, mono=True)
        if mix is None:
            mix = y.astype(np.float32)
        else:
            n = max(len(mix), len(y))
            m = np.zeros(n, dtype=np.float32)
            m[: len(mix)] += mix
            m[: len(y)] += y
            mix = m
    if max_dur_s:
        mix = mix[: int(max_dur_s * sr_out)]
    peak = float(np.abs(mix).max()) if len(mix) else 0.0
    if peak > 1.0:
        mix /= peak
    return mix, sr_out


def evaluate_song(song_dir: Path, max_dur_s: float, roles: tuple) -> Dict[str, dict]:
    import soundfile as sf

    from tone_forge.midi.gpu_extractor import extract_midi_hybrid

    mids = sorted(song_dir.glob("*.mid"))
    if not mids:
        return {}
    gt = _gt_by_role(mids[0], max_dur_s)

    stems: Dict[str, List[Path]] = {r: [] for r in ROLES}
    for p in sorted(song_dir.glob("*.wav")):
        role = _stem_role(p)
        if role:
            stems[role].append(p)

    out: Dict[str, dict] = {}
    for role in roles:
        if not stems[role] or not gt[role]:
            continue
        audio, sr = _sum_wavs(stems[role], max_dur_s)
        t0 = time.perf_counter()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
            sf.write(f.name, audio, sr)
            midi_result = extract_midi_hybrid(
                f.name, stem_type=role, preset_name=f"{song_dir.name}-{role}"
            )
        est_notes = _decode_extracted_notes(midi_result)
        out[role] = {
            "scores": _score_role(role, gt[role], est_notes),
            "method": midi_result.get("method"),
            "n_gt": len(gt[role]),
            "n_est": len(est_notes),
            "extract_s": round(time.perf_counter() - t0, 1),
        }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m scripts.samples_note_eval",
        description="Score production MIDI extraction vs composed-MIDI truth.",
    )
    ap.add_argument("--only", help="single song slug")
    ap.add_argument("--max-dur", type=float, default=0.0)
    ap.add_argument("--roles", default="bass,drums,other")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    rows: Dict[str, dict] = {}
    for song_dir in sorted(d for d in SAMPLES_DIR.iterdir() if d.is_dir()):
        slug = _slugify(song_dir.name)
        if args.only and slug != args.only:
            continue
        print(f"evaluating {slug}...", flush=True)
        rows[slug] = evaluate_song(song_dir, args.max_dur, roles)

    print(f"\n{'song':28s} {'role':6s} {'onsetF1':>8s} {'fullF1':>8s} "
          f"{'n_gt':>6s} {'n_est':>6s}  method")
    print("-" * 78)
    sums: Dict[str, List[float]] = {r: [] for r in roles}
    for slug, by_role in rows.items():
        for role, r in by_role.items():
            onset_f1 = r["scores"]["onset"]["f1"]
            full = r["scores"].get("full")
            full_f1 = full["f1"] if isinstance(full, dict) else float("nan")
            sums[role].append(onset_f1)
            print(f"{slug:28s} {role:6s} {onset_f1:8.4f} "
                  f"{full_f1:8.4f} {r['n_gt']:6d} {r['n_est']:6d}  {r['method']}")
    print("-" * 78)
    for role in roles:
        vals = sums[role]
        if vals:
            print(f"MEAN {role:6s} onsetF1 {sum(vals)/len(vals):.4f} "
                  f"({len(vals)} songs)")

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
