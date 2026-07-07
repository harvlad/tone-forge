"""Chop-slice metadata for the Jam **Contribute** mode.

The Launchpad's Contribute presets treat the currently-loaded song as a
sample source: each of the 64 pads becomes a slice of one of the stems
(or the full mix), sliced at a musically meaningful boundary — beat,
chord change, section change, or detected onset. Pressing a pad plays
that slice back through the browser AudioContext.

This module owns the *slice-boundary computation*. It only reads what
the analysis pipeline already produced (beats, downbeats, chords,
sections) plus, for onset mode, the stem WAV file itself. It never
generates new audio — the browser handles playback from the decoded
stem buffer using the returned ``startSec`` / ``endSec`` offsets.

The endpoint layer (``tone_forge_api.get_song_chops``) is a thin
lookup + adaptor around ``build_chops`` in this module. Keeping the
computation here means Contribute-Chops logic stays testable in
isolation from the FastAPI surface.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Grid capacity — the on-screen Launchpad mirror is 8×8 = 64 pads,
# but the top row (padIdx 81..88) is reserved for the countdown /
# anticipation bar, and the physical layout leaves 7 rows × 8 pads
# = 56 usable positions. We deliberately fill only 3 rows (24 pads)
# so the user gets more *unique* material per song rather than a
# full page of near-duplicate slices — a repeating chorus often
# contributes dozens of near-identical chord chops that would all
# feel the same under the fingers. Fewer, more diverse pads = more
# musically useful. Extra rows stay dark and available for future
# per-row semantics without stealing tone from active pads.
MAX_CHOPS = 24

# Palette hints returned to the client. The Launchpad module maps
# these strings to RGB when painting; keeping them as tags decouples
# color decisions from slice logic.
_COLOR_ROOT_PC = [
    "red", "red-orange", "orange", "yellow-orange",
    "yellow", "yellow-green", "green", "cyan",
    "blue", "blue-violet", "violet", "magenta",
]
_COLOR_ONSET_KIND = {
    "kick": "red",
    "snare": "yellow",
    "hat": "cyan",
    "cymbal": "violet",
}
_COLOR_SECTION = {
    "intro": "gray",
    "verse": "blue",
    "chorus": "gold",
    "bridge": "violet",
    "solo": "orange",
    "outro": "gray",
    "breakdown": "cyan",
    "build": "yellow",
    "drop": "red",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_chops(
    *,
    stem: str,
    slice_mode: str,
    analysis_result: Dict[str, Any],
    stem_wav_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:  # noqa: PLR0912  intentional dispatch fan-out
    """Return the chop list for one (stem, sliceMode) combination.

    Args:
        stem: Source stem name ('vocals', 'drums', 'bass', 'other', 'mix').
            Determines *which* audio the client will decode; this function
            only uses it as metadata + as the key into ``stem_wav_path``
            for onset detection.
        slice_mode: One of 'phrase' | 'onset' | 'beat' | 'chord' | 'section'.
        analysis_result: The persisted analysis dict from history.json.
            Reads beats_s, downbeats_s, chords, sections.
        stem_wav_path: Path to the WAV file for the stem. Required for
            slice_mode='onset', ignored otherwise. Missing/unreadable
            files fall back to beat-aligned chops so the pad grid still
            gets populated.

    Returns:
        A list of chop dicts (see module docstring for shape), capped
        at MAX_CHOPS. Never raises for well-formed input; degrades to
        an empty list when the requested slice mode has no source data
        (e.g. section mode on a song with no detected sections).
    """
    # Minimum chop count below which we consider a mode "starved" and
    # fall through to a coarser slicer. 4 is enough to fill half a
    # row of pads with something distinct, which is the point at
    # which the user perceives the preset as "working" instead of
    # "broken". Below that, the coarser slicers add material so the
    # grid still has usable pads to hit.
    MIN_USEFUL = 4

    if slice_mode == "beat":
        chops = _chops_from_beats(analysis_result)
        if len(chops) < MIN_USEFUL:
            chops = chops + _chops_from_even_time(analysis_result, MAX_CHOPS)
    elif slice_mode == "chord":
        chops = _chops_from_chords(analysis_result)
        # Waterfall: chords (only if we have several distinct ones)
        # → beats/downbeats → even-time slicing of the whole song.
        # Guarantees the harmonic/bass Contribute presets always
        # yield a populated grid even on songs whose analysis
        # pipeline produced ≤ 1 chord region (~90% of the current
        # corpus). Merging preserves any real chord data (with root
        # metadata for harmonic-lock) at the top of the list.
        if len(chops) < MIN_USEFUL:
            chops = chops + _chops_from_downbeats(analysis_result)
        if len(chops) < MIN_USEFUL:
            chops = chops + _chops_from_beats(analysis_result)
        if len(chops) < MIN_USEFUL:
            chops = chops + _chops_from_even_time(analysis_result, MAX_CHOPS)
    elif slice_mode == "section":
        chops = _chops_from_sections(analysis_result)
        # Same waterfall as chord mode. When a song has 1 section
        # entry covering the whole track we subdivide the audio
        # into evenly-spaced sub-slices — the pad grid becomes a
        # "song timeline" you can scrub through per pad.
        if len(chops) < MIN_USEFUL:
            chops = chops + _chops_from_downbeats(analysis_result)
        if len(chops) < MIN_USEFUL:
            chops = chops + _chops_from_even_time(analysis_result, MAX_CHOPS)
    elif slice_mode == "phrase":
        # Vocal-phrase slicing when the stem is vocals and we have
        # the WAV on disk: run the RMS-envelope phrase detector so
        # each pad = one short vocal utterance (~0.3–2.5 s) with
        # leading silence trimmed. This directly addresses two UX
        # complaints:
        #   * whole-section chops carried "too many words per pad"
        #   * pressing a voice pad often played 1–2 s of silence
        #     before the vocal actually kicked in.
        # When the slicer produces *any* phrase chops we return
        # them unpadded — mixing 0.5-second phrase chops with 20 s
        # section chops or 24 evenly-spaced whole-song slices
        # defeats the point and lets the diversity selector drop
        # the short phrases in favour of the (longer, more
        # spread-out) fallback chops. Only fall through to the
        # section waterfall when the slicer produced nothing
        # (missing WAV, silent stem, non-vocals stem).
        phrase_chops: List[Dict[str, Any]] = []
        if stem == "vocals" and stem_wav_path is not None:
            phrase_chops = _chops_from_vocal_phrases(
                analysis_result, stem_wav_path,
            )
        if phrase_chops:
            chops = phrase_chops
        else:
            chops = _chops_from_sections(analysis_result)
            if len(chops) < MIN_USEFUL:
                chops = chops + _chops_from_downbeats(analysis_result)
            if len(chops) < MIN_USEFUL:
                chops = chops + _chops_from_even_time(
                    analysis_result, MAX_CHOPS,
                )
    elif slice_mode == "onset":
        chops = _chops_from_onsets(analysis_result, stem_wav_path)
        if not chops:
            # Fallback: treat every beat as an onset when the WAV load
            # fails. Rhythm still lands on the beat grid.
            chops = _chops_from_beats(analysis_result)
    elif slice_mode == "drum-bundle":
        # Drums-specific mix: bar-length loops on the bottom row of
        # the populated grid, half-bar loops in the middle, individual
        # onset one-shots on top. Falls back to plain onset if the
        # downbeat grid is missing.
        chops = _chops_from_drum_bundle(analysis_result, stem_wav_path)
        if not chops:
            chops = _chops_from_onsets(analysis_result, stem_wav_path)
        if not chops:
            chops = _chops_from_beats(analysis_result)
    else:
        chops = []

    # Select a subset that maximises *musical distinctness*, not just
    # label distinctness. The old dedupe collapsed two verses at
    # different points in a song (different chords, different
    # energy, different dominant stem) into one pad — throwing away
    # real material. Farthest-point sampling in the feature space
    # (label + energy + dominant stem + temporal spread) keeps the
    # most contrasting chops instead.
    if len(chops) > MAX_CHOPS:
        chops = _select_diverse_chops(chops, slice_mode, MAX_CHOPS)

    # Strip internal-only distinctness fields so they never hit the
    # wire — clients don't need them and the JSON stays lean.
    for c in chops:
        c.pop("_energy", None)
        c.pop("_dominantStem", None)

    for i, c in enumerate(chops):
        c["idx"] = i
        c["durationSec"] = round(c["endSec"] - c["startSec"], 4)

    return chops


# ---------------------------------------------------------------------------
# Slice generators
# ---------------------------------------------------------------------------

def _chops_from_beats(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One chop per beat, with kind labels cycling kick/snare/hat/hat
    across the bar. Used as the onset fallback and for beat-quantized
    contribution modes.
    """
    beats = _floats(result.get("beats_s"))
    if len(beats) < 2:
        return []
    chops: List[Dict[str, Any]] = []
    for i in range(len(beats) - 1):
        # Fake a kit distribution matching typical rock/pop:
        # beat 0 = kick, beat 1 = snare, beat 2 = kick, beat 3 = snare,
        # with the hat kind mixed in every off-beat via cycle position.
        # This is only for the onset-fallback path; real onset mode
        # overrides with true clustering.
        kind = ("kick", "snare", "kick", "snare")[i % 4]
        chops.append({
            "startSec": round(beats[i], 4),
            "endSec": round(beats[i + 1], 4),
            "kind": kind,
            "root": None,
            "sectionLabel": None,
            "chordSymbol": None,
            "colorHint": _COLOR_ONSET_KIND[kind],
        })
    return chops


