#!/usr/bin/env python3
"""Build the specialist listening-check pack (registry 2026.08.30-1).

The separated Slakh revalidation promoted riley_bass(+12) and riley_guitar
for the experimental engine; the open question the numbers cannot answer
is how each transcription SOUNDS on real (non-Slakh) audio — above all
whether the +12 bass register rule holds outside Slakh's written-pitch
convention. This script builds a blind A/B/C pack from the real multitrack
songs in samples/:

    for each clip (highest-energy window of the target stem):
        mix all stems  -> htdemucs_6s separation -> target separated stem
        A/B/C (shuffled per clip, seeded):
            incumbent   extract_midi_hybrid on the separated stem
            specialist  routed engine (bass gets +12 via register_up_12)
            spec_raw    bass only: same specialist notes WITHOUT the +12
        each rendered to WAV with the same numpy synth, next to
        ref_separated.wav and ref_clean.wav for grounding.

Blind protocol: listen to X1/X2/X3 against the refs, note the better one
per clip in VERDICTS.md, and only then open ANSWER_KEY.json.

Usage (from backend/):
    python3 scripts/make_specialist_listening_pack.py \
        [--samples-dir ../samples] [--out lab_data/listening_pack_v1] \
        [--bass-clips 6] [--clip-seconds 30] [--seed 20260830]
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SR = 44100
CLICK_MARKER = "clicktrack"


# -- audio helpers ---------------------------------------------------------

def _load_mono(path: Path, sr: int = SR) -> np.ndarray:
    data, file_sr = sf.read(str(path), always_2d=True)
    mono = data.mean(axis=1)
    if file_sr != sr:
        n_out = int(len(mono) * sr / file_sr)
        mono = np.interp(np.linspace(0, len(mono) - 1, n_out),
                         np.arange(len(mono)), mono)
    return mono.astype(np.float32)


def _best_window(stem: np.ndarray, clip_s: float) -> int:
    """Start sample of the highest-RMS window of the target stem."""
    n = int(clip_s * SR)
    if len(stem) <= n:
        return 0
    hop = SR  # 1 s granularity
    best_i, best_e = 0, -1.0
    for i in range(0, len(stem) - n, hop):
        e = float(np.sqrt(np.mean(stem[i:i + n] ** 2)))
        if e > best_e:
            best_i, best_e = i, e
    return best_i


def _mix_song(song_dir: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Sum all non-click stems; returns (mix, {stem_filename_stem: audio})."""
    stems = {}
    length = 0
    for wav in sorted(song_dir.glob("*.wav")):
        if CLICK_MARKER in wav.name.lower():
            continue
        stems[wav.stem] = _load_mono(wav)
        length = max(length, len(stems[wav.stem]))
    mix = np.zeros(length, dtype=np.float32)
    for a in stems.values():
        mix[:len(a)] += a
    peak = np.abs(mix).max()
    if peak > 0:
        mix *= 0.89 / peak
    return mix, stems


# -- MIDI helpers ----------------------------------------------------------

def _decode_midi_b64(content_b64: str) -> list[tuple]:
    import mido
    mid = mido.MidiFile(file=io.BytesIO(base64.b64decode(content_b64)))
    tempo = 500000
    for tr in mid.tracks:
        for m in tr:
            if m.type == "set_tempo":
                tempo = m.tempo
                break
    notes = []
    for tr in mid.tracks:
        t, active = 0.0, {}
        for m in tr:
            t += mido.tick2second(m.time, mid.ticks_per_beat, tempo)
            if m.type == "note_on" and m.velocity > 0:
                active[m.note] = (t, m.velocity)
            elif m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
                if m.note in active:
                    s, v = active.pop(m.note)
                    notes.append((s, t, m.note, v))
    return notes


def _render_notes(notes: list[tuple], total_s: float) -> np.ndarray:
    """Same plucked-triangle synth as audition_artifacts.py."""
    buf = np.zeros(int((total_s + 1.0) * SR))
    for onset, offset, pitch, vel in notes:
        dur = max(0.05, offset - onset)
        n = int(dur * SR)
        t = np.arange(n) / SR
        f = 440.0 * 2 ** ((pitch - 69) / 12)
        w = (np.sin(2 * np.pi * f * t)
             + 0.35 * np.sin(2 * np.pi * 2 * f * t)
             + 0.15 * np.sin(2 * np.pi * 3 * f * t))
        env = np.exp(-3.0 * t / max(dur, 0.15))
        i0 = int(onset * SR)
        seg = w * env * (vel / 127.0) * 0.25
        if i0 < len(buf):
            buf[i0:i0 + n] += seg[:max(0, len(buf) - i0)]
    peak = np.abs(buf).max()
    return (buf / peak * 0.85 if peak > 0 else buf).astype(np.float32)


