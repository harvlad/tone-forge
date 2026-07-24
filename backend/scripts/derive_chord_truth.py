"""Derive chord ground truth from composed multitrack MIDI.

The samples/ corpus ships the *composed* MIDI for every first-party
song — the actual notes the songs were rendered from. That is real
harmonic ground truth, unlike the retired ``baseline-captured``
fixtures whose "truth" was a snapshot of the detector's own output
(which made every honest engine improvement score as a regression).

Method (deterministic, no audio analysis):

1. Load the song's .mid (pretty_midi). Drums excluded. The bass
   instrument (name contains "bass", else lowest mean pitch) provides
   the root prior.
2. Build a half-bar window grid from the MIDI's own downbeats.
3. Per window: duration-weighted pitch-class histogram of all
   sounding harmonic notes; duration-weighted bass pitch class.
4. Template-match the histogram over 12 roots x vocabulary
   (maj/min/5/sus2/sus4/7/maj7/m7/dim/aug), scoring in-template mass
   minus out-of-template mass, with a bonus when the root matches the
   bass. Windows with < 2 distinct pitch classes or a losing best
   score emit no chord (region gap — WCSR scores the gap as wrong for
   any prediction, right for none).
5. Merge adjacent windows with equal labels into regions.

Also renders the corpus audio layout expected by ``bench.corpus``:

    data/chord_groundtruth_audio/<slug>/other.wav   (sum of non-bass,
                                                     non-click stems)
    data/chord_groundtruth_audio/<slug>/bass.wav    (bass stem)

and writes ``tests/fixtures/chord_groundtruth/<slug>.json`` with
``tags: ["midi-derived"]``.

Usage:
    python3 -m scripts.derive_chord_truth              # all songs
    python3 -m scripts.derive_chord_truth --only jump_and_die
    python3 -m scripts.derive_chord_truth --dry-run    # print regions
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

SAMPLES_DIR = _BACKEND_DIR.parent / "samples"
FIXTURES_DIR = _BACKEND_DIR / "tests" / "fixtures" / "chord_groundtruth"
AUDIO_DIR = _BACKEND_DIR / "data" / "chord_groundtruth_audio"

_ROOTS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Chord vocabulary: intervals from root. Matches what
# tone_forge.analysis.chord_eval.normalise_symbol parses.
_TEMPLATES: Dict[str, Tuple[int, ...]] = {
    "": (0, 4, 7),          # maj
    "m": (0, 3, 7),         # min
    "5": (0, 7),            # power chord
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10),
    "dim": (0, 3, 6),
    "aug": (0, 4, 8),
}

# Dyads/triads are complete chords; extra chord tones beyond the
# template cost less than non-chord tones. Small template-size prior
# keeps "5" from swallowing full triads (a triad's third is positive
# evidence for maj/min that "5" leaves on the table).
_OUT_OF_TEMPLATE_PENALTY = 1.0
_BASS_ROOT_BONUS = 0.08
_MIN_WINDOW_MASS = 1e-6
_MIN_SCORE = 0.55  # of window mass covered net of penalty; below -> no chord


def _slugify(dirname: str) -> str:
    """'02 - Jump and Die' -> 'jump_and_die'."""
    name = re.sub(r"^\d+\s*-\s*", "", dirname)
    name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return name


def _find_midi(song_dir: Path) -> Optional[Path]:
    mids = sorted(song_dir.glob("*.mid"))
    return mids[0] if mids else None


def _harmonic_and_bass_instruments(pm) -> Tuple[list, Optional[object]]:
    # Pitched tracks named "drums"/"perc" (tuned percussion) render into the
    # drum stems, not the harmonic audio -- exclude them from chord truth.
    harmonic = [
        i
        for i in pm.instruments
        if not i.is_drum
        and i.notes
        and not any(k in i.name.lower() for k in ("drum", "perc"))
    ]
    if not harmonic:
        return [], None
    named_bass = [i for i in harmonic if "bass" in i.name.lower()]
    if named_bass:
        return harmonic, named_bass[0]
    lowest = min(
        harmonic,
        key=lambda i: sum(n.pitch for n in i.notes) / len(i.notes),
    )
    return harmonic, lowest


def _window_grid(pm, end_time: float) -> List[Tuple[float, float]]:
    """Half-bar windows from MIDI downbeats; fallback to 1s windows."""
    downbeats = list(pm.get_downbeats())
    if len(downbeats) >= 2:
        edges: List[float] = []
        for a, b in zip(downbeats, downbeats[1:]):
            edges.append(a)
            edges.append(a + (b - a) / 2.0)
        # Extend the last bar pattern to song end.
        bar = downbeats[-1] - downbeats[-2]
        t = downbeats[-1]
        while t < end_time:
            edges.append(t)
            edges.append(min(t + bar / 2.0, end_time))
            t += bar
        edges = sorted(set(e for e in edges if e < end_time))
        edges.append(end_time)
        return [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 1e-3]
    step = 1.0
    edges = [i * step for i in range(int(end_time / step) + 1)] + [end_time]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 1e-3]


def _pc_histograms(
    harmonic, bass, windows: List[Tuple[float, float]]
) -> Tuple[List[List[float]], List[Optional[int]]]:
    """Duration-weighted pitch-class mass + bass pc per window."""
    n = len(windows)
    hists = [[0.0] * 12 for _ in range(n)]
    bass_mass = [[0.0] * 12 for _ in range(n)]

    starts = [w[0] for w in windows]

    def _accumulate(notes, target):
        import bisect

        for note in notes:
            lo = bisect.bisect_right(starts, note.start) - 1
            lo = max(lo, 0)
            for wi in range(lo, n):
                ws, we = windows[wi]
                if ws >= note.end:
                    break
                overlap = min(we, note.end) - max(ws, note.start)
                if overlap > 0:
                    target[wi][note.pitch % 12] += overlap

    for inst in harmonic:
        _accumulate(inst.notes, hists)
    if bass is not None:
        _accumulate(bass.notes, bass_mass)

    bass_pc: List[Optional[int]] = []
    for wi in range(n):
        m = bass_mass[wi]
        total = sum(m)
        bass_pc.append(max(range(12), key=lambda p: m[p]) if total > _MIN_WINDOW_MASS else None)
    return hists, bass_pc


def _label_window(hist: List[float], bass_pc: Optional[int]) -> Optional[str]:
    total = sum(hist)
    if total < _MIN_WINDOW_MASS:
        return None
    active = [p for p in range(12) if hist[p] > 0.05 * total]
    if len(active) < 2:
        return None

    best_label: Optional[str] = None
    best_score = -1.0
    for root in range(12):
        for suffix, intervals in _TEMPLATES.items():
            pcs = {(root + iv) % 12 for iv in intervals}
            in_mass = sum(hist[p] for p in pcs)
            out_mass = total - in_mass
            score = (in_mass - _OUT_OF_TEMPLATE_PENALTY * out_mass) / total
            # Root evidence: the root pitch class should actually sound.
            if hist[root] < 0.05 * total:
                continue
            if bass_pc is not None and root == bass_pc:
                score += _BASS_ROOT_BONUS
            # Prefer richer templates at equal coverage (break "5" ties
            # toward the triad that explains the third).
            score += 0.001 * len(intervals)
            if score > best_score:
                best_score = score
                best_label = f"{_ROOTS[root]}{suffix}"
    if best_score < _MIN_SCORE:
        return None
    return best_label


def derive_regions(midi_path: Path) -> Tuple[List[Dict[str, object]], float]:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(midi_path))
    end_time = float(pm.get_end_time())
    harmonic, bass = _harmonic_and_bass_instruments(pm)
    if not harmonic:
        return [], end_time

    # Rendered stems are trimmed to the first sounding note across ALL
    # tracks (verified: audio duration == midi_end - min_first_note), so
    # MIDI-time truth must be shifted into audio time.
    offset = min(
        (i.notes[0].start for i in pm.instruments if i.notes), default=0.0
    )

    windows = _window_grid(pm, end_time)
    hists, bass_pcs = _pc_histograms(harmonic, bass, windows)

    regions: List[Dict[str, object]] = []
    for (ws, we), hist, bpc in zip(windows, hists, bass_pcs):
        label = _label_window(hist, bpc)
        if label is None:
            continue
        ws, we = ws - offset, we - offset
        if we <= 0:
            continue
        ws = max(ws, 0.0)
        if regions and regions[-1]["label"] == label and abs(float(regions[-1]["end"]) - ws) < 1e-3:
            regions[-1]["end"] = round(we, 3)
        else:
            regions.append({"start": round(ws, 3), "end": round(we, 3), "label": label})
    return regions, end_time - offset


def render_audio(song_dir: Path, slug: str) -> Tuple[Optional[str], Optional[str], float]:
    """Sum stems -> other.wav (non-bass) + bass.wav. Returns rel paths + duration."""
    import numpy as np
    import soundfile as sf
    import librosa

    sr_out = 22050
    stems = [p for p in sorted(song_dir.glob("*.wav")) if "clicktrack" not in p.name.lower()]
    bass_stems = [p for p in stems if "bass" in p.name.lower()]
    # Production chords run on the demucs "other" stem: no bass, no drums.
    other_stems = [
        p
        for p in stems
        if p not in bass_stems
        and not any(k in p.name.lower() for k in ("drum", "perc"))
    ]
    if not other_stems:
        return None, None, 0.0

    def _mix(paths):
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
        peak = float(np.abs(mix).max()) if mix is not None and len(mix) else 0.0
        if peak > 1.0:
            mix /= peak
        return mix

    out_dir = AUDIO_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    other_path = out_dir / "other.wav"
    bass_path = out_dir / "bass.wav"
    if other_path.exists():
        info = sf.info(str(other_path))
        bass_rel = (
            f"data/chord_groundtruth_audio/{slug}/bass.wav"
            if bass_path.exists()
            else None
        )
        return (
            f"data/chord_groundtruth_audio/{slug}/other.wav",
            bass_rel,
            info.frames / info.samplerate,
        )

    other = _mix(other_stems)
    sf.write(str(out_dir / "other.wav"), other, sr_out, subtype="PCM_16")
    duration = len(other) / sr_out

    bass_rel = None
    if bass_stems:
        bass = _mix(bass_stems)
        sf.write(str(out_dir / "bass.wav"), bass, sr_out, subtype="PCM_16")
        bass_rel = f"data/chord_groundtruth_audio/{slug}/bass.wav"

    return f"data/chord_groundtruth_audio/{slug}/other.wav", bass_rel, duration


def build_fixture(song_dir: Path, dry_run: bool = False) -> Optional[str]:
    slug = _slugify(song_dir.name)
    midi_path = _find_midi(song_dir)
    if midi_path is None:
        print(f"  {slug}: no .mid, skipped")
        return None

    regions, midi_end = derive_regions(midi_path)
    if not regions:
        print(f"  {slug}: no chord regions derivable, skipped")
        return None

    covered = sum(float(r["end"]) - float(r["start"]) for r in regions)
    print(
        f"  {slug}: {len(regions)} regions, "
        f"coverage {covered / midi_end:.0%} of {midi_end:.0f}s"
    )
    if dry_run:
        for r in regions[:14]:
            print(f"    {r['start']:7.2f} {r['end']:7.2f}  {r['label']}")
        if len(regions) > 14:
            print(f"    ... {len(regions) - 14} more")
        return slug

    other_rel, bass_rel, duration = render_audio(song_dir, slug)
    if other_rel is None:
        print(f"  {slug}: no stems to render, skipped")
        return None

    bpm_match = re.search(r"(\d+)bpm", midi_path.name)
    fixture = {
        "song": re.sub(r"^\d+\s*-\s*", "", song_dir.name),
        "artist": "(local sample)",
        "source": f"midi-derived from {midi_path.name} (composed multitrack MIDI)",
        "confidence": (
            "MIDI-DERIVED: regions computed deterministically from the "
            "composed MIDI note data (scripts.derive_chord_truth), not "
            "from any audio detector. Windows with ambiguous harmony "
            "are uncovered gaps."
        ),
        "schema_version": 2,
        "split": "test",
        "genre": "synth",
        "license": "first-party",
        "tags": ["midi-derived"],
        "curated_by": "scripts.derive_chord_truth",
        "tempo_bpm": int(bpm_match.group(1)) if bpm_match else None,
        "duration_s": round(min(duration, midi_end) if duration else midi_end, 3),
        "source_audio_other_stem": other_rel,
        "source_audio_bass_stem": bass_rel,
        "regression_floor_triad_relaxed": 0.0,
        "regions": regions,
    }
    fixture = {k: v for k, v in fixture.items() if v is not None}

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FIXTURES_DIR / f"{slug}.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(fixture, fh, indent=2)
        fh.write("\n")
    return slug


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="single song slug (e.g. jump_and_die)")
    ap.add_argument("--dry-run", action="store_true", help="print regions, write nothing")
    args = ap.parse_args()

    if not SAMPLES_DIR.is_dir():
        print(f"samples dir missing: {SAMPLES_DIR}", file=sys.stderr)
        return 1

    song_dirs = [d for d in sorted(SAMPLES_DIR.iterdir()) if d.is_dir()]
    written = []
    for song_dir in song_dirs:
        slug = _slugify(song_dir.name)
        if args.only and slug != args.only:
            continue
        result = build_fixture(song_dir, dry_run=args.dry_run)
        if result:
            written.append(result)
    print(f"{'derived' if args.dry_run else 'wrote'} {len(written)} fixtures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
