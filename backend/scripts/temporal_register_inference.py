#!/usr/bin/env python3
"""Temporal Register Inference Experiment.

Tests whether octave selection is better modeled as a latent register problem.

For each note, computes evidence for H-1, H0, H+1 hypotheses.
Uses Viterbi-style sequence optimization with empirical transition priors.

Compares:
- Note-level (independent)
- Phrase-level (sequence per phrase)
- Stem-level (sequence per stem)
- Conservative high-confidence
"""
import json
import sys
from pathlib import Path
from collections import defaultdict
import numpy as np
from typing import List, Dict, Tuple
import random

# Empirical transition prior from corpus analysis
TRANSITION_SAME = 0.723
TRANSITION_DIFF = 0.277


def load_data(json_path: Path) -> Tuple[List[Dict], List[Dict]]:
    """Load octave analysis data."""
    with open(json_path) as f:
        data = json.load(f)
    return data['notes'], data['stems']


def compute_hypothesis_evidence(note: Dict) -> Dict[int, float]:
    """
    Compute log-likelihood evidence for each octave hypothesis.

    H-1: predicted - 12 (model went too high, actual is lower)
    H0:  predicted (model is correct)
    H+1: predicted + 12 (model went too low, actual is higher)

    Uses audio features to estimate which hypothesis is most supported.
    """
    # Extract features
    subharm_ratio = note['ratio_half_to_f0']  # Strong subharm suggests H+1
    second_harm = note['ratio_2f0_to_f0']      # Strong 2nd suggests H-1
    centroid = note['spectral_centroid']
    f0_energy = note['energy_at_f0']

    # Initialize log-likelihoods
    evidence = {-1: 0.0, 0: 0.0, 1: 0.0}

    # Subharmonic evidence
    # High subharmonic ratio suggests model detected subharmonic as fundamental
    # Model went DOWN, so H+1 (shift UP) is more likely
    if subharm_ratio > 1.0:
        evidence[1] += np.log(1 + subharm_ratio)  # Support H+1
        evidence[0] -= np.log(1 + subharm_ratio * 0.5)
        evidence[-1] -= np.log(1 + subharm_ratio)
    elif subharm_ratio < 0.3:
        evidence[0] += 0.5  # Support H0 when subharmonic is weak

    # Second harmonic evidence
    # Strong second harmonic suggests model detected 2nd harmonic as fundamental
    # Model went UP, so H-1 (shift DOWN) is more likely
    if second_harm > 1.5:
        evidence[-1] += np.log(1 + second_harm * 0.5)
        evidence[0] -= np.log(1 + second_harm * 0.3)
        evidence[1] -= np.log(1 + second_harm * 0.5)

    # Spectral centroid evidence
    # Very high centroid suggests high-pitched content
    if centroid > 4000:
        evidence[-1] += 1.0  # More likely model went too high
        evidence[1] -= 0.5
    elif centroid < 500:
        evidence[1] += 1.0  # More likely model went too low
        evidence[-1] -= 0.5

    # F0 energy evidence
    # Very low f0 energy suggests missing fundamental
    if f0_energy < 0.1 and f0_energy > 0:
        evidence[0] -= 0.5  # Less confident in model's choice

    # Prior: model is usually correct (82% baseline)
    evidence[0] += 1.5  # Strong prior for model being correct

    return evidence