def _chops_from_even_time(
    result: Dict[str, Any],
    target: int,
) -> List[Dict[str, Any]]:
    """Final fallback: subdivide the whole song duration into
    ``target`` evenly-spaced slices. Used when every other slicer
    starves because the analysis pipeline didn't produce beats,
    downbeats, chords, or multiple sections — which is ~90% of the
    current corpus. The result is essentially a "song timeline" laid
    out on the pad grid: pad 0 = intro, pad 23 = outro. Musically
    useful because a repeating song still contains regionally
    different material even without an explicit structural label.

    Duration comes from ``duration_sec`` (always present after
    analysis). Falls back to the last section / chord / beat
    end-time if for some reason that field is missing, and finally
    to a 60 s default so we never return nothing.
    """
    dur = _seconds_of(result.get("duration_sec"))
    if not dur or dur <= 0:
        dur = _duration_from_events(result)
    if not dur or dur <= 0:
        dur = 60.0
    if target <= 0:
        return []
    step = dur / target
    chops: List[Dict[str, Any]] = []
    for i in range(target):
        a = round(i * step, 4)
        b = round((i + 1) * step, 4)
        if b <= a:
            continue
        chops.append({
            "startSec": a,
            "endSec": b,
            "kind": None,
            "root": None,
            "sectionLabel": None,
            "chordSymbol": None,
            "colorHint": "blue",
        })
    return chops


