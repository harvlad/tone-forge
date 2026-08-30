#!/usr/bin/env python3
"""Basic Pitch Internal Activation Analysis.

Investigates whether Basic Pitch's internal representations contain
correct octave information that's lost in decoding.

Key questions:
1. Is the correct octave already present in internal activations?
2. Are octave failures representation or decoding failures?
3. Can internal activations predict octave errors?
4. What's the internal-activation oracle?
5. Can alternative decoding recover correct octaves?
"""
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import json
import argparse
import numpy as np
from collections import defaultdict
from dataclasses import dataclass, asdict
import random

# Basic Pitch
from basic_pitch.inference import predict
from basic_pitch import ICASSP_2022_MODEL_PATH

# Local imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Constants
MIDI_OFFSET = 21  # Basic Pitch uses 88 keys starting at MIDI 21
FRAME_RATE = 43.066  # Approximate frames per second for Basic Pitch


@dataclass
class NoteActivationData:
    """Internal activation data for a single note."""
    # Identifiers
    stem: str = ""
    inst_class: str = ""

    # Pitch info
    predicted_pitch: int = 0
    gt_pitch: int = 0
    octave_delta: int = 0
    is_octave_error: bool = False

    # Timing
    onset: float = 0.0
    offset: float = 0.0
    duration: float = 0.0

    # Activations at octave candidates (P-24, P-12, P, P+12, P+24)
    # Using predicted pitch as reference
    act_p_minus_24: float = 0.0
    act_p_minus_12: float = 0.0
    act_p: float = 0.0
    act_p_plus_12: float = 0.0
    act_p_plus_24: float = 0.0

    # Activations at GT pitch octave candidates
    act_gt_minus_12: float = 0.0
    act_gt: float = 0.0
    act_gt_plus_12: float = 0.0

    # Onset activations
    onset_act_p: float = 0.0
    onset_act_gt: float = 0.0

    # Detailed features
    mean_act_p: float = 0.0
    max_act_p: float = 0.0
    mean_act_gt: float = 0.0
    max_act_gt: float = 0.0

    # Persistence (proportion of frames above threshold)
    persist_p: float = 0.0
    persist_gt: float = 0.0

    # Activation ratios
    ratio_gt_to_p: float = 0.0  # activation(GT) / activation(P)
    ratio_p12_to_p: float = 0.0  # activation(P+12) / activation(P)
    ratio_pm12_to_p: float = 0.0  # activation(P-12) / activation(P)

    # Margins
    margin_gt_vs_p: float = 0.0  # GT activation - P activation
    octave_margin: float = 0.0   # Best octave - second best

    # Rankings
    gt_rank: int = 0  # Rank of GT among octave candidates (1=highest)
    gt_within_5pct: bool = False
    gt_within_10pct: bool = False
    gt_within_20pct: bool = False

    # Error classification
    error_type: str = ""  # "representation", "decoding", "ambiguous"


def load_midi_notes(midi_path: Path) -> List[Tuple[int, float, float]]:
    """Load GT notes from MIDI file."""
    import mido
    try:
        mid = mido.MidiFile(str(midi_path))
    except Exception:
        return []

    tempo = 500000
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    notes = []
    for track in mid.tracks:
        current_time = 0
        active_notes = {}

        for msg in track:
            current_time += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = current_time
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in active_notes:
                    start = active_notes.pop(msg.note)
                    notes.append((msg.note, start, current_time))

    return notes


