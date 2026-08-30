#!/usr/bin/env python3
"""Octave Error Predictability Analysis.

Analyzes octave_oracle_data.json to determine:
1. Root causes of octave errors
2. Whether errors can be predicted from audio features
3. Best correction strategy (per-note, per-phrase, per-stem)
4. Cross-validation stability
"""
import json
import sys
from pathlib import Path
from collections import defaultdict
import numpy as np


def load_data(json_path: Path) -> tuple:
    """Load octave analysis data."""
    with open(json_path) as f:
        data = json.load(f)
    return data['notes'], data['stems']


def analyze_root_causes(notes: list) -> dict:
    """Analyze potential root causes of octave errors."""
    error_notes = [n for n in notes if n['is_octave_error']]
    correct_notes = [n for n in notes if not n['is_octave_error']]
    down_errors = [n for n in error_notes if n['octave_delta'] < 0]
    up_errors = [n for n in error_notes if n['octave_delta'] > 0]

    causes = {}

    # 1. Missing/weak fundamentals
    # If f0 energy is low relative to harmonics, model might pick wrong octave
    correct_f0 = [n['energy_at_f0'] for n in correct_notes if n['energy_at_f0'] > 0]
    error_f0 = [n['energy_at_f0'] for n in error_notes if n['energy_at_f0'] > 0]

    if correct_f0 and error_f0:
        causes['weak_fundamental'] = {
            'correct_mean_f0': np.mean(correct_f0),
            'error_mean_f0': np.mean(error_f0),
            'ratio': np.mean(error_f0) / max(1e-10, np.mean(correct_f0)),
            'hypothesis': 'error < correct suggests weak fundamentals cause errors',
        }

    # 2. Strong second harmonics
    correct_2f0 = [n['ratio_2f0_to_f0'] for n in correct_notes]
    error_2f0 = [n['ratio_2f0_to_f0'] for n in error_notes]

    if correct_2f0 and error_2f0:
        causes['strong_second_harmonic'] = {
            'correct_mean': np.mean(correct_2f0),
            'error_mean': np.mean(error_2f0),
            'ratio': np.mean(error_2f0) / max(1e-10, np.mean(correct_2f0)),
            'hypothesis': 'error > correct suggests strong 2nd harmonic confuses model',
        }

    # 3. Subharmonic confusion (for octave-down errors)
    if down_errors:
        down_subharm = [n['ratio_half_to_f0'] for n in down_errors]
        correct_subharm = [n['ratio_half_to_f0'] for n in correct_notes]
        causes['subharmonic_confusion'] = {
            'down_error_mean': np.mean(down_subharm),
            'correct_mean': np.mean(correct_subharm),
            'hypothesis': 'higher subharmonic energy in down-errors suggests phantom fundamental',
        }

    # 4. Register-dependent bias
    register_errors = defaultdict(lambda: {'total': 0, 'errors': 0})
    for n in notes:
        midi = n['gt_pitch']
        if midi < 48:
            reg = 'bass'
        elif midi < 60:
            reg = 'low'
        elif midi < 72:
            reg = 'mid'
        elif midi < 84:
            reg = 'high'
        else:
            reg = 'very_high'
        register_errors[reg]['total'] += 1
        if n['is_octave_error']:
            register_errors[reg]['errors'] += 1

    causes['register_bias'] = {
        reg: {
            'total': v['total'],
            'errors': v['errors'],
            'rate': v['errors'] / max(1, v['total']),
        }
        for reg, v in register_errors.items()
    }

    # 5. Polyphonic interference
    correct_poly = [n['local_polyphony'] for n in correct_notes]
    error_poly = [n['local_polyphony'] for n in error_notes]

    if correct_poly and error_poly:
        causes['polyphonic_interference'] = {
            'correct_mean_polyphony': np.mean(correct_poly),
            'error_mean_polyphony': np.mean(error_poly),
            'hypothesis': 'higher polyphony in errors suggests interference',
        }

    # 6. Spectral characteristics
    correct_flatness = [n['spectral_flatness'] for n in correct_notes]
    error_flatness = [n['spectral_flatness'] for n in error_notes]

    if correct_flatness and error_flatness:
        causes['spectral_ambiguity'] = {
            'correct_flatness': np.mean(correct_flatness),
            'error_flatness': np.mean(error_flatness),
            'hypothesis': 'flatter spectrum (more noise-like) may cause ambiguity',
        }

    return causes


