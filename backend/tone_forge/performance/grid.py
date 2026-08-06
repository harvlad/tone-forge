"""MusicalGrid — the beat/bar substrate every musical object snaps to.

Built from the analysis result's already-computed ``beats_s`` / ``downbeats_s``
/ ``tempo_bpm`` (never re-derived). Provides:
  * snap a timestamp to the nearest beat or bar,
  * build a GridPos for any [start,end] with beat/bar indices + lengths,
  * enumerate musically-sane loop windows (1/2/4/8 beats and bars) so loop
    lengths are never arbitrary.

Pure Python + bisect — no audio, fully unit-testable.
"""
from __future__ import annotations

import bisect
from typing import List, Optional, Sequence, Tuple

from .graph import GridPos

# Musically-sane loop lengths, in bars. Beats fill in the sub-bar options.
LOOP_BARS = (1, 2, 4, 8)
LOOP_BEATS = (1, 2)  # sub-bar loops (1 beat, 2 beats) for one-shots / stabs


class MusicalGrid:
    def __init__(
        self,
        beats_s: Sequence[float],
        downbeats_s: Sequence[float],
        tempo_bpm: float,
        time_signature: Tuple[int, int] = (4, 4),
        duration_s: float = 0.0,
    ):
        self.beats: List[float] = sorted(float(b) for b in beats_s)
        self.downbeats: List[float] = sorted(float(d) for d in downbeats_s)
        self.tempo_bpm = float(tempo_bpm) if tempo_bpm else 0.0
        self.beats_per_bar = max(1, int(time_signature[0]))
        self.time_signature = (int(time_signature[0]), int(time_signature[1]))
        self.duration_s = float(duration_s) or (self.beats[-1] if self.beats else 0.0)

    # -- basic lookups ----------------------------------------------------
    @property
    def beat_period_s(self) -> float:
        if self.tempo_bpm > 0:
            return 60.0 / self.tempo_bpm
        if len(self.beats) >= 2:
            return (self.beats[-1] - self.beats[0]) / (len(self.beats) - 1)
        return 0.5

    def nearest_beat_index(self, t: float) -> int:
        if not self.beats:
            return 0
        i = bisect.bisect_left(self.beats, t)
        if i == 0:
            return 0
        if i >= len(self.beats):
            return len(self.beats) - 1
        return i if abs(self.beats[i] - t) < abs(self.beats[i - 1] - t) else i - 1

    def snap_to_beat(self, t: float) -> float:
        return self.beats[self.nearest_beat_index(t)] if self.beats else t

    def nearest_downbeat_index(self, t: float) -> int:
        if not self.downbeats:
            return 0
        i = bisect.bisect_left(self.downbeats, t)
        if i == 0:
            return 0
        if i >= len(self.downbeats):
            return len(self.downbeats) - 1
        return i if abs(self.downbeats[i] - t) < abs(self.downbeats[i - 1] - t) else i - 1

    def snap_to_bar(self, t: float) -> float:
        return self.downbeats[self.nearest_downbeat_index(t)] if self.downbeats else t

    # -- GridPos construction --------------------------------------------
    def _bar_index_of_beat(self, beat_idx: int) -> int:
        """Bar index for a beat, via downbeats if available else /beats_per_bar."""
        if self.beats and self.downbeats:
            t = self.beats[beat_idx]
            di = bisect.bisect_right(self.downbeats, t + 1e-6) - 1
            return max(0, di)
        return beat_idx // self.beats_per_bar

    def make_pos(self, start_s: float, end_s: float, snap: str = "beat") -> GridPos:
        """Build a GridPos for [start,end], snapping to 'beat' or 'bar'."""
        if snap == "bar" and self.downbeats:
            s = self.snap_to_bar(start_s)
            e = self.snap_to_bar(end_s)
            if e <= s:  # keep at least one bar
                di = self.nearest_downbeat_index(s)
                e = self.downbeats[min(di + 1, len(self.downbeats) - 1)]
        else:
            s = self.snap_to_beat(start_s)
            e = self.snap_to_beat(end_s)
            if e <= s and self.beats:
                bi = self.nearest_beat_index(s)
                e = self.beats[min(bi + 1, len(self.beats) - 1)]

        sb = self.nearest_beat_index(s)
        eb = self.nearest_beat_index(e)
        length_beats = max(0.0, float(eb - sb))
        start_bar = self._bar_index_of_beat(sb)
        end_bar = self._bar_index_of_beat(eb)
        length_bars = float(max(0, end_bar - start_bar))
        is_bar_aligned = bool(self.downbeats) and abs(self.snap_to_bar(s) - s) < 1e-3
        return GridPos(
            start_s=s,
            end_s=e,
            start_beat=sb,
            length_beats=length_beats,
            start_bar=start_bar,
            length_bars=length_bars,
            is_bar_aligned=is_bar_aligned,
        )

    # -- loop-window enumeration -----------------------------------------
    def loop_windows(self, from_s: float = 0.0, to_s: Optional[float] = None) -> List[GridPos]:
        """Every musically-sane loop window (1/2/4/8 bars, plus 1/2-beat sub-bar)
        starting on each downbeat within [from_s, to_s]. These are the candidate
        loops the LoopAnalyzer scores — durations are always whole bars/beats."""
        to_s = self.duration_s if to_s is None else to_s
        out: List[GridPos] = []
        anchors = [d for d in self.downbeats if from_s - 1e-6 <= d <= to_s] or (
            [b for b in self.beats if from_s - 1e-6 <= b <= to_s]
        )
        bp = self.beat_period_s
        bpb = self.beats_per_bar
        for a in anchors:
            for bars in LOOP_BARS:
                end = a + bars * bpb * bp
                if end <= to_s + 1e-6:
                    out.append(self.make_pos(a, end, snap="bar"))
            for beats in LOOP_BEATS:
                end = a + beats * bp
                if end <= to_s + 1e-6:
                    out.append(self.make_pos(a, end, snap="beat"))
        # dedupe by (start,end) rounded
        seen = set()
        uniq: List[GridPos] = []
        for p in out:
            k = (round(p.start_s, 3), round(p.end_s, 3))
            if k not in seen:
                seen.add(k)
                uniq.append(p)
        return uniq