def match_notes_with_octave(
    gt_notes: List[Tuple[int, float, float]],
    ext_notes: List[Tuple[float, float, int, float, any]],  # Basic Pitch format
    onset_tol: float = 0.35,
) -> List[Dict]:
    """Match notes tracking octave errors."""
    # Convert ext_notes to (pitch, onset, offset)
    ext_converted = [(n[2], n[0], n[1]) for n in ext_notes]

    candidates = []
    for i, (ep, eo, ee) in enumerate(ext_converted):
        for j, (gp, go, ge) in enumerate(gt_notes):
            if abs(eo - go) > onset_tol:
                continue

            same_pc = (ep % 12) == (gp % 12)
            exact = ep == gp

            if exact or same_pc:
                octave_delta = (ep - gp) // 12
                candidates.append({
                    'gt_idx': j,
                    'ext_idx': i,
                    'exact': exact,
                    'octave_delta': octave_delta,
                    'onset_diff': abs(eo - go),
                })

    candidates.sort(key=lambda x: (not x['exact'], x['onset_diff']))

    gt_matched = set()
    ext_matched = set()
    matches = []

    for c in candidates:
        if c['gt_idx'] in gt_matched or c['ext_idx'] in ext_matched:
            continue
        gt_matched.add(c['gt_idx'])
        ext_matched.add(c['ext_idx'])
        matches.append(c)

    return matches


def extract_activation_features(
    note_activations: np.ndarray,
    onset_activations: np.ndarray,
    predicted_pitch: int,
    gt_pitch: int,
    onset_time: float,
    offset_time: float,
    frame_rate: float = FRAME_RATE,
    threshold: float = 0.3,
) -> Dict:
    """Extract activation features for a note."""
    # Convert times to frames
    start_frame = max(0, int(onset_time * frame_rate))
    end_frame = min(len(note_activations), int(offset_time * frame_rate) + 1)

    if end_frame <= start_frame:
        end_frame = start_frame + 1

    # Extract note region
    note_region = note_activations[start_frame:end_frame]
    onset_region = onset_activations[start_frame:min(start_frame + 5, len(onset_activations))]

    def get_pitch_activation(pitch: int, region: np.ndarray) -> float:
        """Get mean activation for a pitch."""
        idx = pitch - MIDI_OFFSET
        if 0 <= idx < region.shape[1]:
            return float(np.mean(region[:, idx]))
        return 0.0

    def get_max_activation(pitch: int, region: np.ndarray) -> float:
        """Get max activation for a pitch."""
        idx = pitch - MIDI_OFFSET
        if 0 <= idx < region.shape[1]:
            return float(np.max(region[:, idx]))
        return 0.0

    def get_persistence(pitch: int, region: np.ndarray, thresh: float) -> float:
        """Get proportion of frames above threshold."""
        idx = pitch - MIDI_OFFSET
        if 0 <= idx < region.shape[1]:
            return float(np.mean(region[:, idx] > thresh))
        return 0.0

    def get_onset_activation(pitch: int, region: np.ndarray) -> float:
        """Get onset activation."""
        idx = pitch - MIDI_OFFSET
        if 0 <= idx < region.shape[1] and len(region) > 0:
            return float(np.max(region[:, idx]))
        return 0.0

    # Octave candidates relative to predicted pitch
    p = predicted_pitch
    candidates = [p - 24, p - 12, p, p + 12, p + 24]
    candidate_acts = [get_pitch_activation(c, note_region) for c in candidates]

    # GT octave candidates
    g = gt_pitch
    gt_candidates = [g - 12, g, g + 12]
    gt_acts = [get_pitch_activation(c, note_region) for c in gt_candidates]

    # Main activations
    act_p = get_pitch_activation(p, note_region)
    act_gt = get_pitch_activation(g, note_region)
    max_act_p = get_max_activation(p, note_region)
    max_act_gt = get_max_activation(g, note_region)

    # Onset activations
    onset_p = get_onset_activation(p, onset_region)
    onset_gt = get_onset_activation(g, onset_region)

    # Persistence
    persist_p = get_persistence(p, note_region, threshold)
    persist_gt = get_persistence(g, note_region, threshold)

    # Ratios
    eps = 1e-6
    ratio_gt_to_p = act_gt / (act_p + eps)
    ratio_p12_to_p = get_pitch_activation(p + 12, note_region) / (act_p + eps)
    ratio_pm12_to_p = get_pitch_activation(p - 12, note_region) / (act_p + eps)

    # Margins
    margin_gt_vs_p = act_gt - act_p

    # Rank GT among all octave candidates
    all_octave_candidates = [p - 24, p - 12, p, p + 12, p + 24]
    all_acts = [(c, get_pitch_activation(c, note_region)) for c in all_octave_candidates]
    all_acts.sort(key=lambda x: -x[1])

    gt_rank = next((i + 1 for i, (c, _) in enumerate(all_acts) if c == g), 6)

    # Best octave margin
    if len(all_acts) >= 2:
        octave_margin = all_acts[0][1] - all_acts[1][1]
    else:
        octave_margin = 0.0

    # GT relative to selected
    best_act = all_acts[0][1] if all_acts else 0.0
    gt_within_5 = act_gt >= best_act * 0.95
    gt_within_10 = act_gt >= best_act * 0.90
    gt_within_20 = act_gt >= best_act * 0.80

    return {
        'act_p_minus_24': candidate_acts[0],
        'act_p_minus_12': candidate_acts[1],
        'act_p': candidate_acts[2],
        'act_p_plus_12': candidate_acts[3],
        'act_p_plus_24': candidate_acts[4],
        'act_gt_minus_12': gt_acts[0],
        'act_gt': gt_acts[1],
        'act_gt_plus_12': gt_acts[2],
        'onset_act_p': onset_p,
        'onset_act_gt': onset_gt,
        'mean_act_p': act_p,
        'max_act_p': max_act_p,
        'mean_act_gt': act_gt,
        'max_act_gt': max_act_gt,
        'persist_p': persist_p,
        'persist_gt': persist_gt,
        'ratio_gt_to_p': ratio_gt_to_p,
        'ratio_p12_to_p': ratio_p12_to_p,
        'ratio_pm12_to_p': ratio_pm12_to_p,
        'margin_gt_vs_p': margin_gt_vs_p,
        'octave_margin': octave_margin,
        'gt_rank': gt_rank,
        'gt_within_5pct': gt_within_5,
        'gt_within_10pct': gt_within_10,
        'gt_within_20pct': gt_within_20,
    }