def test_predictability(notes: list) -> list:
    """Test various rules for predicting octave errors."""
    results = []

    # Define predictive rules
    rules = [
        # Missing fundamental rules
        ("low_f0_energy", lambda n: n['energy_at_f0'] < 0.1),
        ("weak_f0_vs_2f0", lambda n: n['ratio_2f0_to_f0'] > 1.0),
        ("weak_f0_vs_2f0_strong", lambda n: n['ratio_2f0_to_f0'] > 2.0),

        # Subharmonic rules
        ("strong_subharmonic", lambda n: n['ratio_half_to_f0'] > 0.5),
        ("strong_subharmonic_2x", lambda n: n['ratio_half_to_f0'] > 1.0),

        # Spectral rules
        ("flat_spectrum", lambda n: n['spectral_flatness'] > 0.1),
        ("low_centroid", lambda n: n['spectral_centroid'] < 1000),
        ("high_centroid", lambda n: n['spectral_centroid'] > 4000),

        # Combined rules
        ("weak_f0_AND_strong_2f0", lambda n: n['energy_at_f0'] < 0.5 and n['ratio_2f0_to_f0'] > 1.0),
        ("high_poly_AND_weak_f0", lambda n: n['local_polyphony'] > 3 and n['energy_at_f0'] < 0.5),

        # Duration rules
        ("short_note", lambda n: n['duration'] < 0.2),
        ("long_note", lambda n: n['duration'] > 1.0),

        # Register rules
        ("bass_register", lambda n: n['gt_pitch'] < 48),
        ("treble_register", lambda n: n['gt_pitch'] > 72),
    ]

    for name, rule in rules:
        try:
            flagged = [n for n in notes if rule(n)]
            not_flagged = [n for n in notes if not rule(n)]

            flagged_errors = sum(1 for n in flagged if n['is_octave_error'])
            not_flagged_errors = sum(1 for n in not_flagged if n['is_octave_error'])

            if len(flagged) > 0 and len(not_flagged) > 0:
                flagged_rate = flagged_errors / len(flagged)
                not_flagged_rate = not_flagged_errors / len(not_flagged)
                lift = flagged_rate / max(0.001, not_flagged_rate)

                results.append({
                    'rule': name,
                    'flagged': len(flagged),
                    'flagged_errors': flagged_errors,
                    'flagged_rate': flagged_rate,
                    'not_flagged_rate': not_flagged_rate,
                    'lift': lift,
                })
        except Exception:
            continue

    return sorted(results, key=lambda x: -x['lift'])


def test_octave_correction_feasibility(notes: list) -> dict:
    """Test if we can predict WHICH direction the octave error is."""
    error_notes = [n for n in notes if n['is_octave_error']]
    if not error_notes:
        return {'feasible': False, 'reason': 'no errors'}

    # For each error note, can we predict if it's up or down?
    rules = [
        # Predict octave-DOWN (model went too low)
        ("predict_down: high_subharm", lambda n: n['ratio_half_to_f0'] > 0.5, -1),
        ("predict_down: low_centroid", lambda n: n['spectral_centroid'] < 2000, -1),

        # Predict octave-UP (model went too high)
        ("predict_up: strong_2f0", lambda n: n['ratio_2f0_to_f0'] > 1.5, 1),
        ("predict_up: high_centroid", lambda n: n['spectral_centroid'] > 3000, 1),
        ("predict_up: weak_f0", lambda n: n['energy_at_f0'] < 0.2, 1),
    ]

    results = []
    for name, rule, expected_direction in rules:
        try:
            matching = [n for n in error_notes if rule(n)]
            if not matching:
                continue

            # How many have the expected direction?
            correct_dir = sum(1 for n in matching
                            if (n['octave_delta'] < 0 and expected_direction < 0)
                            or (n['octave_delta'] > 0 and expected_direction > 0))

            accuracy = correct_dir / len(matching)
            results.append({
                'rule': name,
                'matching': len(matching),
                'correct_direction': correct_dir,
                'accuracy': accuracy,
            })
        except Exception:
            continue

    return {
        'feasible': any(r['accuracy'] > 0.6 for r in results),
        'rules': sorted(results, key=lambda x: -x['accuracy']),
    }