def viterbi_decode(
    notes: List[Dict],
    transition_same: float = TRANSITION_SAME,
) -> List[int]:
    """
    Viterbi decoding to find best octave shift sequence.

    States: -1, 0, +1 (octave shift)
    Emissions: evidence from audio features
    Transitions: empirical prior from corpus
    """
    if not notes:
        return []

    states = [-1, 0, 1]
    n = len(notes)

    # Log transition probabilities
    log_trans_same = np.log(transition_same)
    log_trans_diff = np.log(transition_same / 2)  # Split among 2 different states

    # Initialize
    V = [{} for _ in range(n)]
    path = [{} for _ in range(n)]

    # First note
    first_evidence = compute_hypothesis_evidence(notes[0])
    for s in states:
        V[0][s] = first_evidence[s]
        path[0][s] = [s]

    # Forward pass
    for t in range(1, n):
        evidence = compute_hypothesis_evidence(notes[t])

        for s in states:
            best_prev = None
            best_prob = float('-inf')

            for prev_s in states:
                if prev_s == s:
                    trans = log_trans_same
                else:
                    trans = log_trans_diff

                prob = V[t-1][prev_s] + trans + evidence[s]

                if prob > best_prob:
                    best_prob = prob
                    best_prev = prev_s

            V[t][s] = best_prob
            path[t][s] = path[t-1][best_prev] + [s]

    # Find best final state
    best_final = max(states, key=lambda s: V[n-1][s])

    return path[n-1][best_final]


def segment_into_phrases(notes: List[Dict], gap_threshold: float = 2.0) -> List[List[Dict]]:
    """Segment notes into phrases based on timing gaps."""
    if not notes:
        return []

    sorted_notes = sorted(notes, key=lambda n: n['onset'])
    phrases = []
    current_phrase = [sorted_notes[0]]

    for i in range(1, len(sorted_notes)):
        prev_end = sorted_notes[i-1]['offset']
        curr_start = sorted_notes[i]['onset']

        if curr_start - prev_end > gap_threshold:
            phrases.append(current_phrase)
            current_phrase = [sorted_notes[i]]
        else:
            current_phrase.append(sorted_notes[i])

    phrases.append(current_phrase)
    return phrases


def evaluate_corrections(notes: List[Dict], corrections: List[int]) -> Dict:
    """Evaluate correction decisions against ground truth."""
    if len(notes) != len(corrections):
        raise ValueError("Length mismatch")

    correct_before = 0
    correct_after = 0
    true_positives = 0  # Correctly fixed an error
    false_positives = 0  # Incorrectly changed a correct note
    false_negatives = 0  # Failed to fix an error
    true_negatives = 0   # Correctly left unchanged

    for note, correction in zip(notes, corrections):
        was_error = note['is_octave_error']
        needed_shift = -note['octave_delta'] if was_error else 0

        if not was_error:
            correct_before += 1

        # After correction
        if correction == 0:
            # No change
            if not was_error:
                correct_after += 1
                true_negatives += 1
            else:
                false_negatives += 1
        else:
            # Made a correction
            if was_error and correction == needed_shift:
                correct_after += 1
                true_positives += 1
            elif was_error and correction != needed_shift:
                false_positives += 1  # Wrong correction
            elif not was_error:
                false_positives += 1  # Broke a correct note

    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0

    return {
        'before': correct_before,
        'after': correct_after,
        'total': len(notes),
        'before_rate': correct_before / len(notes),
        'after_rate': correct_after / len(notes),
        'delta': (correct_after - correct_before) / len(notes),
        'precision': precision,
        'recall': recall,
        'tp': true_positives,
        'fp': false_positives,
        'fn': false_negatives,
        'tn': true_negatives,
    }


def independent_correction(notes: List[Dict]) -> List[int]:
    """Each note independently chooses best hypothesis."""
    corrections = []
    for note in notes:
        evidence = compute_hypothesis_evidence(note)
        best = max(evidence.keys(), key=lambda k: evidence[k])
        corrections.append(best)
    return corrections


def phrase_viterbi_correction(notes: List[Dict]) -> List[int]:
    """Viterbi decoding per phrase."""
    stem_notes = defaultdict(list)
    for i, n in enumerate(notes):
        stem_notes[n['stem']].append((i, n))

    corrections = [0] * len(notes)

    for stem, indexed_notes in stem_notes.items():
        sorted_indexed = sorted(indexed_notes, key=lambda x: x[1]['onset'])
        just_notes = [n for _, n in sorted_indexed]
        indices = [i for i, _ in sorted_indexed]

        phrases = segment_into_phrases(just_notes)
        phrase_start = 0

        for phrase in phrases:
            phrase_corrections = viterbi_decode(phrase)
            for j, corr in enumerate(phrase_corrections):
                original_idx = indices[phrase_start + j]
                corrections[original_idx] = corr
            phrase_start += len(phrase)

    return corrections