def classify_error_type(features: Dict, is_error: bool) -> str:
    """Classify error as representation, decoding, or ambiguous."""
    if not is_error:
        return "correct"

    act_gt = features['act_gt']
    act_p = features['act_p']

    # Representation failure: GT has little activation
    if act_gt < 0.1:
        return "representation"

    # Decoding failure: GT has strong activation but wasn't selected
    if act_gt > 0.3 and features['gt_rank'] <= 2:
        return "decoding"

    # Ambiguous: similar activations
    if abs(act_gt - act_p) < 0.1:
        return "ambiguous"

    return "other"


def find_stems(dataset_dir: Path, limit: int = 0) -> List[Dict]:
    """Find melodic stems in Slakh dataset."""
    import yaml

    MELODIC_CLASSES = {
        'Guitar', 'Piano', 'Bass', 'Brass', 'Synth Pad', 'Reed', 'Pipe',
        'Organ', 'Synth Lead', 'Strings', 'Strings (continued)',
        'Chromatic Percussion', 'Ethnic',
    }

    stems = []
    for subdir in ["train", "validation"]:
        split_dir = dataset_dir / subdir
        if not split_dir.exists():
            continue

        for track_dir in sorted(split_dir.iterdir()):
            if limit > 0 and len(stems) >= limit:
                break

            if not track_dir.is_dir():
                continue

            metadata_path = track_dir / "metadata.yaml"
            if not metadata_path.exists():
                continue

            with open(metadata_path) as f:
                metadata = yaml.safe_load(f)

            stems_info = metadata.get('stems', {})
            for stem_id, stem_meta in stems_info.items():
                if limit > 0 and len(stems) >= limit:
                    break

                inst_class = stem_meta.get('inst_class', '')
                if inst_class not in MELODIC_CLASSES:
                    continue

                audio_path = track_dir / "stems" / f"{stem_id}.flac"
                midi_path = track_dir / "MIDI" / f"{stem_id}.mid"

                if not audio_path.exists() or not midi_path.exists():
                    continue

                stems.append({
                    'track': track_dir.name,
                    'stem_id': stem_id,
                    'inst_class': inst_class,
                    'audio_path': audio_path,
                    'midi_path': midi_path,
                })

    return stems


