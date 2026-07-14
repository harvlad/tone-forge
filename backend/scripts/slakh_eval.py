"""Slakh note-level MIDI extraction eval (task 12).

Scores the PRODUCTION extraction path (``extract_midi_hybrid`` — the
exact function the local-engine worker calls per stem) against
Slakh/BabySlakh per-stem MIDI ground truth, using the strict
mir_eval-based metrics in ``bench.note_metrics``.

Oracle-stems mode (default): Slakh's clean rendered stems are grouped
into demucs-style roles (bass / drums / other) and each role group is
summed into one audio file, mirroring what demucs would hand the
extractor in the ideal separation case. This isolates transcription
accuracy from separation quality. ``--use-mix-demucs`` (future work)
would measure the full pipeline end-to-end.

Role grouping from each track's ``metadata.yaml``:

* ``is_drum: true``            -> drums   (onset-only scoring)
* ``inst_class == "Bass"``     -> bass    (pYIN+torchcrepe path)
* everything else              -> other   (CoreML polyphonic path)

Ground truth per role = union of the member stems' MIDI notes
(pretty_midi, absolute seconds). Extracted notes are decoded from the
base64 MIDI ``content`` every ``extract_midi_hybrid`` branch returns,
so bass-ensemble / lead-ensemble / CoreML / ONNX fallbacks are all
scored identically.

Usage::

    python3 -m scripts.slakh_eval --tracks 5
    python3 -m scripts.slakh_eval --tracks 20 --max-dur 60 --json out.json

License note: Slakh2100 / BabySlakh is CC-BY 4.0 (Manilow et al.,
"Cutting Music Source Separation Some Slakh", WASPAA 2019).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from bench.note_metrics import NotePRF, note_prf, onset_only_prf  # noqa: E402

DEFAULT_DATA_DIR = _BACKEND_ROOT / "bench" / "data" / "babyslakh_16k"

ROLES = ("bass", "drums", "other")


@dataclass
class RoleGroup:
    """One demucs-style role within a track."""

    role: str
    stem_ids: List[str] = field(default_factory=list)
    wav_paths: List[Path] = field(default_factory=list)
    midi_paths: List[Path] = field(default_factory=list)


def _group_roles(track_dir: Path) -> Dict[str, RoleGroup]:
    """Parse metadata.yaml and bucket stems into bass/drums/other.

    Only stems with BOTH a wav and a MIDI file on disk participate —
    ground truth and audio must cover the same instruments or the
    scores are meaningless.
    """
    import yaml

    meta = yaml.safe_load((track_dir / "metadata.yaml").read_text())
    groups = {r: RoleGroup(role=r) for r in ROLES}
    for stem_id, info in sorted((meta.get("stems") or {}).items()):
        wav = track_dir / "stems" / f"{stem_id}.wav"
        mid = track_dir / "MIDI" / f"{stem_id}.mid"
        if not (wav.exists() and mid.exists()):
            continue
        if info.get("is_drum"):
            role = "drums"
        elif info.get("inst_class") == "Bass":
            role = "bass"
        else:
            role = "other"
        g = groups[role]
        g.stem_ids.append(stem_id)
        g.wav_paths.append(wav)
        g.midi_paths.append(mid)
    return {r: g for r, g in groups.items() if g.stem_ids}


def _sum_wavs(paths: List[Path], max_dur_s: float) -> tuple[np.ndarray, int]:
    """Load and sum mono wavs at their native sample rate."""
    import soundfile as sf

    buffers: List[np.ndarray] = []
    sr_out = 0
    for p in paths:
        y, sr = sf.read(str(p), dtype="float32", always_2d=False)
        if y.ndim > 1:
            y = y.mean(axis=1)
        if max_dur_s > 0:
            y = y[: int(max_dur_s * sr)]
        buffers.append(y)
        sr_out = sr
    n = max(len(b) for b in buffers)
    out = np.zeros(n, dtype=np.float32)
    for b in buffers:
        out[: len(b)] += b
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    if peak > 1.0:
        out /= peak
    return out, sr_out


def _load_gt_notes(
    midi_paths: List[Path], max_dur_s: float
) -> List[dict]:
    """Union of note dicts across the group's ground-truth MIDIs."""
    import pretty_midi

    notes: List[dict] = []
    for p in midi_paths:
        pm = pretty_midi.PrettyMIDI(str(p))
        for inst in pm.instruments:
            for n in inst.notes:
                if max_dur_s > 0 and n.start >= max_dur_s:
                    continue
                notes.append(
                    {
                        "pitch": int(n.pitch),
                        "start": float(n.start),
                        "end": float(min(n.end, max_dur_s) if max_dur_s > 0
                                     else n.end),
                    }
                )
    notes.sort(key=lambda d: (d["start"], d["pitch"]))
    return notes


def _decode_extracted_notes(midi_result: dict) -> List[dict]:
    """Decode base64 MIDI ``content`` into note dicts.

    Every branch of ``extract_midi_hybrid`` returns its notes as a
    base64 standard MIDI file, so this is the one shape that scores
    all extractor paths identically.
    """
    import pretty_midi

    content = midi_result.get("content")
    if not content:
        return []
    pm = pretty_midi.PrettyMIDI(io.BytesIO(base64.b64decode(content)))
    notes = [
        {"pitch": int(n.pitch), "start": float(n.start),
         "end": float(n.end)}
        for inst in pm.instruments
        for n in inst.notes
    ]
    notes.sort(key=lambda d: (d["start"], d["pitch"]))
    return notes


