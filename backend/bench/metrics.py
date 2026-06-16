"""Metrics layer for the chord-detector benchmark.

Six pure metric functions consumed by ``bench.benchmark`` to build
the per-fixture metric panel. Each accepts a list of predicted
chord regions (any of the shapes ``chord_eval.to_regions`` adapts:
``contracts.Chord``, ``chord_detector.Chord``, dicts, or tuples)
plus a list of reference regions and the song duration in seconds.

Five complement the existing WCSR metrics in
``tone_forge.analysis.chord_eval``:

* ``chord_error_rate``           -- wrong-symbol time mass divided
                                     by song duration
* ``boundary_iou``               -- symmetric IoU of detected vs
                                     reference boundary timelines,
                                     within tolerance ``tol_s``
* ``region_stability``           -- detected label transitions per
                                     minute (over-segmentation proxy)
* ``expected_calibration_error`` -- binned ECE on per-region
                                     ``confidence`` field
* ``triad_relaxed_wcsr_score`` /
  ``strict_wcsr_score``          -- thin wrappers over ``chord_eval``
                                     for a uniform API surface

All metrics are pure functions: no I/O, no logging, no config
state, no side effects. They are deterministic given the same
input.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Tuple

from tone_forge.analysis.chord_eval import (
    normalise_symbol,
    to_regions,
    triad_relaxed_wcsr,
    wcsr,
)


__all__ = [
    "triad_relaxed_wcsr_score",
    "strict_wcsr_score",
    "chord_error_rate",
    "boundary_iou",
    "region_stability",
    "expected_calibration_error",
]


# ---------------------------------------------------------------------------
# WCSR wrappers
# ---------------------------------------------------------------------------


def triad_relaxed_wcsr_score(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
) -> float:
    """Triad-relaxed WCSR, the primary headline score.

    Thin wrapper around ``chord_eval.triad_relaxed_wcsr`` so the
    benchmark surfaces all metrics from a single module.
    """
    return triad_relaxed_wcsr(predicted, reference, duration_s)


def strict_wcsr_score(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
) -> float:
    """Strict-quality WCSR (A and Am are distinct labels).

    Thin wrapper around ``chord_eval.wcsr``; reported alongside the
    triad-relaxed score but NOT gated by the acceptance rule.
    """
    return wcsr(predicted, reference, duration_s)


# ---------------------------------------------------------------------------
# Region adapters (extract (start, end, label, confidence) per item)
# ---------------------------------------------------------------------------


def _to_regions_with_confidence(
    items: Iterable[Any],
) -> List[Tuple[float, float, str, float]]:
    """Like ``chord_eval.to_regions`` but also extracts ``confidence``.

    Confidence defaults to 1.0 when the input shape doesn't expose
    it (e.g. plain ``(start, end, label)`` tuples from a JSON
    ground-truth fixture). Sorts by start time.
    """
    out: List[Tuple[float, float, str, float]] = []
    for it in items:
        if hasattr(it, "confidence"):
            conf = float(it.confidence)
        elif isinstance(it, dict) and "confidence" in it:
            conf = float(it["confidence"])
        else:
            conf = 1.0
        # Reuse the canonical adapter for the (start, end, label) shape.
        (start, end, label), = to_regions([it])
        out.append((start, end, label, conf))
    out.sort(key=lambda r: r[0])
    return out


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Length of overlap between two half-open intervals."""
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


# ---------------------------------------------------------------------------
# Chord Error Rate
# ---------------------------------------------------------------------------


def chord_error_rate(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
) -> float:
    """Time-weighted wrong-symbol rate divided by song duration.

    Complements WCSR by counting wrong-symbol mass directly. For
    every overlap between a predicted region and a reference region
    where the (normalised) labels differ, the overlap duration
    contributes to the error. The result is divided by
    ``duration_s`` (NOT total reference duration, matching the
    WCSR denominator convention).

    Returns 0.0 if ``duration_s <= 0``.

    Note: ``chord_error_rate + strict_wcsr`` does NOT necessarily
    equal 1.0 because the time the predicted regions cover need
    not equal ``duration_s`` (gaps + uncovered ground truth count
    as neither correct nor wrong-symbol).
    """
    if duration_s <= 0:
        return 0.0
    ref = to_regions(reference)
    pred = to_regions(predicted)
    wrong = 0.0
    for r_start, r_end, r_lab in ref:
        try:
            r_norm = normalise_symbol(r_lab)
        except ValueError:
            r_norm = r_lab
        for p_start, p_end, p_lab in pred:
            if p_end <= r_start:
                continue
            if p_start >= r_end:
                break
            ov = _overlap(r_start, r_end, p_start, p_end)
            if ov <= 0:
                continue
            try:
                p_norm = normalise_symbol(p_lab)
            except ValueError:
                p_norm = p_lab
            if p_norm != r_norm:
                wrong += ov
    return wrong / duration_s


# ---------------------------------------------------------------------------
# Boundary IoU
# ---------------------------------------------------------------------------


def _internal_boundaries(regions: List[Tuple[float, float, str]]) -> List[float]:
    """Return the strictly-internal boundary timestamps of ``regions``.

    Endpoints 0 and ``duration`` are excluded; only the boundaries
    between adjacent regions count. ``[(0, 1, A), (1, 2, B)]`` -> ``[1.0]``.
    """
    if len(regions) < 2:
        return []
    return [float(regions[i][1]) for i in range(len(regions) - 1)]