def run_analysis(dataset_dir: Path, output_path: Path, limit: int = 50):
    """Run internal activation analysis."""
    stems = find_stems(dataset_dir, limit)
    print(f"Analyzing {len(stems)} stems...", flush=True)

    all_note_data = []

    for idx, stem in enumerate(stems):
        if (idx + 1) % 10 == 0:
            print(f"  [{idx+1}/{len(stems)}]...", flush=True)

        stem_name = f"{stem['track']}/{stem['stem_id']}"

        # Load GT
        gt_notes = load_midi_notes(stem['midi_path'])
        if not gt_notes:
            continue

        # Run Basic Pitch and get internal activations
        try:
            model_output, midi_data, note_events = predict(str(stem['audio_path']))
        except Exception as e:
            print(f"  Warning: Basic Pitch failed: {e}", flush=True)
            continue

        if not note_events:
            continue

        note_activations = model_output['note']
        onset_activations = model_output['onset']

        # Match notes
        matches = match_notes_with_octave(gt_notes, note_events)

        for match in matches:
            gt_idx = match['gt_idx']
            ext_idx = match['ext_idx']

            gp, go, ge = gt_notes[gt_idx]
            ext_note = note_events[ext_idx]
            ep = ext_note[2]  # pitch
            eo = ext_note[0]  # onset
            ee = ext_note[1]  # offset

            is_error = not match['exact']
            octave_delta = match['octave_delta']

            # Extract activation features
            features = extract_activation_features(
                note_activations, onset_activations,
                ep, gp, go, ge,
            )

            # Classify error type
            error_type = classify_error_type(features, is_error)

            note_data = NoteActivationData(
                stem=stem_name,
                inst_class=stem['inst_class'],
                predicted_pitch=int(ep),
                gt_pitch=int(gp),
                octave_delta=int(octave_delta),
                is_octave_error=bool(is_error),
                onset=float(go),
                offset=float(ge),
                duration=float(ge - go),
                error_type=error_type,
                **features,
            )

            all_note_data.append(asdict(note_data))

    # Save data
    print(f"\nSaved {len(all_note_data)} notes", flush=True)

    with open(output_path, 'w') as f:
        json.dump({'notes': all_note_data}, f, indent=2)

    # Analysis
    analyze_results(all_note_data)

    return all_note_data