def test_correction_granularity(notes: list, stems: list) -> dict:
    """Compare per-note vs per-stem correction."""
    # Per-note: each note gets corrected individually
    # Per-stem: all notes in stem shifted same direction

    # Group by stem
    stem_notes = defaultdict(list)
    for n in notes:
        stem_notes[n['stem']].append(n)

    # Per-stem analysis: what's dominant error direction?
    stem_analysis = []
    for stem_name, stem_note_list in stem_notes.items():
        errors = [n for n in stem_note_list if n['is_octave_error']]
        if not errors:
            continue

        up = sum(1 for n in errors if n['octave_delta'] > 0)
        down = sum(1 for n in errors if n['octave_delta'] < 0)

        stem_analysis.append({
            'stem': stem_name,
            'total': len(stem_note_list),
            'errors': len(errors),
            'up': up,
            'down': down,
            'dominant': 'up' if up > down else 'down' if down > up else 'mixed',
            'consistency': max(up, down) / len(errors) if errors else 0,
        })

    # High consistency = per-stem correction viable
    avg_consistency = np.mean([s['consistency'] for s in stem_analysis]) if stem_analysis else 0

    return {
        'per_stem_consistency': avg_consistency,
        'recommendation': 'per-stem' if avg_consistency > 0.8 else 'per-note',
        'stems': stem_analysis,
    }


def cross_validate(notes: list, n_splits: int = 5) -> dict:
    """Cross-validate predictive rules."""
    import random

    # Group by stem for proper CV
    stem_notes = defaultdict(list)
    for n in notes:
        stem_notes[n['stem']].append(n)

    stems = list(stem_notes.keys())
    random.seed(42)
    random.shuffle(stems)

    # Best rule from full data
    best_rules = test_predictability(notes)[:3]

    cv_results = []
    for rule_info in best_rules:
        rule_name = rule_info['rule']

        fold_lifts = []
        fold_size = len(stems) // n_splits

        for fold in range(n_splits):
            test_stems = set(stems[fold * fold_size:(fold + 1) * fold_size])
            train_stems = set(stems) - test_stems

            test_notes = [n for n in notes if n['stem'] in test_stems]
            if not test_notes:
                continue

            # Re-run rule on test set
            for r in test_predictability(test_notes):
                if r['rule'] == rule_name:
                    fold_lifts.append(r['lift'])
                    break

        if fold_lifts:
            cv_results.append({
                'rule': rule_name,
                'mean_lift': np.mean(fold_lifts),
                'std_lift': np.std(fold_lifts),
                'stable': np.std(fold_lifts) < 0.5 * np.mean(fold_lifts),
            })

    return {
        'n_splits': n_splits,
        'results': cv_results,
    }