def boundary_iou(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
    tol_s: float = 0.5,
) -> float:
    """Symmetric IoU of detected vs reference boundary timelines.

    A predicted boundary at time ``tp`` matches a reference
    boundary at time ``tr`` iff ``|tp - tr| <= tol_s``. Matches are
    selected greedily from the closest pair first; each reference
    boundary can match at most one predicted boundary and vice
    versa.

        IoU = matches / (n_pred + n_ref - matches)

    Returns 1.0 when both sides have zero internal boundaries (the
    trivial "single chord everywhere" case is treated as a perfect
    match by convention; both sides agree there is no internal
    structure).
    """
    if duration_s <= 0:
        return 0.0
    if tol_s < 0:
        raise ValueError("tol_s must be >= 0")
    ref = to_regions(reference)
    pred = to_regions(predicted)
    pred_b = _internal_boundaries(pred)
    ref_b = _internal_boundaries(ref)
    if not pred_b and not ref_b:
        return 1.0
    # Greedy bipartite match by ascending distance.
    pairs = sorted(
        (
            (abs(p - r), i, j)
            for i, p in enumerate(pred_b)
            for j, r in enumerate(ref_b)
            if abs(p - r) <= tol_s
        )
    )
    used_pred: set = set()
    used_ref: set = set()
    matches = 0
    for _, i, j in pairs:
        if i in used_pred or j in used_ref:
            continue
        used_pred.add(i)
        used_ref.add(j)
        matches += 1
    union = len(pred_b) + len(ref_b) - matches
    if union <= 0:
        return 1.0
    return matches / union


# ---------------------------------------------------------------------------
# Region Stability
# ---------------------------------------------------------------------------


def region_stability(
    predicted: Iterable[Any],
    duration_s: float,
) -> float:
    """Detected chord-label transitions per minute.

    A "transition" is an adjacent pair of predicted regions whose
    normalised labels differ. This is a proxy for over-segmentation:
    a stable detector that emits one chord per beat on a 4-beat
    progression at 120 BPM produces ~120 transitions/minute, while
    a clean detector that merges identical adjacent regions produces
    closer to the song's actual chord-change rate (e.g. 8-30/min for
    typical pop/rock).

    Returns 0.0 if ``duration_s <= 0`` or there are fewer than two
    predicted regions.
    """
    if duration_s <= 0:
        return 0.0
    pred = to_regions(predicted)
    if len(pred) < 2:
        return 0.0
    transitions = 0
    for i in range(1, len(pred)):
        try:
            prev = normalise_symbol(pred[i - 1][2])
            curr = normalise_symbol(pred[i][2])
        except ValueError:
            prev = pred[i - 1][2]
            curr = pred[i][2]
        if prev != curr:
            transitions += 1
    return transitions * 60.0 / duration_s


# ---------------------------------------------------------------------------
# Expected Calibration Error
# ---------------------------------------------------------------------------


def expected_calibration_error(
    predicted: Iterable[Any],
    reference: Iterable[Any],
    duration_s: float,
    bins: int = 10,
) -> float:
    """Time-weighted Expected Calibration Error.

    Each predicted region carries a ``confidence`` in [0, 1]. We
    bin predicted regions by their confidence into ``bins`` equal-
    width buckets over [0, 1]. For each non-empty bucket:

      * empirical_accuracy_b = (predicted time in bucket whose
                                normalised label matches the
                                concurrent reference label) /
                               (total predicted time in bucket)
      * mean_confidence_b    = (duration-weighted mean of the
                                predicted regions' confidences in
                                bucket)

    ECE is the duration-weighted mean absolute gap:

      ECE = sum_b (time_b / total_pred_time) *
            |empirical_accuracy_b - mean_confidence_b|

    Zero is perfect calibration. Returns 0.0 if no predicted regions
    expose a confidence value (every region's confidence defaulted
    to 1.0 from a plain-tuple shape) AND no predicted time exists,
    i.e. there is nothing to bin.
    """
    if duration_s <= 0:
        return 0.0
    if bins <= 0:
        raise ValueError("bins must be > 0")
    pred = _to_regions_with_confidence(predicted)
    ref = to_regions(reference)
    if not pred:
        return 0.0

    # Pre-bin every predicted region. For each, compute the
    # duration that matches reference (strict normalised equality)
    # to populate the numerator of empirical_accuracy_b.
    bin_time = [0.0] * bins
    bin_correct = [0.0] * bins
    bin_conf_mass = [0.0] * bins  # sum of conf * duration, for mean

    def _which_bin(c: float) -> int:
        # Clamp to [0, 1); ``confidence == 1.0`` lands in the last bin.
        if c >= 1.0:
            return bins - 1
        if c < 0.0:
            return 0
        return min(bins - 1, int(c * bins))

    for p_start, p_end, p_lab, conf in pred:
        dur = max(0.0, p_end - p_start)
        if dur <= 0:
            continue
        b = _which_bin(conf)
        bin_time[b] += dur
        bin_conf_mass[b] += conf * dur
        try:
            p_norm = normalise_symbol(p_lab)
        except ValueError:
            p_norm = p_lab
        # Sum overlap with reference regions whose label matches.
        for r_start, r_end, r_lab in ref:
            if r_end <= p_start:
                continue
            if r_start >= p_end:
                break
            ov = _overlap(p_start, p_end, r_start, r_end)
            if ov <= 0:
                continue
            try:
                r_norm = normalise_symbol(r_lab)
            except ValueError:
                r_norm = r_lab
            if r_norm == p_norm:
                bin_correct[b] += ov

    total = sum(bin_time)
    if total <= 0:
        return 0.0
    ece = 0.0
    for b in range(bins):
        t = bin_time[b]
        if t <= 0:
            continue
        acc = bin_correct[b] / t
        mean_conf = bin_conf_mass[b] / t
        ece += (t / total) * abs(acc - mean_conf)
    return ece