# -- transcription paths ---------------------------------------------------

def _incumbent_notes(stem_path: Path, family: str) -> list[tuple]:
    from tone_forge.midi.gpu_extractor import extract_midi_hybrid
    stem_type = "bass" if family == "bass" else "other"
    result = extract_midi_hybrid(str(stem_path), stem_type=stem_type,
                                 preset_name=f"incumbent_{family}")
    return _decode_midi_b64(result["content"])


def _specialist_notes(stem_path: Path, family: str) -> tuple[list[tuple], dict]:
    from tone_forge.specialist.router import RoutingRequest, resolve
    from tone_forge.specialist.runner import transcribe_stem, warm_imports
    decision = resolve(RoutingRequest(engine="experimental_specialist",
                                      family=family))
    if decision is None:
        raise SystemExit(f"no specialist route for family {family!r}")
    warm_imports(decision)
    midi_result, provenance = transcribe_stem(decision, str(stem_path))
    return _decode_midi_b64(midi_result["content"]), provenance


# -- main ------------------------------------------------------------------

def _separate_and_blind(mix_clip: np.ndarray, clean_clip: np.ndarray,
                        family: str, clip_dir: Path, clip_s: float,
                        rng: random.Random) -> dict | None:
    """Shared tail: separate mix, transcribe variants, render blind X files."""
    clip_dir.mkdir(parents=True, exist_ok=True)
    from tone_forge.stem_separator import separate_all_stems
    with tempfile.TemporaryDirectory() as td:
        mix_path = Path(td) / "mix.wav"
        sf.write(str(mix_path), mix_clip, SR)
        sep = separate_all_stems(mix_path, output_dir=td,
                                 model_name="htdemucs_6s")
        sep_key = "bass" if family == "bass" else "guitar"
        if sep_key not in sep:
            print(f"  !! separator returned no '{sep_key}' stem; skipping")
            shutil.rmtree(clip_dir, ignore_errors=True)
            return None
        sep_stem = clip_dir / "ref_separated.wav"
        shutil.copy(sep[sep_key], sep_stem)
        sf.write(str(clip_dir / "ref_clean.wav"), clean_clip, SR)

        print(f"  transcribing (incumbent + specialist) ...", flush=True)
        variants: dict[str, list[tuple]] = {}
        variants["incumbent"] = _incumbent_notes(sep_stem, family)
        spec_notes, provenance = _specialist_notes(sep_stem, family)
        variants["specialist"] = spec_notes
        if family == "bass":
            # the +12 question, isolated: identical notes minus the shift
            variants["spec_raw"] = [(s, e, p - 12, v) for s, e, p, v in spec_notes]

    labels = [f"X{i + 1}" for i in range(len(variants))]
    order = list(variants)
    rng.shuffle(order)
    key = {}
    for label, variant in zip(labels, order):
        sf.write(str(clip_dir / f"{label}.wav"),
                 _render_notes(variants[variant], clip_s), SR)
        key[label] = variant
    print(f"  done: {clip_dir.name} ({', '.join(labels)})", flush=True)
    return {
        "clip": clip_dir.name,
        "family": family,
        "clip_seconds": clip_s,
        "note_counts": {v: len(nts) for v, nts in variants.items()},
        "specialist_provenance": provenance,
        "_answer_key": key,
    }


def build_prepared_clip(clip_src: Path, out_dir: Path, clip_s: float,
                        rng: random.Random) -> dict | None:
    """Clip dir prepared elsewhere (e.g. Hetzner): <name>__<family>/ with
    mix.wav + ref_clean.wav already windowed."""
    family = clip_src.name.rsplit("__", 1)[-1]
    if family not in ("bass", "guitar"):
        print(f"  !! cannot parse family from {clip_src.name}; skipping")
        return None
    print(f"[{clip_src.name}] separating ...", flush=True)
    mix_clip = _load_mono(clip_src / "mix.wav")
    clean_clip = _load_mono(clip_src / "ref_clean.wav")
    info = _separate_and_blind(mix_clip, clean_clip, family,
                               out_dir / clip_src.name, clip_s, rng)
    if info:
        info["source"] = "prepared:" + str(clip_src)
    return info