def main():
    json_path = Path('/tmp/octave_oracle_data.json')
    if not json_path.exists():
        print(f"Error: {json_path} not found. Run octave_oracle_experiment.py first.")
        sys.exit(1)

    notes, stems = load_data(json_path)
    print(f"Loaded {len(notes)} notes from {len(stems)} stems")

    error_notes = [n for n in notes if n['is_octave_error']]
    print(f"Octave errors: {len(error_notes)} ({100*len(error_notes)/len(notes):.1f}%)")
    print()

    # 1. Root causes
    print("=" * 70)
    print("ROOT CAUSE ANALYSIS")
    print("=" * 70)

    causes = analyze_root_causes(notes)

    if 'weak_fundamental' in causes:
        c = causes['weak_fundamental']
        print(f"Weak fundamental: error/correct ratio = {c['ratio']:.2f}")
        print(f"  {c['hypothesis']}")

    if 'strong_second_harmonic' in causes:
        c = causes['strong_second_harmonic']
        print(f"Strong 2nd harmonic: error/correct ratio = {c['ratio']:.2f}")
        print(f"  {c['hypothesis']}")

    if 'subharmonic_confusion' in causes:
        c = causes['subharmonic_confusion']
        print(f"Subharmonic (octave-down): {c['down_error_mean']:.3f} vs correct {c['correct_mean']:.3f}")

    if 'polyphonic_interference' in causes:
        c = causes['polyphonic_interference']
        print(f"Polyphony: errors avg {c['error_mean_polyphony']:.1f} vs correct {c['correct_mean_polyphony']:.1f}")

    print()
    print("Register bias:")
    if 'register_bias' in causes:
        for reg, v in sorted(causes['register_bias'].items()):
            print(f"  {reg:>12}: {100*v['rate']:.1f}% error rate ({v['errors']}/{v['total']})")

    # 2. Predictability
    print()
    print("=" * 70)
    print("PREDICTABILITY ANALYSIS")
    print("=" * 70)

    pred_results = test_predictability(notes)
    print(f"{'Rule':<35} {'Flagged':>8} {'ErrRate':>8} {'Lift':>6}")
    print("-" * 65)

    for r in pred_results[:10]:
        print(f"{r['rule']:<35} {r['flagged']:>8} {100*r['flagged_rate']:>7.1f}% {r['lift']:>5.1f}x")

    # 3. Direction prediction
    print()
    print("=" * 70)
    print("DIRECTION PREDICTION (up vs down)")
    print("=" * 70)

    dir_results = test_octave_correction_feasibility(notes)
    if dir_results['feasible']:
        print("Direction prediction appears feasible:")
        for r in dir_results.get('rules', [])[:5]:
            print(f"  {r['rule']:<40}: {100*r['accuracy']:.1f}% accurate ({r['matching']} notes)")
    else:
        print("Direction prediction NOT reliably feasible with current features")

    # 4. Granularity
    print()
    print("=" * 70)
    print("CORRECTION GRANULARITY")
    print("=" * 70)

    gran_results = test_correction_granularity(notes, stems)
    print(f"Per-stem error direction consistency: {100*gran_results['per_stem_consistency']:.1f}%")
    print(f"Recommendation: {gran_results['recommendation']}")

    if gran_results['per_stem_consistency'] > 0.7:
        print("\nPer-stem correction viable - errors tend to go same direction within stem")
    else:
        print("\nPer-note correction needed - error directions vary within stems")

    # 5. Cross-validation
    print()
    print("=" * 70)
    print("CROSS-VALIDATION")
    print("=" * 70)

    cv_results = cross_validate(notes)
    print(f"{'Rule':<35} {'Mean Lift':>10} {'Std':>8} {'Stable':>8}")
    print("-" * 65)

    for r in cv_results['results']:
        stable = "YES" if r['stable'] else "NO"
        print(f"{r['rule']:<35} {r['mean_lift']:>9.2f}x {r['std_lift']:>7.2f} {stable:>8}")

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    total_errors = len(error_notes)
    total_notes = len(notes)
    oracle_gain = 100 * total_errors / total_notes

    print(f"Oracle octave correction potential: +{oracle_gain:.1f}% strict pitch matches")
    print()

    stable_rules = [r for r in cv_results['results'] if r['stable']]
    if stable_rules:
        best = stable_rules[0]
        print(f"Best stable predictor: {best['rule']} ({best['mean_lift']:.1f}x lift)")
    else:
        print("No stable predictors found - audio-driven correction may be unreliable")

    print()
    print(f"Granularity recommendation: {gran_results['recommendation']}")


if __name__ == '__main__':
    main()
