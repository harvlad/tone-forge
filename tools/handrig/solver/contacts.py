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

    def _region(self):
        """Physically-valid fretting AREA (min, max) per axis, mm:
        - x: anywhere in the fret SLOT behind the wire (between wire F-1
             and wire F), with a small margin off each wire.
        - z: within ±40% of a string gap of the target string (on the
             correct string, clear of the neighbours).
        - y: pad touching the string within a shallow press band.
        The tip may sit ANYWHERE in this box at zero cost, giving the
        whole-hand optimiser spatial freedom."""
        lo_wire, hi_wire = G.wire(self.fret - 1), G.wire(self.fret)
        xr = (-(hi_wire) + 1.5, -(lo_wire) - 1.5)          # slot interior
        x_mid = G.finger_x(self.fret)
        gap = abs(G.string_z(0, abs(x_mid)) - G.string_z(1, abs(x_mid)))
        z0 = G.string_z(self.string, abs(x_mid))
        zr = (z0 - 0.40 * gap, z0 + 0.40 * gap)
        yr = (-3.0, 2.0)
        return (min(xr), max(xr)), zr, yr

    @staticmethod
    def _axis_miss(v, lo, hi):
        if v < lo:
            return lo - v
        if v > hi:
            return v - hi
        return 0.0

    def residual(self, fk) -> float:
        (xl, xh), (zl, zh), (yl, yh) = self._region()
        t = fk.tip
        dx = self._axis_miss(t[0], xl, xh)
        dy = self._axis_miss(t[1], yl, yh)
        dz = self._axis_miss(t[2], zl, zh)
        return float(dx * dx + dy * dy + dz * dz)

    def error_mm(self, fk) -> float:
        (xl, xh), (zl, zh), (yl, yh) = self._region()
        t = fk.tip
        dx = self._axis_miss(t[0], xl, xh)
        dy = self._axis_miss(t[1], yl, yh)
        dz = self._axis_miss(t[2], zl, zh)
        return float(np.sqrt(dx * dx + dy * dy + dz * dz))


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