def analyze_results(notes: List[Dict]):
    """Analyze internal activation results."""
    errors = [n for n in notes if n['is_octave_error']]
    correct = [n for n in notes if not n['is_octave_error']]

    print()
    print("=" * 70)
    print("QUESTION 1: IS CORRECT OCTAVE PRESENT IN INTERNAL ACTIVATIONS?")
    print("=" * 70)
    print()

    if errors:
        # GT rank among octave candidates
        rank_1 = sum(1 for n in errors if n['gt_rank'] == 1)
        rank_2 = sum(1 for n in errors if n['gt_rank'] == 2)
        rank_3 = sum(1 for n in errors if n['gt_rank'] == 3)

        within_5 = sum(1 for n in errors if n['gt_within_5pct'])
        within_10 = sum(1 for n in errors if n['gt_within_10pct'])
        within_20 = sum(1 for n in errors if n['gt_within_20pct'])

        print(f"For {len(errors)} octave-error notes:")
        print(f"  GT has HIGHEST activation:     {rank_1:>5} ({100*rank_1/len(errors):>5.1f}%)")
        print(f"  GT has 2nd-highest activation: {rank_2:>5} ({100*rank_2/len(errors):>5.1f}%)")
        print(f"  GT has 3rd-highest activation: {rank_3:>5} ({100*rank_3/len(errors):>5.1f}%)")
        print()
        print(f"  GT within 5% of selected:  {within_5:>5} ({100*within_5/len(errors):>5.1f}%)")
        print(f"  GT within 10% of selected: {within_10:>5} ({100*within_10/len(errors):>5.1f}%)")
        print(f"  GT within 20% of selected: {within_20:>5} ({100*within_20/len(errors):>5.1f}%)")

    print()
    print("=" * 70)
    print("QUESTION 2: REPRESENTATION VS DECODING FAILURE")
    print("=" * 70)
    print()

    if errors:
        rep_fail = sum(1 for n in errors if n['error_type'] == 'representation')
        dec_fail = sum(1 for n in errors if n['error_type'] == 'decoding')
        ambig = sum(1 for n in errors if n['error_type'] == 'ambiguous')
        other = sum(1 for n in errors if n['error_type'] == 'other')

        print(f"Error classification ({len(errors)} errors):")
        print(f"  REPRESENTATION failure: {rep_fail:>5} ({100*rep_fail/len(errors):>5.1f}%)")
        print(f"  DECODING failure:       {dec_fail:>5} ({100*dec_fail/len(errors):>5.1f}%)")
        print(f"  AMBIGUOUS:              {ambig:>5} ({100*ambig/len(errors):>5.1f}%)")
        print(f"  OTHER:                  {other:>5} ({100*other/len(errors):>5.1f}%)")

    print()
    print("=" * 70)
    print("QUESTION 4: INTERNAL-ACTIVATION ORACLE")
    print("=" * 70)
    print()

    # True oracle: For errors, is GT pitch in candidate set?
    # (i.e., if we perfectly knew which notes were wrong, could we fix them?)
    total = len(notes)
    baseline_correct = len(correct)
    n_errors = len(errors)

    # For each error, check if GT is in octave candidate set
    gt_in_candidates = 0
    for n in errors:
        p = n['predicted_pitch']
        g = n['gt_pitch']
        candidates = [p - 24, p - 12, p, p + 12, p + 24]
        if g in candidates:
            gt_in_candidates += 1

    oracle_correct = baseline_correct + gt_in_candidates
    oracle_rate = oracle_correct / total
    baseline_rate = baseline_correct / total

    print(f"Total notes: {total}")
    print(f"Baseline correct: {baseline_correct} ({100*baseline_rate:.1f}%)")
    print(f"Errors: {n_errors} ({100*n_errors/total:.1f}%)")
    print()
    print(f"For {n_errors} errors, GT in candidate set: {gt_in_candidates} ({100*gt_in_candidates/n_errors:.1f}%)")
    print(f"Oracle correct (keep correct + fix recoverable): {oracle_correct} ({100*oracle_rate:.1f}%)")
    print(f"Oracle improvement: +{100*(oracle_rate - baseline_rate):.1f}%")
    print()

    total_oracle_potential = n_errors / total  # All errors could theoretically be fixed
    internal_recovery = gt_in_candidates / total
    pct_recoverable = 100 * gt_in_candidates / n_errors if n_errors > 0 else 0
    print(f"Total octave oracle potential: +{100*total_oracle_potential:.1f}%")
    print(f"Internal recoverable:          +{100*internal_recovery:.1f}%")
    print(f"Percentage recoverable:        {pct_recoverable:.1f}%")

    print()
    print("=" * 70)
    print("QUESTION 3: INTERNAL FEATURES AS ERROR PREDICTORS")
    print("=" * 70)
    print()

    # Can we predict errors from activation patterns?
    # Key features to test:
    # 1. Activation at selected pitch (act_p)
    # 2. Octave margin (gap between best and 2nd-best octave)
    # 3. Ratio of nearby octaves

    if correct and errors:
        # Feature: activation at selected pitch
        correct_act = [n['act_p'] for n in correct]
        error_act = [n['act_p'] for n in errors]
        print(f"Mean activation at selected pitch:")
        print(f"  Correct: {np.mean(correct_act):.3f}  (std: {np.std(correct_act):.3f})")
        print(f"  Errors:  {np.mean(error_act):.3f}  (std: {np.std(error_act):.3f})")
        print()

        # Feature: octave margin
        correct_margin = [n['octave_margin'] for n in correct]
        error_margin = [n['octave_margin'] for n in errors]
        print(f"Octave margin (best - 2nd best):")
        print(f"  Correct: {np.mean(correct_margin):.3f}  (std: {np.std(correct_margin):.3f})")
        print(f"  Errors:  {np.mean(error_margin):.3f}  (std: {np.std(error_margin):.3f})")
        print()

        # Feature: GT rank for errors
        rank_dist = defaultdict(int)
        for n in errors:
            rank_dist[n['gt_rank']] += 1
        print(f"GT rank distribution (errors):")
        for rank in sorted(rank_dist.keys()):
            print(f"  Rank {rank}: {rank_dist[rank]:>4} ({100*rank_dist[rank]/len(errors):.1f}%)")
        print()

        # Simple threshold test: low activation predicts error?
        thresholds = [0.15, 0.20, 0.25, 0.30]
        print("Error detection via low activation threshold:")
        for thresh in thresholds:
            low_act_correct = sum(1 for n in correct if n['act_p'] < thresh)
            low_act_error = sum(1 for n in errors if n['act_p'] < thresh)
            total_flagged = low_act_correct + low_act_error
            if total_flagged > 0:
                precision = 100 * low_act_error / total_flagged
                recall = 100 * low_act_error / len(errors)
                print(f"  act < {thresh}: precision={precision:.1f}% recall={recall:.1f}% (flagged {total_flagged})")

    print()
    print("=" * 70)
    print("QUESTION 5: ALTERNATIVE DECODING STRATEGIES")
    print("=" * 70)
    print()

    # Test: What if we used max-activation decoding?
    # For each note, compare decoder choice vs max-activation choice
    max_act_correct = 0
    max_act_flips_correct_to_wrong = 0
    max_act_flips_wrong_to_correct = 0
    max_act_keeps_correct = 0
    max_act_keeps_wrong = 0

    for n in notes:
        p = n['predicted_pitch']
        g = n['gt_pitch']
        is_error = n['is_octave_error']

        candidates = [
            (p - 24, n['act_p_minus_24']),
            (p - 12, n['act_p_minus_12']),
            (p, n['act_p']),
            (p + 12, n['act_p_plus_12']),
            (p + 24, n['act_p_plus_24']),
        ]
        max_pitch = max(candidates, key=lambda x: x[1])[0]

        if max_pitch == g:
            max_act_correct += 1

        decoder_correct = not is_error
        max_correct = (max_pitch == g)

        if decoder_correct and max_correct:
            max_act_keeps_correct += 1
        elif decoder_correct and not max_correct:
            max_act_flips_correct_to_wrong += 1
        elif not decoder_correct and max_correct:
            max_act_flips_wrong_to_correct += 1
        else:
            max_act_keeps_wrong += 1

    print("Max-activation decoding analysis:")
    print(f"  Total notes: {total}")
    print(f"  Decoder correct: {len(correct)} ({100*len(correct)/total:.1f}%)")
    print(f"  Max-act correct: {max_act_correct} ({100*max_act_correct/total:.1f}%)")
    print()
    print("  Transition matrix:")
    print(f"    Keeps correct:       {max_act_keeps_correct:>5}")
    print(f"    Flips correct→wrong: {max_act_flips_correct_to_wrong:>5}")
    print(f"    Flips wrong→correct: {max_act_flips_wrong_to_correct:>5}")
    print(f"    Keeps wrong:         {max_act_keeps_wrong:>5}")
    print()
    net_change = max_act_flips_wrong_to_correct - max_act_flips_correct_to_wrong
    print(f"  Net change: {net_change:+d} notes ({100*net_change/total:+.1f}%)")

    print()
    print("=" * 70)
    print("PER-CLASS BREAKDOWN")
    print("=" * 70)
    print()

    # Per instrument class analysis
    by_class = defaultdict(list)
    for n in notes:
        by_class[n['inst_class']].append(n)

    print(f"{'Class':<25} {'Total':>6} {'Err%':>6} {'Rep%':>5} {'Dec%':>5} {'Amb%':>5} {'GT in cand%':>10}")
    print("-" * 75)

    for cls in sorted(by_class.keys(), key=lambda c: -len(by_class[c])):
        cls_notes = by_class[cls]
        cls_errors = [n for n in cls_notes if n['is_octave_error']]
        n_cls = len(cls_notes)
        n_err = len(cls_errors)
        err_rate = 100 * n_err / n_cls if n_cls > 0 else 0

        if n_err > 0:
            rep = sum(1 for n in cls_errors if n['error_type'] == 'representation')
            dec = sum(1 for n in cls_errors if n['error_type'] == 'decoding')
            amb = sum(1 for n in cls_errors if n['error_type'] == 'ambiguous')

            gt_in_cand = 0
            for n in cls_errors:
                p = n['predicted_pitch']
                g = n['gt_pitch']
                if g in [p - 24, p - 12, p, p + 12, p + 24]:
                    gt_in_cand += 1

            print(f"{cls:<25} {n_cls:>6} {err_rate:>5.1f}% {100*rep/n_err:>4.0f}% {100*dec/n_err:>4.0f}% {100*amb/n_err:>4.0f}% {100*gt_in_cand/n_err:>9.0f}%")
        else:
            print(f"{cls:<25} {n_cls:>6} {err_rate:>5.1f}%    -     -     -          -")

    print()
    print("=" * 70)
    print("OCTAVE DIRECTION ANALYSIS")
    print("=" * 70)
    print()

    up_errors = [n for n in errors if n['octave_delta'] > 0]
    down_errors = [n for n in errors if n['octave_delta'] < 0]
    print(f"Octave direction:")
    print(f"  UP (predicted too high):   {len(up_errors):>4} ({100*len(up_errors)/len(errors):.1f}%)")
    print(f"  DOWN (predicted too low):  {len(down_errors):>4} ({100*len(down_errors)/len(errors):.1f}%)")

    # Magnitude
    print()
    print("Octave magnitude:")
    mag_dist = defaultdict(int)
    for n in errors:
        mag = abs(n['octave_delta'])
        mag_dist[mag] += 1
    for mag in sorted(mag_dist.keys()):
        print(f"  {mag} octave(s): {mag_dist[mag]:>4} ({100*mag_dist[mag]/len(errors):.1f}%)")

    print()
    print("=" * 70)
    print("ACTIVATION STATISTICS")
    print("=" * 70)
    print()

    # Compare activations for correct vs error notes
    correct_act_p = [n['act_p'] for n in correct]
    error_act_p = [n['act_p'] for n in errors]
    error_act_gt = [n['act_gt'] for n in errors]

    print(f"Mean activation at selected pitch:")
    print(f"  Correct notes: {np.mean(correct_act_p):.3f}")
    print(f"  Error notes:   {np.mean(error_act_p):.3f}")
    print()
    print(f"For error notes, activation at GT pitch: {np.mean(error_act_gt):.3f}")
    print(f"Ratio GT/Selected for errors: {np.mean([n['ratio_gt_to_p'] for n in errors]):.3f}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=str,
                       default='/Users/mattharvey/Sites/tone-forge/datasets/slakh2100_flac_redux')
    parser.add_argument('--output', type=str, default='/tmp/basic_pitch_internal.json')
    parser.add_argument('--limit', type=int, default=50)
    args = parser.parse_args()

    run_analysis(Path(args.dataset), Path(args.output), args.limit)
