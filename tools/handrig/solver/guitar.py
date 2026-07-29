"""Phase 2 — Guitar contact + collision model.

Requested playing positions are expressed as CONTACT CONSTRAINTS, never
as chord poses. The physical neck geometry mirrors the app's
GuitarPhysical (648 mm scale, equal-temperament frets, string taper,
neck thickness) so the solver and the renderer share one coordinate
system.

Guitar space (mm), matching the reference pipeline:
    x  along the neck; nut at x=0, frets increase toward NEGATIVE x
       (headstock-right UI convention).
    z  across the strings; low E (string 0) at the TOP (+z), high e
       (string 5) at the bottom (−z).
    y  depth: +y is BEHIND the board (into the neck / toward the
       thumb); the fretting fingers press from y<0 (viewer side).
The board front face is the plane y=0. The neck rear face is y=+NECK_THICK.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np

SCALE_LEN = 648.0
NECK_THICK = 21.0
STRING_SPAN_NUT = 35.0
STRING_SPAN_12 = 43.5      # E→e span halfway to the saddle (taper)
BOARD_W = 52.0             # fingerboard width across the 6 strings + margin
N_STRINGS = 6


def wire(n: int) -> float:
    """Distance from the nut to fret-wire n (mm), equal temperament."""
    if n <= 0:
        return 0.0
    return SCALE_LEN * (1.0 - 2.0 ** (-n / 12.0))


def finger_x(fret: int) -> float:
    """Contact x for a fret: 30% of the slot behind the wire.
    Returns a NEGATIVE x (frets increase toward −x)."""
    lo, hi = wire(fret - 1), wire(fret)
    return -(hi - (hi - lo) * 0.30)


def string_z(s: int, x_abs: float) -> float:
    """Across-neck position of string s (0 = low E, top). x_abs is the
    absolute distance from the nut (for the span taper)."""
    t = min(1.0, x_abs / (SCALE_LEN / 2.0))
    span = STRING_SPAN_NUT + (STRING_SPAN_12 - STRING_SPAN_NUT) * t
    # string 0 (low E) at +z top, string 5 (high e) at −z bottom.
    return (2.5 - s) * span / 5.0


@dataclass
class Contact:
    """A required fingertip contact. The solver treats `finger` as the
    assigned digit (fingering comes from JAMN / the planner, NOT here)."""
    string: int                 # 0 = low E … 5 = high e
    fret: int                   # 1-based; 0 would be an open string (no contact)
    finger: str                 # "index"|"middle"|"ring"|"pinky"
    required: bool = True
    # Timing hooks for future trajectory work (unused this increment):
    t_contact: float = 0.0
    t_release: float = 0.0
    articulation: str = "fret"  # fret|barre|mute|slide|hammer|bend

    def target(self) -> np.ndarray:
        """World-space fingertip target (mm). Pad presses just in front
        of the board (y slightly negative → tip touches the string)."""
        x = finger_x(self.fret)
        z = string_z(self.string, abs(x))
        return np.array([x, -1.5, z])

    def approach_normal(self) -> np.ndarray:
        """Desired press direction: into the board (+y)."""
        return np.array([0.0, 1.0, 0.0])


@dataclass
class BarreContact:
    """A barre: one finger as a LINE contact across a string range at a
    fret. Expressed as several colinear point contacts on that finger's
    tip→base extent; the solver keeps the finger straight to satisfy
    them jointly."""
    fret: int
    lo_string: int
    hi_string: int
    finger: str = "index"

    def targets(self) -> list[np.ndarray]:
        x = finger_x(self.fret)
        return [np.array([x, -1.5, string_z(s, abs(x))])
                for s in range(self.lo_string, self.hi_string + 1)]


# --------------------------------------------------------------------------
# Neck geometry helpers for collision.
# --------------------------------------------------------------------------
def board_front_y() -> float:
    return 0.0


def neck_rear_y() -> float:
    return NECK_THICK


def board_z_range():
    return (-BOARD_W / 2.0, BOARD_W / 2.0)


def penetrates_board(point: np.ndarray, margin: float = 0.0) -> float:
    """How far a point is BEHIND the board front face while over the
    fingerboard width (mm, 0 if in front). Fingers must press from
    y<0; a fingertip pushed to y>margin is inside the wood."""
    zlo, zhi = board_z_range()
    if zlo - 5 <= point[2] <= zhi + 5:
        depth = point[1] - margin
        return max(0.0, depth)
    return 0.0


def behind_neck(point: np.ndarray) -> bool:
    """True if a point is behind the neck's rear face (thumb zone)."""
    return bool(point[1] > neck_rear_y() - 3.0)