def stem_viterbi_correction(notes: List[Dict]) -> List[int]:
    """Viterbi decoding per entire stem."""
    stem_notes = defaultdict(list)
    for i, n in enumerate(notes):
        stem_notes[n['stem']].append((i, n))

    corrections = [0] * len(notes)

    for stem, indexed_notes in stem_notes.items():
        sorted_indexed = sorted(indexed_notes, key=lambda x: x[1]['onset'])
        just_notes = [n for _, n in sorted_indexed]
        indices = [i for i, _ in sorted_indexed]

        stem_corrections = viterbi_decode(just_notes)
        for j, corr in enumerate(stem_corrections):
            corrections[indices[j]] = corr

    return corrections


def conservative_correction(notes: List[Dict], subharm_thresh: float = 2.0, centroid_thresh: float = 5000) -> List[int]:
    """
    Very conservative: only correct when overwhelming evidence.

    High precision, lower recall.
    """
    corrections = []
    for note in notes:
        correction = 0

        # Very strong subharmonic -> shift UP
        if note['ratio_half_to_f0'] > subharm_thresh:
            correction = 1

        # Very high centroid -> shift DOWN
        elif note['spectral_centroid'] > centroid_thresh:
            correction = -1

        corrections.append(correction)

    return corrections


def run_experiment():
    """Run full temporal register inference experiment."""
    json_path = Path('/tmp/octave_oracle_data.json')
    if not json_path.exists():
        print(f"Error: {json_path} not found")
        sys.exit(1)

    notes, stems = load_data(json_path)
    print(f"Loaded {len(notes)} notes from {len(stems)} stems")
    print()

    # Split into dev and held-out
    stem_names = list(set(n['stem'] for n in notes))
    random.seed(42)
    random.shuffle(stem_names)

    split = int(0.7 * len(stem_names))
    dev_stems = set(stem_names[:split])
    held_stems = set(stem_names[split:])

    dev_notes = [n for n in notes if n['stem'] in dev_stems]
    held_notes = [n for n in notes if n['stem'] in held_stems]

    print(f"Dev set: {len(dev_stems)} stems, {len(dev_notes)} notes")
    print(f"Held-out: {len(held_stems)} stems, {len(held_notes)} notes")
    print()

    # Run all methods on dev set
    print("=" * 70)
    print("DEVELOPMENT SET RESULTS")
    print("=" * 70)
    print()

    methods = [
        ("Independent (per-note)", independent_correction),
        ("Phrase Viterbi", phrase_viterbi_correction),
        ("Stem Viterbi", stem_viterbi_correction),
        ("Conservative (subharm>2, cent>5k)", lambda n: conservative_correction(n, 2.0, 5000)),
        ("Conservative (subharm>3, cent>6k)", lambda n: conservative_correction(n, 3.0, 6000)),
    ]

    print(f"{'Method':<40} {'Before':>7} {'After':>7} {'Delta':>7} {'Prec':>6} {'Rec':>6}")
    print("-" * 80)

    dev_results = {}
    for name, method in methods:
        corrections = method(dev_notes)
        result = evaluate_corrections(dev_notes, corrections)
        dev_results[name] = result
        print(f"{name:<40} {100*result['before_rate']:>6.1f}% {100*result['after_rate']:>6.1f}% {100*result['delta']:>+6.1f}% {100*result['precision']:>5.1f}% {100*result['recall']:>5.1f}%")

    # Cross-validation
    print()
    print("=" * 70)
    print("5-FOLD CROSS-VALIDATION")
    print("=" * 70)
    print()

    cv_results = defaultdict(list)
    fold_size = len(stem_names) // 5

    for fold in range(5):
        test_stems = set(stem_names[fold * fold_size:(fold + 1) * fold_size])
        test_notes = [n for n in notes if n['stem'] in test_stems]

        for name, method in methods:
            corrections = method(test_notes)
            result = evaluate_corrections(test_notes, corrections)
            cv_results[name].append(result['delta'])

    print(f"{'Method':<40} {'Mean':>8} {'Std':>7} {'Stable':>7}")
    print("-" * 65)

    for name, _ in methods:
        deltas = cv_results[name]
        mean_delta = np.mean(deltas)
        std_delta = np.std(deltas)
        stable = "YES" if std_delta < abs(mean_delta) * 0.5 else "NO"
        print(f"{name:<40} {100*mean_delta:>+7.1f}% {100*std_delta:>6.1f}% {stable:>7}")

    # Held-out validation
    print()
    print("=" * 70)
    print("HELD-OUT VALIDATION")
    print("=" * 70)
    print()

    print(f"{'Method':<40} {'Before':>7} {'After':>7} {'Delta':>7} {'Prec':>6} {'Rec':>6}")
    print("-" * 80)

    held_results = {}
    for name, method in methods:
        corrections = method(held_notes)
        result = evaluate_corrections(held_notes, corrections)
        held_results[name] = result
        print(f"{name:<40} {100*result['before_rate']:>6.1f}% {100*result['after_rate']:>6.1f}% {100*result['delta']:>+6.1f}% {100*result['precision']:>5.1f}% {100*result['recall']:>5.1f}%")

    # Per-class analysis for best method
    print()
    print("=" * 70)
    print("PER-CLASS ANALYSIS (Stem Viterbi on held-out)")
    print("=" * 70)
    print()

    corrections = stem_viterbi_correction(held_notes)

    class_results = defaultdict(lambda: {'before': 0, 'after': 0, 'total': 0})
    for note, corr in zip(held_notes, corrections):
        cls = note['inst_class']
        class_results[cls]['total'] += 1

        if not note['is_octave_error']:
            class_results[cls]['before'] += 1

        needed_shift = -note['octave_delta'] if note['is_octave_error'] else 0
        if corr == 0:
            if not note['is_octave_error']:
                class_results[cls]['after'] += 1
        else:
            if note['is_octave_error'] and corr == needed_shift:
                class_results[cls]['after'] += 1

    print(f"{'Class':<25} {'Before':>8} {'After':>8} {'Delta':>8}")
    print("-" * 55)

    for cls in sorted(class_results.keys(), key=lambda x: -class_results[x]['total']):
        r = class_results[cls]
        before = 100 * r['before'] / r['total']
        after = 100 * r['after'] / r['total']
        delta = after - before
        print(f"{cls:<25} {before:>7.1f}% {after:>7.1f}% {delta:>+7.1f}%")

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print()

    # Oracle comparisons
    baseline = sum(1 for n in notes if not n['is_octave_error']) / len(notes)
    note_oracle = 1.0
    print(f"Baseline:          {100*baseline:.1f}%")
    print(f"Note oracle:       100.0% (+{100*(1-baseline):.1f}%)")
    print()

    # Best results
    best_dev = max(dev_results.items(), key=lambda x: x[1]['delta'])
    best_held = max(held_results.items(), key=lambda x: x[1]['delta'])

    print(f"Best dev method:      {best_dev[0]}")
    print(f"  Delta: {100*best_dev[1]['delta']:+.1f}%, Precision: {100*best_dev[1]['precision']:.1f}%")
    print()
    print(f"Best held-out method: {best_held[0]}")
    print(f"  Delta: {100*best_held[1]['delta']:+.1f}%, Precision: {100*best_held[1]['precision']:.1f}%")
    print()

    # Conservative analysis
    cons_result = held_results.get("Conservative (subharm>3, cent>6k)", {})
    if cons_result:
        print(f"Conservative (high precision):")
        print(f"  Corrections made: {cons_result.get('tp', 0) + cons_result.get('fp', 0)}")
        print(f"  Precision: {100*cons_result.get('precision', 0):.1f}%")
        print(f"  Recall: {100*cons_result.get('recall', 0):.1f}%")


if __name__ == '__main__':
    run_experiment()
