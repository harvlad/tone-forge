"""Melody / accompaniment split for polyphonic stem MIDI.

Why: demucs lumps every non-drums-bass-vocals instrument into the
"other" stem, and the polyphonic extractor dumps ALL of its notes
into one lane. The Jam UI's lead/tab lane then shows chord tones,
arpeggio filler and the actual tune interleaved — "cluttered and
untrue" in user terms. This module separates the two roles so
downstream surfaces can present the melody line and the harmonic
accompaniment honestly.

Pure functions over plain note dicts ``{"pitch", "start", "end",
"velocity"}`` — no audio, no I/O, unit-testable without models.

Algorithm (v1, deliberately simple and ratchetable once note-level
ground truth lands in the corpus):

1. Cluster notes by onset: notes starting within ``onset_window_s``
   of a cluster's anchor onset belong to the same strum/attack.
2. Role by cluster size:
   * 1 note   -> provisional melody
   * 2 notes  -> double-stop: top voice provisional melody, lower
                 note harmony (dyad riffs keep their top line)
   * 3+ notes -> chord strum: every note harmony
3. Register gate: a provisional melody note whose pitch sits more
   than ``register_gate_semitones`` below the local melody median
   (median pitch of provisional melody notes within
   ``+-register_window_s``) is reassigned to harmony. Catches bass
   arpeggios and low fills that are accompaniment even though they
   are played one note at a time.

The gate is computed on the provisional set in one pass (no
fix-point iteration) so results are deterministic.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


__all__ = ["split_melody_accompaniment", "annotate_roles"]


ROLE_MELODY = "melody"
ROLE_HARMONY = "harmony"


def _onset_clusters(
    notes: List[Dict[str, Any]], onset_window_s: float
) -> List[List[Dict[str, Any]]]:
    """Group notes whose onsets fall within one attack window.

    Notes must be pre-sorted by (start, pitch). The window anchors on
    the first note of each cluster, so a slow arpeggio (inter-onset
    gap > window) stays a chain of singletons while a strum (near-
    simultaneous onsets) collapses into one cluster.
    """
    clusters: List[List[Dict[str, Any]]] = []
    for note in notes:
        if clusters and (
            float(note["start"]) - float(clusters[-1][0]["start"])
            <= onset_window_s
        ):
            clusters[-1].append(note)
        else:
            clusters.append([note])
    return clusters


def split_melody_accompaniment(
    notes: List[Dict[str, Any]],
    onset_window_s: float = 0.05,
    register_gate_semitones: int = 12,
    register_window_s: float = 2.0,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split polyphonic notes into (melody, accompaniment).

    Returns the original dict objects (not copies) partitioned into
    two lists, each sorted by (start, pitch). Empty input -> two
    empty lists.
    """
    if not notes:
        return [], []
    ordered = sorted(notes, key=lambda n: (float(n["start"]), int(n["pitch"])))

    melody: List[Dict[str, Any]] = []
    harmony: List[Dict[str, Any]] = []
    for cluster in _onset_clusters(ordered, onset_window_s):
        if len(cluster) == 1:
            melody.append(cluster[0])
        elif len(cluster) == 2:
            low, high = sorted(cluster, key=lambda n: int(n["pitch"]))
            melody.append(high)
            harmony.append(low)
        else:
            harmony.extend(cluster)

    # Register gate over the provisional melody stream.
    if melody:
        kept: List[Dict[str, Any]] = []
        provisional = melody  # single-pass reference set
        for note in provisional:
            t = float(note["start"])
            window_pitches = sorted(
                int(n["pitch"]) for n in provisional
                if abs(float(n["start"]) - t) <= register_window_s
            )
            median = window_pitches[len(window_pitches) // 2]
            if int(note["pitch"]) < median - register_gate_semitones:
                harmony.append(note)
            else:
                kept.append(note)
        melody = kept

    melody.sort(key=lambda n: (float(n["start"]), int(n["pitch"])))
    harmony.sort(key=lambda n: (float(n["start"]), int(n["pitch"])))
    return melody, harmony


def annotate_roles(
    notes: List[Dict[str, Any]],
    onset_window_s: float = 0.05,
    register_gate_semitones: int = 12,
    register_window_s: float = 2.0,
) -> List[Dict[str, Any]]:
    """Return new note dicts with an added ``"role"`` field.

    Additive: every original key is preserved; consumers that ignore
    ``role`` see the exact same lane as before. Order matches the
    input list's (start, pitch) sort.
    """
    melody, harmony = split_melody_accompaniment(
        notes,
        onset_window_s=onset_window_s,
        register_gate_semitones=register_gate_semitones,
        register_window_s=register_window_s,
    )
    melody_ids = {id(n) for n in melody}
    out: List[Dict[str, Any]] = []
    for note in sorted(
        notes, key=lambda n: (float(n["start"]), int(n["pitch"]))
    ):
        role = ROLE_MELODY if id(note) in melody_ids else ROLE_HARMONY
        out.append({**note, "role": role})
    return out