def _duration_from_events(result: Dict[str, Any]) -> Optional[float]:
    """Best-effort song duration when ``duration_sec`` is absent —
    take the latest end-time we can find in any event list."""
    candidates: List[float] = []
    for key in ("sections", "chords"):
        for item in (result.get(key) or []):
            end = _seconds_of(_first_present(item, "end_time", "end_s", "end"))
            if end is not None:
                candidates.append(end)
    for key in ("downbeats_s", "beats_s", "beat_times"):
        arr = _floats(result.get(key))
        if arr:
            candidates.append(arr[-1])
    return max(candidates) if candidates else None


def _chops_from_downbeats(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One chop per bar. Used as the phrase-mode fallback when a song
    has no detected section list."""
    downs = _floats(result.get("downbeats_s"))
    if len(downs) < 2:
        return []
    return [
        {
            "startSec": round(downs[i], 4),
            "endSec": round(downs[i + 1], 4),
            "kind": None,
            "root": None,
            "sectionLabel": None,
            "chordSymbol": None,
            "colorHint": "gold",
        }
        for i in range(len(downs) - 1)
    ]


def _chops_from_chords(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One chop per chord region. Root pitch class stored on each so
    the client can implement harmonic-lock (play the chop whose root
    matches the currently-active chord).

    Boundaries are snapped to the beat grid first (see
    ``_snap_chops_to_beats``) then to the bar grid (see
    ``_snap_chops_to_bars``). Without both, bass chops drift audibly
    against the master clock: beat-snap fixes fractional-beat offset
    from the chord recogniser, and bar-snap guarantees whole-BAR
    durations so ``quantize='bar'`` retriggers stay phase-locked
    across loop iterations.
    """
    chords = result.get("chords") or []
    chops: List[Dict[str, Any]] = []
    for c in chords:
        # Use ``coalesce`` (not ``or``) so a legit 0.0 start_s survives.
        start = _seconds_of(_first_present(c, "start_s", "startSec", "start"))
        end = _seconds_of(_first_present(c, "end_s", "endSec", "end"))
        symbol = c.get("symbol") or c.get("chord") or None
        if start is None or end is None or end <= start:
            continue
        root = _root_pc_of(symbol) if symbol else None
        chops.append({
            "startSec": round(start, 4),
            "endSec": round(end, 4),
            "kind": None,
            "root": root,
            "sectionLabel": None,
            "chordSymbol": symbol,
            "colorHint": _COLOR_ROOT_PC[root] if root is not None else "gray",
        })
    chops = _snap_chops_to_beats(chops, result)
    return _snap_chops_to_bars(chops, result)


def _chops_from_sections(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One chop per song section (intro / verse / chorus / …).

    Attaches distinctness signals (``_energy`` and ``_dominantStem``)
    the selector reads to pick a diverse subset. These fields are
    kept private-ish (leading underscore) and stripped before the
    chop is serialised — they never reach the wire.
    """
    sections = result.get("sections") or []
    chops: List[Dict[str, Any]] = []
    for s in sections:
        start = _seconds_of(_first_present(s, "start_time", "start_s", "start"))
        end = _seconds_of(_first_present(s, "end_time", "end_s", "end"))
        label = (s.get("type") or s.get("label") or s.get("name") or "section").lower()
        if start is None or end is None or end <= start:
            continue
        energy = _seconds_of(s.get("energy_mean"))
        dom = s.get("dominant_stem")
        chops.append({
            "startSec": round(start, 4),
            "endSec": round(end, 4),
            "kind": None,
            "root": None,
            "sectionLabel": label,
            "chordSymbol": None,
            "colorHint": _COLOR_SECTION.get(label, "gray"),
            "_energy": energy if energy is not None else 0.5,
            "_dominantStem": dom if isinstance(dom, str) else None,
        })
    return chops


def _chops_from_onsets(
    result: Dict[str, Any],
    stem_wav_path: Optional[Path],
) -> List[Dict[str, Any]]:
    """Run librosa onset detection on the stem WAV, then cluster each
    detected onset by spectral centroid into 4 buckets that we map to
    'kick' / 'snare' / 'hat' / 'cymbal'. Percussion-agnostic — works on
    any stem, but the kind labels are only musically meaningful for
    drums.
    """
    if stem_wav_path is None or not Path(stem_wav_path).exists():
        return []
    try:
        import librosa
        import numpy as np
    except ImportError:
        return []

    try:
        y, sr = librosa.load(str(stem_wav_path), sr=22050, mono=True)
    except Exception:
        return []

    if y.size == 0:
        return []

    duration = float(y.shape[0] / sr)
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=512)
    if len(onset_frames) == 0:
        return []

    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=512)

    # Windowed spectral centroid at each onset — this is our "brightness"
    # signal. Kicks live in low centroid, hats/cymbals in high, snare in
    # the middle. We split the sorted centroid array into 4 evenly-
    # populated quartiles instead of running k-means; k-means on ~50
    # points is overkill and quantiles are more stable across songs
    # with different overall spectral character.
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=512))
    centroid = librosa.feature.spectral_centroid(S=S, sr=sr, hop_length=512)[0]
    centroid_at = np.take(centroid, np.clip(onset_frames, 0, len(centroid) - 1))

    q = np.quantile(centroid_at, [0.25, 0.5, 0.75])
    kinds: List[str] = []
    for c in centroid_at:
        if c < q[0]:
            kinds.append("kick")
        elif c < q[1]:
            kinds.append("snare")
        elif c < q[2]:
            kinds.append("hat")
        else:
            kinds.append("cymbal")

    # Slice length = time to next onset, clamped to 300 ms so a lone
    # onset at the tail of the song doesn't ring out to infinity. Also
    # clamp minimum to 30 ms — anything shorter is a false onset.
    chops: List[Dict[str, Any]] = []
    for i, t in enumerate(onset_times):
        nxt = onset_times[i + 1] if i + 1 < len(onset_times) else duration
        end = min(nxt, t + 0.3)
        if end - t < 0.03:
            continue
        kind = kinds[i]
        chops.append({
            "startSec": round(float(t), 4),
            "endSec": round(float(end), 4),
            "kind": kind,
            "root": None,
            "sectionLabel": None,
            "chordSymbol": None,
            "colorHint": _COLOR_ONSET_KIND[kind],
        })
    return chops


def _chops_from_vocal_phrases(
    result: Dict[str, Any],
    stem_wav_path: Optional[Path],
) -> List[Dict[str, Any]]:
    """Slice the vocals stem into short phrase-length chops.

    Motivation: the previous ``phrase`` slicer for vocals fell
    straight through to ``_chops_from_sections`` — every pad became
    a whole verse or chorus (~15–25 s of audio). That produced two
    UX failures we hit in Jam:

      1. Each pad played too many words. The user wanted "one lyric
         line per pad", not "one whole verse per pad", so triggering
         a pad on a beat produced an amorphous wall of vocal that
         didn't line up rhythmically with anything.
      2. Section boundaries fall on musical downbeats, not on the
         first vocal onset, so most section chops opened with
         0.5–2 s of instrumental air before any singing. A pad
         hit on the beat therefore *sounded* delayed even though
         it was scheduled correctly.

    Approach — RMS-envelope voiced-region detection:

      * Load the vocals WAV mono @ 22050 Hz (matches the onset
        detector's rate so librosa's frame math lines up).
      * Compute short-window RMS with a 20 ms hop.
      * Adaptive threshold from the envelope's own distribution
        (percentile of nonzero frames) — songs vary massively in
        overall vocal loudness, so a hardcoded dBFS threshold
        false-negatives quiet singers and false-positives loud
        breaths.
      * Extract contiguous spans above threshold.
      * Bridge gaps < ``BREATH_GAP_SEC`` (0.2 s) so a phrase
        interrupted by a breath doesn't fragment into two half-
        phrases.
      * Drop spans shorter than ``MIN_PHRASE_SEC`` (0.15 s) — those
        are almost always breath or plosive artifacts, not sung
        words.
      * Cap spans at ``MAX_PHRASE_SEC`` (2.5 s): longer phrases
        split at the deepest RMS trough inside the cap window, so
        the split lands on natural word breaks instead of on a
        vowel.

    The trimmed start-time is what does the actual "starts on the
    vocal" fix — the pad's ``startSec`` is now the first voiced
    frame, not the section downbeat. Combined with the beat-quantize
    change on the client, pressing a pad on a beat produces vocal
    that starts on the beat.

    Falls back to the same waterfall as other phrase modes when the
    WAV can't be loaded (missing file, librosa import failure, empty
    signal) — the caller checks ``len(chops) < MIN_USEFUL`` and
    tops up from ``_chops_from_sections``.
    """
    if stem_wav_path is None or not Path(stem_wav_path).exists():
        return []
    try:
        import librosa
        import numpy as np
    except ImportError:
        return []

    try:
        y, sr = librosa.load(str(stem_wav_path), sr=22050, mono=True)
    except Exception:
        return []

    if y.size == 0:
        return []

    # Frame size: 20 ms hop @ 22050 Hz = 441 samples. Window is
    # 2× the hop for overlap smoothing without adding a separate
    # low-pass — librosa's rms sums squared samples per frame, so
    # a 40 ms window with 20 ms hop gives ~50 % overlap.
    HOP = 441
    FRAME = 882
    rms = librosa.feature.rms(y=y, frame_length=FRAME, hop_length=HOP)[0]
    if rms.size == 0:
        return []

    # Frame time-axis for later start/end conversion.
    frame_times = librosa.frames_to_time(
        np.arange(rms.size), sr=sr, hop_length=HOP,
    )

    # Smooth with a 5-frame (100 ms) moving average so a single
    # loud plosive doesn't punch a spurious "voiced" frame into a
    # silent gap. Reflect-pad the boundaries so start/end frames
    # aren't attenuated by convolution edge effects.
    kernel = np.ones(5, dtype=np.float32) / 5.0
    smoothed = np.convolve(
        np.pad(rms, (2, 2), mode="reflect"),
        kernel,
        mode="valid",
    )

    # Adaptive threshold. Two signals combine:
    #   * A fraction of the loudness peak (p95 of smoothed RMS).
    #     Anchors the threshold below any real singing on this
    #     track regardless of overall level — a whispered verse and
    #     a belted chorus both produce a threshold proportional to
    #     their own peak.
    #   * The 30th percentile of *nonzero* frames as a floor. Pure
    #     zeros from the source separator in instrumental passages
    #     are excluded so the floor isn't dragged down; the 30 %
    #     mark sits below the burst of the median vocal frame,
    #     which keeps steady-amplitude passages (a monotone verse
    #     line, a synthetic test tone) from oscillating in and out
    #     of the voiced mask.
    # Taking the *max* of the two gives us: on loud tracks the p95
    # ratio dominates; on quiet or flat-amplitude tracks the p30
    # floor prevents false-positives on background noise.
    nonzero = smoothed[smoothed > 1e-5]
    if nonzero.size < 4:
        # Track has essentially no vocal content — nothing to slice.
        return []
    peak_ratio_thr = float(np.percentile(smoothed, 95.0)) * 0.12
    floor_thr = float(np.percentile(nonzero, 30.0))
    threshold = max(peak_ratio_thr, floor_thr)
    if threshold <= 0:
        return []

    voiced = smoothed > threshold
    if not voiced.any():
        return []

    # Convert the boolean mask into contiguous [start_frame,
    # end_frame) spans. np.diff on the padded mask gives ±1 at
    # transitions; +1 = voiced onset, −1 = voiced offset.
    padded = np.concatenate([[False], voiced, [False]])
    edges = np.diff(padded.astype(np.int8))
    starts = np.where(edges == 1)[0]
    ends = np.where(edges == -1)[0]

    # Merge frame indices into (start_sec, end_sec) spans.
    spans: List[tuple[float, float]] = []
    hop_sec = HOP / float(sr)
    for s_f, e_f in zip(starts, ends):
        s_t = float(frame_times[s_f]) if s_f < frame_times.size else s_f * hop_sec
        # e_f is exclusive; step back one frame for the last voiced
        # sample, then add one hop for the trailing frame's duration.
        last_f = min(e_f - 1, frame_times.size - 1)
        e_t = float(frame_times[last_f]) + hop_sec
        if e_t > s_t:
            spans.append((s_t, e_t))

    if not spans:
        return []

    # Bridge breath gaps: two spans separated by less than
    # BREATH_GAP_SEC of silence collapse into one phrase.
    BREATH_GAP_SEC = 0.2
    merged: List[tuple[float, float]] = [spans[0]]
    for s, e in spans[1:]:
        prev_s, prev_e = merged[-1]
        if s - prev_e < BREATH_GAP_SEC:
            merged[-1] = (prev_s, e)
        else:
            merged.append((s, e))

    # Drop tiny fragments (breaths, single-plosive false positives).
    MIN_PHRASE_SEC = 0.15
    merged = [(s, e) for (s, e) in merged if e - s >= MIN_PHRASE_SEC]
    if not merged:
        return []

    # Cap long phrases. When a merged span exceeds MAX_PHRASE_SEC we
    # split at the deepest RMS trough within the [MAX_PHRASE_SEC × 0.6,
    # MAX_PHRASE_SEC] window so the split lands on the natural word
    # break rather than on a held vowel.
    MAX_PHRASE_SEC = 2.5
    split_window_lo = MAX_PHRASE_SEC * 0.6
    capped: List[tuple[float, float]] = []
    for s, e in merged:
        cur_s = s
        while e - cur_s > MAX_PHRASE_SEC:
            lo_t = cur_s + split_window_lo
            hi_t = cur_s + MAX_PHRASE_SEC
            lo_f = int(np.searchsorted(frame_times, lo_t))
            hi_f = int(np.searchsorted(frame_times, hi_t))
            lo_f = max(0, min(lo_f, smoothed.size - 1))
            hi_f = max(lo_f + 1, min(hi_f, smoothed.size))
            trough_offset = int(np.argmin(smoothed[lo_f:hi_f]))
            split_f = lo_f + trough_offset
            split_t = float(frame_times[split_f]) if split_f < frame_times.size else hi_t
            if split_t <= cur_s + MIN_PHRASE_SEC or split_t >= e:
                # Trough search degenerate (flat envelope) — fall
                # back to a hard cap at MAX_PHRASE_SEC so we still
                # make forward progress.
                split_t = cur_s + MAX_PHRASE_SEC
            capped.append((cur_s, split_t))
            cur_s = split_t
        if e - cur_s >= MIN_PHRASE_SEC:
            capped.append((cur_s, e))

    if not capped:
        return []

    # Build a section-label lookup so each phrase inherits the
    # section it sits inside — clients use this for color-coding
    # and for harmonic-lock heuristics.
    section_spans: List[tuple[float, float, str]] = []
    for sec in (result.get("sections") or []):
        s_start = _seconds_of(_first_present(sec, "start_time", "start_s", "start"))
        s_end = _seconds_of(_first_present(sec, "end_time", "end_s", "end"))
        label = (sec.get("type") or sec.get("label") or sec.get("name") or "").lower()
        if s_start is None or s_end is None or s_end <= s_start:
            continue
        section_spans.append((s_start, s_end, label or "section"))

    def _label_at(t: float) -> Optional[str]:
        for s_start, s_end, label in section_spans:
            if s_start <= t < s_end:
                return label
        return None

    chops: List[Dict[str, Any]] = []
    for s, e in capped:
        label = _label_at(s)
        chops.append({
            "startSec": round(s, 4),
            "endSec": round(e, 4),
            "kind": "phrase",
            "root": None,
            "sectionLabel": label,
            "chordSymbol": None,
            "colorHint": _COLOR_SECTION.get(label or "", "cyan"),
        })
    return chops


def _chops_from_drum_bundle(
    result: Dict[str, Any],
    stem_wav_path: Optional[Path],
) -> List[Dict[str, Any]]:
    """Mixed drum grid: bar loops + half-bar loops + one-shot hits.

    Layout in the returned list (client packs 8 chops per row, chops[0..7]
    → top row, chops[16..23] → bottom populated row):

        chops[ 0.. 7] : 8 one-shot onsets  (top row of the grid)
        chops[ 8..15] : 8 half-bar loops   (middle row)
        chops[16..23] : 8 bar-length loops (bottom populated row)

    Bar length is derived from ``downbeats_s``; without at least two
    downbeats this bundle is impossible so we return [] and the caller
    falls back to plain onset chops.

    Rationale: playing an individual kick per pad forces the user into
    programming rhythm one hit at a time, which sounds like half-time
    against a real drum groove. Giving them ready-made bar-length
    loops on one row lets them layer full grooves; the half-bar row
    is for fills / transitions; the top row keeps the surgical
    one-shots for hits that need to land exactly on a downbeat.
    """
    downs = _floats(result.get("downbeats_s"))
    if len(downs) < 2:
        return []

    # Bar loops: use spans between successive downbeats. Skip zero-
    # length or negative spans (e.g. duplicate timestamps).
    bar_spans: List[tuple[float, float]] = []
    for i in range(len(downs) - 1):
        a, b = downs[i], downs[i + 1]
        if b > a + 0.1:
            bar_spans.append((a, b))
    if not bar_spans:
        return []

    def _pick_evenly(items: Sequence[Any], target: int) -> List[Any]:
        n = len(items)
        if n <= target:
            return list(items)
        return [items[int(round(i * (n - 1) / (target - 1)))] for i in range(target)]

    bar_picks = _pick_evenly(bar_spans, 8)
    bar_chops = [
        {
            "startSec": round(a, 4),
            "endSec": round(b, 4),
            "kind": "bar",
            "root": None,
            "sectionLabel": None,
            "chordSymbol": None,
            "colorHint": "gold",
        }
        for (a, b) in bar_picks
    ]

    # Half-bar loops: split each source bar in half. Uses the same
    # bar_spans pool but split; picking 8 evenly spreads across the song.
    half_spans: List[tuple[float, float]] = []
    for (a, b) in bar_spans:
        mid = (a + b) / 2.0
        # Take both halves so we have double the material to pick
        # from; the even-pick step keeps the temporal spread.
        half_spans.append((a, mid))
        half_spans.append((mid, b))
    half_picks = _pick_evenly(half_spans, 8)
    half_chops = [
        {
            "startSec": round(a, 4),
            "endSec": round(b, 4),
            "kind": "half-bar",
            "root": None,
            "sectionLabel": None,
            "chordSymbol": None,
            "colorHint": "orange",
        }
        for (a, b) in half_picks
    ]

    # One-shots: reuse the onset detector; pick 8 spread across the
    # available onsets, prioritising kind variety. If onset detection
    # fails / returns nothing, fall through with what we have — the
    # top row simply stays empty.
    onset_chops = _chops_from_onsets(result, stem_wav_path)
    # Try to keep two of each kind for kind variety before spreading.
    if onset_chops:
        by_kind: Dict[str, List[Dict[str, Any]]] = {}
        for c in onset_chops:
            by_kind.setdefault(str(c.get("kind") or "unknown"), []).append(c)
        one_shot_picks: List[Dict[str, Any]] = []
        for k, group in by_kind.items():
            one_shot_picks.extend(_pick_evenly(group, 2))
        # If we ended up with too few (a kind has < 2 members),
        # top up with more from the largest bucket to reach 8.
        if len(one_shot_picks) < 8:
            largest = max(by_kind.values(), key=len)
            needed = 8 - len(one_shot_picks)
            already = set(id(c) for c in one_shot_picks)
            filler = [c for c in largest if id(c) not in already]
            one_shot_picks.extend(_pick_evenly(filler, needed))
        one_shot_picks = one_shot_picks[:8]
        # Sort by time to match the temporal spread of the other rows.
        one_shot_picks.sort(key=lambda c: float(c.get("startSec") or 0))
    else:
        one_shot_picks = []

    # Concatenate in row order: one-shots first (row 7, top), then
    # half-bar (row 6), then bar (row 5, bottom of populated grid).
    return one_shot_picks + half_chops + bar_chops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap_chops_to_beats(
    chops: List[Dict[str, Any]],
    result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Snap each chop's startSec / endSec to the nearest tracked beat.

    Motivation — bass-chop drift on ``sliceMode='chord'``:

    Chord regions from the analyzer sit wherever the chord recogniser
    said one chord ended and the next began. That boundary is a
    continuous timestamp; it's almost never exactly on the beat
    grid. When the client triggers a chord chop with a bar-quantized
    schedule (so playback *starts* on a downbeat), the *content* of
    the chop is still offset by a fraction of a beat — the note
    inside plays a hair late (or early) relative to the master beat,
    and the effect accumulates:

      * One-shot retriggers phase against each other because each
        instance starts at a bar downbeat but its content is offset
        by the same fractional beat, so consecutive triggers
        overlap out-of-phase.
      * Row-loop mode is worst: each loop iteration compounds the
        misalignment, drifting audibly within a few bars.

    Snapping both edges to the nearest beat guarantees:

      * The chop's audio starts on a beat — attack lines up.
      * The chop's duration is a whole number of beat periods —
        loop iterations stay phase-locked.
      * Overlapping triggers stay coherent because their content
        respects the same beat grid.

    Trade-off: the audio content shifts by up to half a beat relative
    to what the analyzer flagged as the chord boundary. For bass and
    harmonic content this is a win — the analyzer's boundary is
    itself an estimate, and note attacks typically land on beats in
    the source anyway. For onset / phrase / drum-bundle chops where
    the exact attack timing IS the point, callers should NOT invoke
    this helper (and don't — the vocal-phrase slicer and the drum
    onset detector both preserve their raw timestamps).

    Uses ``np.searchsorted`` (O(log N) per lookup). Falls through
    unchanged when beats are missing or numpy is unavailable so
    call sites can chain the helper unconditionally.
    """
    beats = _floats(result.get("beats_s"))
    if len(beats) < 2 or not chops:
        return chops
    try:
        import numpy as np
    except ImportError:
        return chops
    beats_arr = np.asarray(beats, dtype=float)

    def _nearest(t: float) -> float:
        idx = int(np.searchsorted(beats_arr, t))
        # Compare the beat immediately before and after ``t`` and
        # return whichever is closer. Boundary indices (0 or N)
        # collapse to a single candidate.
        prev_b = beats_arr[idx - 1] if idx > 0 else beats_arr[0]
        next_b = beats_arr[idx] if idx < beats_arr.size else beats_arr[-1]
        return float(prev_b if abs(t - prev_b) <= abs(t - next_b) else next_b)

    out: List[Dict[str, Any]] = []
    for c in chops:
        s = float(c.get("startSec") or 0.0)
        e = float(c.get("endSec") or 0.0)
        sn = _nearest(s)
        en = _nearest(e)
        if en <= sn:
            # Degenerate — chord region shorter than the beat
            # spacing, or both edges snapped to the same beat.
            # Skip; downstream waterfall / diversity selector
            # handles the shortfall.
            continue
        cc = dict(c)
        cc["startSec"] = round(sn, 4)
        cc["endSec"] = round(en, 4)
        out.append(cc)
    return out


def _snap_chops_to_bars(
    chops: List[Dict[str, Any]],
    result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Snap each chop's startSec / endSec to the nearest tracked
    downbeat so its duration is a whole number of bars.

    Motivation — bass-chop drift on ``quantize='bar'`` scheduling:

    ``_snap_chops_to_beats`` guarantees a whole-BEAT duration, but the
    bass preset triggers on the BAR grid. If a chop is 3 beats long
    and the scheduler retriggers every 4 beats (one bar in 4/4), the
    second iteration starts a beat before its content ended, and the
    third iteration compounds the offset — you hear a shuffled,
    unlocked loop.

    Snapping both edges to the nearest downbeat guarantees:

      * The chop starts on a bar boundary — attack lines up with the
        first beat of a bar in the source and with the retrigger
        schedule on the client.
      * The chop's duration is a whole number of bar periods —
        loop iterations stay phase-locked at ``quantize='bar'``.

    Trade-off: the audio content shifts by up to half a bar relative
    to the underlying chord region. For chord chops on bass /
    harmonic material this is a win — chord changes usually land on
    downbeats in the source anyway, and any residual drift from the
    chord recogniser's continuous timestamps is absorbed. For
    onset / phrase / drum-bundle chops where sub-beat attack timing
    IS the point, callers must NOT invoke this helper.

    Degrades to a no-op (returns input unchanged) when fewer than
    two downbeats are tracked or numpy is unavailable, so call sites
    can chain the helper unconditionally.
    """
    downs = _floats(result.get("downbeats_s"))
    if len(downs) < 2 or not chops:
        return chops
    try:
        import numpy as np
    except ImportError:
        return chops
    downs_arr = np.asarray(downs, dtype=float)

    def _nearest(t: float) -> float:
        idx = int(np.searchsorted(downs_arr, t))
        prev_d = downs_arr[idx - 1] if idx > 0 else downs_arr[0]
        next_d = downs_arr[idx] if idx < downs_arr.size else downs_arr[-1]
        return float(prev_d if abs(t - prev_d) <= abs(t - next_d) else next_d)

    out: List[Dict[str, Any]] = []
    for c in chops:
        s = float(c.get("startSec") or 0.0)
        e = float(c.get("endSec") or 0.0)
        sn = _nearest(s)
        en = _nearest(e)
        if en <= sn:
            # Degenerate — chord region shorter than one bar, or
            # both edges snapped to the same downbeat. Drop; the
            # waterfall selects other chops to fill the pad grid.
            continue
        cc = dict(c)
        cc["startSec"] = round(sn, 4)
        cc["endSec"] = round(en, 4)
        out.append(cc)
    return out


def _floats(x: Any) -> List[float]:
    if not isinstance(x, (list, tuple)):
        return []
    out: List[float] = []
    for v in x:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _first_present(d: Dict[str, Any], *keys: str) -> Any:
    """Return the value of the first key that's actually present in the
    dict. Unlike ``d.get(a) or d.get(b)``, this preserves a legitimate
    0 / '' / False value from a leading key instead of falling through
    to the next one."""
    for k in keys:
        if k in d:
            return d[k]
    return None


def _seconds_of(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


_PITCH_CLASS = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
    "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11,
}


def _root_pc_of(symbol: str) -> Optional[int]:
    """Parse the leading root note out of a chord symbol like 'F#m7'."""
    if not symbol or not isinstance(symbol, str):
        return None
    # Try two chars first ('C#', 'Db') then one.
    if len(symbol) >= 2 and symbol[:2] in _PITCH_CLASS:
        return _PITCH_CLASS[symbol[:2]]
    if symbol[:1] in _PITCH_CLASS:
        return _PITCH_CLASS[symbol[:1]]
    return None


def _select_diverse_chops(
    chops: Sequence[Dict[str, Any]],
    slice_mode: str,
    target: int,
) -> List[Dict[str, Any]]:
    """Farthest-point sampling in a per-mode distinctness metric.

    Two chops that share a label but sit over different chord
    territory, at different energy levels, or driven by a different
    dominant stem are *not* duplicates — they will sound different
    under the fingers. The old label-collapse dedupe erased that
    material. This selector instead greedily picks ``target`` chops
    that maximise total pairwise distance in the feature space
    below, which naturally keeps:

      * At least one instance of each label (label distance
        dominates the metric).
      * Temporally spread copies within the same label when only a
        few labels are present (temporal distance takes over).
      * Contrasting-energy copies when temporal spread is small
        (energy distance takes over).

    Feature weights are tuned by ``slice_mode`` because the
    generators attach different fields:

      * section : label ∪ energy ∪ dominant-stem ∪ time
      * chord   : symbol ∪ root pitch-class ∪ time ∪ duration
      * onset   : kind ∪ time (energy field is absent)
      * other   : time only → equivalent to evenly-spaced downsample

    Runtime is O(N × target) which is fine for our N ≈ few hundred
    upper bound. Preserves original ordering (start time) in the
    return value so downstream painting keeps the song's arc intact.
    """
    if len(chops) <= target:
        return list(chops)

    total_span = _time_span_of(chops)
    def _dist(a: Dict[str, Any], b: Dict[str, Any]) -> float:
        return _chop_distance(a, b, slice_mode, total_span)

    # Seed: pick the earliest chop. That gives the algorithm a
    # deterministic starting point and biases coverage toward song
    # start. From there, each pick maximises min-distance to any
    # already-selected chop (classic farthest-point sampling), so we
    # naturally spread across label + feature space + time.
    remaining = sorted(chops, key=lambda c: float(c.get("startSec") or 0.0))
    picked: List[Dict[str, Any]] = [remaining.pop(0)]
    # min_d[i] = current minimum distance from remaining[i] to any
    # already-picked chop. Update incrementally instead of scanning
    # all pairs on each step — this is what keeps the loop O(N * k).
    min_d: List[float] = [_dist(picked[0], c) for c in remaining]

    while len(picked) < target and remaining:
        # Take the remaining chop with the largest min-distance —
        # it's the one most contrasting with what we've already
        # got. Ties broken by earliest time to keep runs stable.
        best_i = 0
        best_d = -1.0
        for i, d in enumerate(min_d):
            if d > best_d:
                best_d, best_i = d, i
        chosen = remaining.pop(best_i)
        min_d.pop(best_i)
        picked.append(chosen)
        # Update min-distance table for the new pick.
        for i, c in enumerate(remaining):
            d = _dist(chosen, c)
            if d < min_d[i]:
                min_d[i] = d

    picked.sort(key=lambda c: float(c.get("startSec") or 0.0))
    return picked


def _time_span_of(chops: Sequence[Dict[str, Any]]) -> float:
    """Elapsed time between earliest start and latest end across the
    chops. Used as the denominator for temporal distance so the
    normalised value is comparable to the [0, 1] label / feature
    axes."""
    if not chops:
        return 1.0
    starts = [float(c.get("startSec") or 0.0) for c in chops]
    ends = [float(c.get("endSec") or 0.0) for c in chops]
    span = max(ends) - min(starts)
    return span if span > 0.001 else 1.0


def _chop_distance(
    a: Dict[str, Any],
    b: Dict[str, Any],
    slice_mode: str,
    total_span: float,
) -> float:
    """Per-mode distinctness distance in [0, ~1.5]. Higher = more
    different. Label carries the strongest weight because it is the
    most audible discriminator; temporal spread is a tie-breaker."""
    # Temporal distance is common to every mode: farther apart in
    # the song = more sonically different context around the chop.
    dt = abs(float(a.get("startSec") or 0.0) - float(b.get("startSec") or 0.0))
    time_d = min(1.0, dt / total_span)

    if slice_mode == "section":
        label_d = 1.0 if a.get("sectionLabel") != b.get("sectionLabel") else 0.0
        dom_d = 1.0 if (a.get("_dominantStem") or "") != (b.get("_dominantStem") or "") else 0.0
        ea = float(a.get("_energy") if a.get("_energy") is not None else 0.5)
        eb = float(b.get("_energy") if b.get("_energy") is not None else 0.5)
        energy_d = min(1.0, abs(ea - eb))
        # Weighted sum: label dominates, energy / dominant-stem are
        # meaningful signals when labels tie, time breaks residual
        # ties. Weights sum to > 1 on purpose so identical-label
        # sections still see ~0.5 distance when their features
        # diverge, keeping them from ever ranking below unrelated
        # noise.
        return 0.6 * label_d + 0.25 * energy_d + 0.2 * dom_d + 0.15 * time_d

    if slice_mode == "chord":
        sym_d = 1.0 if a.get("chordSymbol") != b.get("chordSymbol") else 0.0
        root_d = 0.0
        ra, rb = a.get("root"), b.get("root")
        if isinstance(ra, int) and isinstance(rb, int):
            # Interval distance on the circle of 12 pitch classes.
            interval = min((ra - rb) % 12, (rb - ra) % 12)
            root_d = interval / 6.0
        da = float(a.get("endSec") or 0.0) - float(a.get("startSec") or 0.0)
        db = float(b.get("endSec") or 0.0) - float(b.get("startSec") or 0.0)
        dur_d = min(1.0, abs(da - db) / max(1.0, max(da, db)))
        return 0.6 * sym_d + 0.25 * root_d + 0.2 * time_d + 0.1 * dur_d

    if slice_mode == "onset":
        kind_d = 1.0 if a.get("kind") != b.get("kind") else 0.0
        return 0.7 * kind_d + 0.3 * time_d

    return time_d


def _downsample_even(chops: Sequence[Dict[str, Any]], target: int) -> List[Dict[str, Any]]:
    """Pick ``target`` chops evenly across the full length. Preserves
    the temporal span of the original list so the pads still cover the
    whole song instead of stopping at chop 64."""
    n = len(chops)
    if n <= target:
        return list(chops)
    return [chops[int(round(i * (n - 1) / (target - 1)))] for i in range(target)]