def build_clip(song_dir: Path, family: str, out_dir: Path, clip_s: float,
               rng: random.Random) -> dict | None:
    marker = "bass" if family == "bass" else "guitar"
    targets = [w for w in song_dir.glob("*.wav")
               if marker in w.name.lower() and CLICK_MARKER not in w.name.lower()]
    if not targets:
        return None
    print(f"[{song_dir.name}] {family}: mixing + separating ...", flush=True)
    mix, stems = _mix_song(song_dir)
    clean = _load_mono(targets[0])
    start = _best_window(clean, clip_s)
    n = int(clip_s * SR)
    mix_clip = mix[start:start + n]
    clean_clip = clean[start:start + n]

    clip_dir = out_dir / f"{song_dir.name.replace(' ', '_')}__{family}"
    info = _separate_and_blind(mix_clip, clean_clip, family, clip_dir,
                               clip_s, rng)
    if info:
        info["song"] = song_dir.name
        info["window_start_s"] = round(start / SR, 1)
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples-dir", default="../samples")
    ap.add_argument("--prepared-dir", default=None,
                    help="dir of pre-windowed clip dirs (<name>__<family>/ "
                         "with mix.wav + ref_clean.wav); overrides "
                         "--samples-dir selection")
    ap.add_argument("--out", default="lab_data/listening_pack_v1")
    ap.add_argument("--bass-clips", type=int, default=6)
    ap.add_argument("--clip-seconds", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=20260830)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    clips = []
    if args.prepared_dir:
        srcs = sorted(d for d in Path(args.prepared_dir).iterdir()
                      if d.is_dir() and (d / "mix.wav").exists())
        print(f"{len(srcs)} prepared clips")
        for src in srcs:
            info = build_prepared_clip(src, out_dir, args.clip_seconds, rng)
            if info:
                clips.append(info)
    else:
        samples = Path(args.samples_dir)
        bass_songs = sorted(d for d in samples.iterdir() if d.is_dir()
                            and any("bass" in w.name.lower()
                                    for w in d.glob("*.wav")))
        guitar_songs = sorted(d for d in samples.iterdir() if d.is_dir()
                              and any("guitar" in w.name.lower()
                                      for w in d.glob("*.wav")))
        picked_bass = rng.sample(bass_songs,
                                 min(args.bass_clips, len(bass_songs)))
        jobs = ([(d, "bass") for d in picked_bass]
                + [(d, "guitar") for d in guitar_songs])
        print(f"{len(jobs)} clips: {len(picked_bass)} bass, "
              f"{len(guitar_songs)} guitar")
        for song_dir, family in jobs:
            info = build_clip(song_dir, family, out_dir, args.clip_seconds, rng)
            if info:
                clips.append(info)

    from tone_forge.specialist import registry as reg
    answer_key = {c["clip"]: c.pop("_answer_key") for c in clips}
    manifest = {
        "purpose": "real-audio listening check for registry promotion "
                   "2026.08.30-1 (bass register_up_12 caveat + guitar route)",
        "protocol": "Per clip: listen to ref_clean.wav and ref_separated.wav, "
                    "then X*.wav. Note the best variant per clip in "
                    "VERDICTS.md BEFORE opening ANSWER_KEY.json.",
        "registry_version": reg.registry_version(),
        "seed": args.seed,
        "clips": clips,
    }
    (out_dir / "pack.json").write_text(json.dumps(manifest, indent=1))
    (out_dir / "ANSWER_KEY.json").write_text(json.dumps(answer_key, indent=1))
    (out_dir / "VERDICTS.md").write_text(
        "# Listening verdicts (fill in BEFORE opening ANSWER_KEY.json)\n\n"
        + "\n".join(f"- {c['clip']}: X? best because ..." for c in clips)
        + "\n")
    print(f"\npack written to {out_dir} — {len(clips)} clips. "
          f"Fill VERDICTS.md before opening ANSWER_KEY.json.")


if __name__ == "__main__":
    main()
