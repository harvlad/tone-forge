"""Corpus scoreboard runner: chords + sections + key, one command.

Runs the production analysis stack against every corpus fixture and
prints a per-fixture scoreboard plus corpus means. This is the
auto-iterate loop for engine hardening: change the detector, run
this, watch the numbers.

Usage:
    python -m scripts.analysis_eval                 # whole corpus
    python -m scripts.analysis_eval --split train   # tuning split only
    python -m scripts.analysis_eval --json          # machine-readable

Metrics per fixture (blank when the fixture lacks that ground truth):

* chords  — triad-relaxed / strict WCSR vs ``regions``
            (``bench.metrics``, same convention as ``bench.benchmark``)
* sections — MIREX boundary F-measure @0.5s and @3.0s plus
            time-weighted canonical-label accuracy vs ``sections``
            (``bench.section_metrics``)
* key     — MIREX weighted key score vs ``key``

Audio routing matches production: chords see the "other" stem with
the bass stem as the bias lane; sections see the sum of the stems we
have on disk (mix approximation — fixtures store stems, not the mix).

Regression floors: any fixture whose triad-relaxed WCSR lands below
``regression_floor_triad_relaxed`` is flagged and the process exits
non-zero, so this runner doubles as a regression gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Importable regardless of CWD.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from bench.corpus import CorpusFixture, iter_corpus_fixtures  # noqa: E402
from bench.metrics import (  # noqa: E402
    strict_wcsr_score,
    triad_relaxed_wcsr_score,
)
from bench.section_metrics import (  # noqa: E402
    boundary_f_measure,
    key_score,
    section_label_accuracy,
)


__all__ = ["evaluate_fixture", "aggregate", "main"]


# ---------------------------------------------------------------------------
# Audio + prediction plumbing (lazy imports so --help stays fast)
# ---------------------------------------------------------------------------


def _load_audio(path: Path, sr: int = 22050):
    import librosa

    y, sr_out = librosa.load(str(path), sr=sr, mono=True)
    return y, sr_out


def _track_beats(y, sr, backend: str = "librosa"):
    """Best-effort beat track.

    ``librosa`` mirrors bench.corpus._measure_* exactly (production
    parity). ``beat_this`` runs the Beat This! transformer (MIT,
    CPJKU) on the same audio — the A/B lever for measuring how much
    better beats improve downstream chord/section scores.
    """
    import numpy as np

    if backend == "beat_this":
        try:
            import torch
            from beat_this.inference import Audio2Beats

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            a2b = _beat_this_model(device)
            beats, _downbeats = a2b(y.astype(np.float32), sr)
            if len(beats) >= 2:
                intervals = np.diff(beats)
                tempo_val = 60.0 / float(np.median(intervals))
                return tempo_val, np.asarray(beats, dtype=float)
        except Exception as exc:  # fall through to librosa
            sys.stderr.write(f"beat_this failed ({exc}); librosa fallback\n")

    import librosa

    try:
        tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        tempo_val = float(np.asarray(tempo_raw).item())
        if 40 <= tempo_val <= 240 and len(beat_frames) >= 2:
            return tempo_val, librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        pass
    return None, None


_BEAT_THIS_CACHE: Dict[str, Any] = {}


def _beat_this_model(device: str):
    """Load Beat This! once per process (checkpoint ~78 MB)."""
    if device not in _BEAT_THIS_CACHE:
        from beat_this.inference import Audio2Beats

        _BEAT_THIS_CACHE[device] = Audio2Beats(
            checkpoint_path="final0", device=device, dbn=False
        )
    return _BEAT_THIS_CACHE[device]


def _mix_stems(stems):
    """Sum available stems into a mix approximation (peak-normalised)."""
    import numpy as np

    stems = [s for s in stems if s is not None and len(s)]
    if not stems:
        return None
    n = max(len(s) for s in stems)
    mix = np.zeros(n, dtype=np.float32)
    for s in stems:
        mix[: len(s)] += s
    peak = float(np.max(np.abs(mix)))
    if peak > 1.0:
        mix /= peak
    return mix


def evaluate_fixture(
    fixture: CorpusFixture,
    beats_backend: str = "librosa",
    chords_backend: str = "production",
) -> Dict[str, Any]:
    """Run the production detectors on one fixture; return metric row.

    Missing ground truth for an axis -> that axis's keys are absent
    from the row (not 0.0), so corpus means only average over
    fixtures that actually carry that annotation.
    """
    from tone_forge.analysis.chord_detector import detect_chords_from_audio
    from tone_forge.analysis.detector_config import DetectorConfig
    from tone_forge.analysis.sections import SectionDetector

    row: Dict[str, Any] = {
        "name": fixture.name,
        "split": fixture.split,
        "duration_s": fixture.duration_s,
        "floor": fixture.regression_floor_triad_relaxed,
    }

    y, sr = _load_audio(fixture.audio_path)
    bass_y = None
    if fixture.bass_path is not None and fixture.bass_path.exists():
        bass_y, _ = _load_audio(fixture.bass_path, sr=sr)

    tempo, beats_s = _track_beats(y, sr, backend=beats_backend)

    # --- Chords (always present: regions are a required field) ------
    # production: same path bench.corpus uses to pin regression
    # floors (default DetectorConfig) — keeps below_floor comparisons
    # apples-to-apples. btc: vendored BTC-ISMIR19 transformer fed the
    # stem-sum mix (same information set: other + bass), the A/B
    # lever for a chord-model swap. BTC emits no key estimate, so the
    # key axis is skipped on that backend.
    key_info: Dict[str, Any] = {}
    if chords_backend == "btc":
        from tone_forge.analysis.btc_chords import detect_chords_btc

        predicted = detect_chords_btc(_mix_stems([y, bass_y]), sr)
    else:
        predicted = detect_chords_from_audio(
            y, sr, bass_y=bass_y, beats_s=beats_s,
            config=DetectorConfig(), key_out=key_info,
        )
    ref_regions = [
        {"start": s, "end": e, "label": lab} for s, e, lab in fixture.regions
    ]
    row["wcsr_triad"] = triad_relaxed_wcsr_score(
        predicted, ref_regions, fixture.duration_s
    )
    row["wcsr_strict"] = strict_wcsr_score(
        predicted, ref_regions, fixture.duration_s
    )
    row["below_floor"] = row["wcsr_triad"] < fixture.regression_floor_triad_relaxed

    # --- Sections (optional ground truth) ----------------------------
    ref_sections = fixture.metadata.get("sections")
    if isinstance(ref_sections, list) and ref_sections:
        mix = _mix_stems([y, bass_y])
        analysis = SectionDetector().detect_sections(
            mix, sr, tempo=tempo, beats_s=beats_s
        )
        pred_sections = analysis.sections
        _, _, f_strict = boundary_f_measure(
            pred_sections, ref_sections, window_s=0.5
        )
        _, _, f_coarse = boundary_f_measure(
            pred_sections, ref_sections, window_s=3.0
        )
        row["boundary_f_05"] = f_strict
        row["boundary_f_30"] = f_coarse
        row["section_label_acc"] = section_label_accuracy(
            pred_sections, ref_sections, fixture.duration_s
        )

    # --- Key (optional ground truth; chord backend must emit one) ----
    ref_key = fixture.metadata.get("key")
    if isinstance(ref_key, str) and ref_key.strip() and key_info:
        pred_key = str(key_info.get("label", "")) if key_info else ""
        row["key_pred"] = pred_key
        row["key_ref"] = ref_key
        row["key_score"] = key_score(pred_key, ref_key)

    return row


# ---------------------------------------------------------------------------
# Aggregation (pure; unit-tested without audio)
# ---------------------------------------------------------------------------

_MEAN_KEYS = (
    "wcsr_triad",
    "wcsr_strict",
    "boundary_f_05",
    "boundary_f_30",
    "section_label_acc",
    "key_score",
)


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Corpus means per metric, averaging only rows that carry it."""
    summary: Dict[str, Any] = {"n_fixtures": len(rows)}
    for key in _MEAN_KEYS:
        vals = [r[key] for r in rows if key in r]
        summary[f"mean_{key}"] = (sum(vals) / len(vals)) if vals else None
        summary[f"n_{key}"] = len(vals)
    summary["below_floor"] = [r["name"] for r in rows if r.get("below_floor")]
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fmt(v: Optional[float]) -> str:
    return f"{v:.4f}" if isinstance(v, float) else "   -  "


