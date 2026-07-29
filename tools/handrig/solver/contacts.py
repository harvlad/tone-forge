"""B. Generalized contact model.

Contacts are physical primitives, NOT poses. The solver reads only the
GEOMETRY a contact requires; it never sees a chord name and there is no
`if barre` / `if F#` anywhere. New primitive types (SURFACE, MOVING) can
be added by subclassing without touching the solver.

Primitive interface:
    kind            : str
    finger          : str  (assigned digit; fingering comes from JAMN)
    targets()       : list[np.ndarray]   (points, for rendering)
    residual(fk)    : float  (squared geometric error to feed the objective)
    error_mm(fk)    : float  (worst physical miss, for the feasibility check)

`fk` is the per-finger forward-kinematics result (anatomy.FingerFK), so a
primitive can reason about the whole finger surface (spine), not just the
tip.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import guitar as G


def _point_to_polyline(p, pts):
    """Min distance from point p to the polyline through pts."""
    best = 1e18
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        ab = b - a
        t = np.clip(np.dot(p - a, ab) / (np.dot(ab, ab) + 1e-9), 0, 1)
        best = min(best, float(np.linalg.norm(p - (a + ab * t))))
    return best


class ContactPrimitive:
    kind = "abstract"
    finger = "index"
    required = True

    def targets(self):
        raise NotImplementedError

    def residual(self, fk) -> float:
        raise NotImplementedError

    def error_mm(self, fk) -> float:
        raise NotImplementedError


@dataclass
class PointContact(ContactPrimitive):
    """A fingertip presses one string at one fret."""
    string: int
    fret: int
    finger: str
    required: bool = True
    kind: str = "point"
    t_contact: float = 0.0
    t_release: float = 0.0
    articulation: str = "fret"

    def _target(self):
        x = G.finger_x(self.fret)
        return np.array([x, -1.5, G.string_z(self.string, abs(x))])

    def targets(self):
        return [self._target()]

    def residual(self, fk) -> float:
        return float(np.sum((fk.tip - self._target()) ** 2))

    def error_mm(self, fk) -> float:
        return float(np.linalg.norm(fk.tip - self._target()))


@dataclass
class LineContact(ContactPrimitive):
    """A finger SURFACE (its spine) must maintain contact across a
    fretboard/string span at one fret. General: the solver decides the
    posture that lays the finger across the line. No barre/chord logic —
    this is pure geometry.

    Contact is satisfied when, for every covered string, SOME point on
    the finger's spine (MCP→PIP→DIP→tip) lies within tolerance of that
    string's point at the fret. A curled finger cannot pass near a set
    of colinear points, so this term intrinsically demands a flat
    finger without any explicit 'straighten' hack."""
    fret: int
    lo_string: int
    hi_string: int
    finger: str = "index"
    required: bool = True
    kind: str = "line"

    def targets(self):
        x = G.finger_x(self.fret)
        return [np.array([x, -1.5, G.string_z(s, abs(x))])
                for s in range(self.lo_string, self.hi_string + 1)]

    def _spine(self, fk):
        return [fk.mcp, fk.pip, fk.dip, fk.tip]

    def residual(self, fk) -> float:
        spine = self._spine(fk)
        return float(sum(_point_to_polyline(t, spine) ** 2
                         for t in self.targets()))

    def error_mm(self, fk) -> float:
        spine = self._spine(fk)
        return max(_point_to_polyline(t, spine) for t in self.targets())


# --- Reserved for future increments (interface only; NOT implemented) ---
class SurfaceContact(ContactPrimitive):
    """Reserved: a 2-D finger-pad surface against a region. Not
    implemented this increment — present so the solver interface need
    not change later."""
    kind = "surface"


class MovingContact(ContactPrimitive):
    """Reserved: a contact that translates over time (slides/bends).
    Not implemented — trajectory work only."""
    kind = "moving"