def _score_role(
    role: str, gt_notes: List[dict], est_notes: List[dict]
) -> Dict[str, dict]:
    """Role-appropriate scoring: drums onset-only, pitched otherwise.

    Pitched roles are scored at global octave shifts {-12, 0, +12}
    applied to the estimate, keeping the best onset F1. Slakh renders
    some patches (e.g. scarbee Rickenbacker bass) one octave below
    the written MIDI note, so a fixed transposition between GT and
    audio is an instrument convention, not a transcription error —
    verified empirically: Track00001 bass GT note 50 sounds at
    73.6 Hz (D2, MIDI 38) in the rendered stem. The shift used is
    reported so real octave instability still surfaces.
    """
    if role == "drums":
        prf = onset_only_prf(
            [n["start"] for n in gt_notes],
            [n["start"] for n in est_notes],
        )
        return {"onset": prf.to_dict()}

    best: Optional[Dict[str, dict]] = None
    for shift in (0, -12, 12):
        shifted = [
            {**n, "pitch": n["pitch"] + shift} for n in est_notes
        ]
        onset = note_prf(gt_notes, shifted)
        cand = {
            "onset": onset.to_dict(),
            "full": note_prf(gt_notes, shifted, with_offset=True).to_dict(),
            "octave_shift": shift,
        }
        if best is None or onset.f1 > best["onset"]["f1"]:
            best = cand
    assert best is not None
    return best


def evaluate_track(
    track_dir: Path, max_dur_s: float, roles: tuple
) -> Dict[str, dict]:
    """Run extraction + scoring for one track. Returns role -> scores."""
    import soundfile as sf

    from tone_forge.midi.gpu_extractor import extract_midi_hybrid

    groups = _group_roles(track_dir)
    out: Dict[str, dict] = {}
    for role in roles:
        g = groups.get(role)
        if g is None:
            continue
        gt_notes = _load_gt_notes(g.midi_paths, max_dur_s)
        if not gt_notes:
            continue
        audio, sr = _sum_wavs(g.wav_paths, max_dur_s)
        t0 = time.perf_counter()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
            sf.write(f.name, audio, sr)
            midi_result = extract_midi_hybrid(
                f.name, stem_type=role, preset_name=f"{track_dir.name}-{role}"
            )
        est_notes = _decode_extracted_notes(midi_result)
        elapsed = time.perf_counter() - t0
        out[role] = {
            "scores": _score_role(role, gt_notes, est_notes),
            "method": midi_result.get("method"),
            "n_gt": len(gt_notes),
            "n_est": len(est_notes),
            "stems": g.stem_ids,
            "extract_s": round(elapsed, 1),
        }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m scripts.slakh_eval",
        description="Score production MIDI extraction vs Slakh ground truth.",
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--tracks", type=int, default=5,
                        help="Number of tracks (sorted) to evaluate.")
    parser.add_argument("--start", type=int, default=0,
                        help="Skip this many tracks first.")
    parser.add_argument("--max-dur", type=float, default=0.0,
                        help="Trim audio+GT to first N seconds (0=full).")
    parser.add_argument("--roles", default="bass,drums,other",
                        help="Comma-separated roles to score.")
    parser.add_argument("--json", type=Path, default=None,
                        help="Write full results JSON here.")
    args = parser.parse_args(argv)

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    track_dirs = sorted(
        d for d in args.data.iterdir()
        if d.is_dir() and d.name.startswith("Track")
    )[args.start: args.start + args.tracks]
    if not track_dirs:
        sys.stderr.write(f"no Track* dirs under {args.data}\n")
        return 2

    results: Dict[str, dict] = {}
    for td in track_dirs:
        sys.stderr.write(f"[slakh-eval] {td.name}...\n")
        try:
            results[td.name] = evaluate_track(td, args.max_dur, roles)
        except Exception as exc:  # keep going; report per-track failure
            sys.stderr.write(f"[slakh-eval] {td.name} FAILED: {exc}\n")
            results[td.name] = {"error": str(exc)}

    # Aggregate: mean F1 per role across tracks.
    agg: Dict[str, List[float]] = {}
    print(f"\n{'track':<14}{'role':<8}{'onset F1':>9}{'full F1':>9}"
          f"{'oct':>5}{'n_gt':>7}{'n_est':>7}  method")
    for tname, troles in results.items():
        if "error" in troles:
            print(f"{tname:<14}ERROR: {troles['error']}")
            continue
        for role, r in troles.items():
            onset_f1 = r["scores"]["onset"]["f1"]
            full_f1 = r["scores"].get("full", {}).get("f1")
            oct_shift = r["scores"].get("octave_shift")
            agg.setdefault(f"{role}/onset", []).append(onset_f1)
            if full_f1 is not None:
                agg.setdefault(f"{role}/full", []).append(full_f1)
            full_str = f"{full_f1:>9.3f}" if full_f1 is not None else "        -"
            oct_str = f"{oct_shift:>5d}" if oct_shift is not None else "    -"
            print(f"{tname:<14}{role:<8}{onset_f1:>9.3f}{full_str}{oct_str}"
                  f"{r['n_gt']:>7}{r['n_est']:>7}  {r.get('method')}")

    print("\nmeans:")
    summary = {}
    for key in sorted(agg):
        mean = float(np.mean(agg[key]))
        summary[key] = round(mean, 4)
        print(f"  {key:<14} {mean:.4f}  (n={len(agg[key])})")

    if args.json:
        args.json.write_text(json.dumps(
            {"tracks": results, "summary": summary,
             "max_dur_s": args.max_dur},
            indent=2,
        ))
        sys.stderr.write(f"[slakh-eval] wrote {args.json}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