def _print_table(rows: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
    hdr = (
        f"{'fixture':<28s} {'split':<7s} {'triad':>7s} {'strict':>7s} "
        f"{'bF@.5':>7s} {'bF@3':>7s} {'labAcc':>7s} {'key':>7s}  flags"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        flags = "BELOW-FLOOR" if r.get("below_floor") else ""
        print(
            f"{r['name']:<28s} {r['split']:<7s} "
            f"{_fmt(r.get('wcsr_triad')):>7s} {_fmt(r.get('wcsr_strict')):>7s} "
            f"{_fmt(r.get('boundary_f_05')):>7s} {_fmt(r.get('boundary_f_30')):>7s} "
            f"{_fmt(r.get('section_label_acc')):>7s} {_fmt(r.get('key_score')):>7s}"
            f"  {flags}"
        )
    print("-" * len(hdr))
    print(
        f"{'MEAN (' + str(summary['n_fixtures']) + ' fixtures)':<36s} "
        f"{_fmt(summary['mean_wcsr_triad']):>7s} "
        f"{_fmt(summary['mean_wcsr_strict']):>7s} "
        f"{_fmt(summary['mean_boundary_f_05']):>7s} "
        f"{_fmt(summary['mean_boundary_f_30']):>7s} "
        f"{_fmt(summary['mean_section_label_acc']):>7s} "
        f"{_fmt(summary['mean_key_score']):>7s}"
    )
    if summary["below_floor"]:
        print(f"\nREGRESSION: below floor: {', '.join(summary['below_floor'])}")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m scripts.analysis_eval",
        description="Corpus scoreboard: chords + sections + key.",
    )
    p.add_argument("--fixtures-dir", type=Path, default=None)
    p.add_argument(
        "--split",
        choices=("train", "val", "test", "holdout"),
        action="append",
        default=None,
        help="Limit to split(s); repeatable. Default: all.",
    )
    p.add_argument(
        "--only", default=None,
        help="Run a single fixture by name.",
    )
    p.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )
    p.add_argument(
        "--beats",
        choices=("librosa", "beat_this"),
        default="librosa",
        help="Beat-tracker backend feeding chord beat-sync + sections. "
             "librosa = production parity; beat_this = SOTA A/B.",
    )
    p.add_argument(
        "--chords",
        choices=("production", "btc"),
        default="production",
        help="Chord backend. production = in-house detector (floor "
             "parity); btc = vendored BTC-ISMIR19 transformer A/B "
             "(large vocab, MPS). BTC skips the key axis.",
    )
    args = p.parse_args(argv)

    fixtures = iter_corpus_fixtures(
        fixtures_dir=args.fixtures_dir,
        require_audio=True,
        splits=args.split,
    )
    if args.only:
        fixtures = [f for f in fixtures if f.name == args.only]
    if not fixtures:
        sys.stderr.write("no fixtures with audio matched\n")
        return 2

    rows: List[Dict[str, Any]] = []
    for f in fixtures:
        sys.stderr.write(f"evaluating {f.name} ({f.duration_s:.0f}s)...\n")
        rows.append(evaluate_fixture(
            f, beats_backend=args.beats, chords_backend=args.chords,
        ))

    summary = aggregate(rows)

    if args.json:
        print(json.dumps({"fixtures": rows, "summary": summary},
                         indent=2, sort_keys=True))
    else:
        _print_table(rows, summary)

    return 1 if summary["below_floor"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
