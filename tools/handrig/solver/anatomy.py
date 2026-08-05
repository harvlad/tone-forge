"""Phase 1 — Anatomical hand constraint model (guitar-agnostic).

A commercially clean, clean-room representation of human hand kinematics
and physiological limits. NO MANO / MS-MANO models, weights, or PCA data
are used. Every constant carries provenance: source, units, and any
engineering approximation we make.

Coordinate convention (hand-local, right hand in canonical rest pose):
    +x  toward the thumb side (radial)
    +y  toward the fingertips (distal)
    +z  out of the palm, dorsal (back of hand)
The wrist places this frame in world space (see forward_kinematics).

Degrees of freedom (26 total):
    wrist:  translation(3) + rotation(3, euler XYZ)          = 6
    fingers index/middle/ring/pinky, each:
        MCP flexion, MCP abduction, PIP flexion, DIP flexion  = 4 × 4 = 16
    thumb:  CMC opposition, CMC flexion, MCP flexion, IP flex = 4
DIP is a FREE variable in the state but is softly coupled to PIP by the
tendon relationship (see COUPLING); the solver may keep it as a driven
value or optimize it within a tight band.

Units: lengths in millimetres, angles in radians internally (degrees at
the provenance boundary for readability).

--------------------------------------------------------------------------
PROVENANCE OF PHYSIOLOGICAL DATA
--------------------------------------------------------------------------
[ROM]  Joint range-of-motion. Sources:
       - OpenSim hand/wrist musculoskeletal model, McFarland et al.,
         "A Musculoskeletal Model of the Hand and Wrist Capable of
         Simulating Functional Tasks", bioRxiv 2021.12.28.474357 —
         passive flexion/extension ROM per IP/MCP DoF.
       - American Academy of Orthopaedic Surgeons (AAOS) / American
         Medical Association (AMA) "Guides to the Evaluation of
         Permanent Impairment" standard joint ROM tables (finger MCP
         0–90°, PIP 0–100–110°, DIP 0–70–80°; thumb CMC/MCP/IP).
       These are published physiological FACTS (measured ranges), used
       as numeric limits — not copyrighted model expression.
[COUP] Inter-joint coupling. Source: well-documented FDP/FDS tendon
       linkage and the clinically standard DIP≈(2/3)·PIP relationship
       (e.g. Leijnse; Rijpkema & Girard 1991 hand-animation coupling).
       Engineering approximation: linear ratio, band ±15°.
[SPLAY] MCP abduction/adduction limits. Source: AAOS finger abduction
       ~±20° from neutral at the MCP; index and little finger abduct
       more freely than middle/ring. Approximation: per-finger caps.
[SEG]  Phalange segment lengths. Source: anthropometric means
       (Buchholz, Armstrong & Goldstein 1992, "Anthropometric data for
       describing the kinematics of the human hand"), male 50th pct.
       Scalable by a single hand-size factor.
[MCPROW] Knuckle (MCP) placement across the palm. Source: same
       anthropometric hand-breadth data; the metacarpal heads lie on a
       shallow arch. Approximation: fixed local offsets on that arch.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

D2R = np.pi / 180.0

FINGERS = ("index", "middle", "ring", "pinky")


# --------------------------------------------------------------------------
# [SEG] Phalange segment lengths (mm), male 50th percentile.
# Buchholz et al. 1992. Order: proximal, middle, distal.
# --------------------------------------------------------------------------
SEGMENTS_MM = {
    "index":  (39.8, 22.4, 15.8),
    "middle": (44.6, 26.3, 17.4),
    "ring":   (41.4, 25.7, 17.3),
    "pinky":  (32.7, 18.1, 15.8),
}
# Thumb: metacarpal, proximal, distal (mm). Buchholz et al. 1992.
THUMB_SEGMENTS_MM = (46.2, 31.6, 21.7)

# [MCPROW] Metacarpal-head (MCP) local positions on the palm arch (mm).
# x = radial spread (index toward +x), y = distal offset (knuckle row),
# z = slight dorsal arch (middle proudest). Anthropometric hand breadth
# ~ 85 mm across the four MCPs.
MCP_LOCAL_MM = {
    "index":  (28.0, 3.0, 2.0),
    "middle": (9.0, 5.0, 3.5),
    "ring":   (-11.0, 3.5, 2.5),
    "pinky":  (-30.0, 0.0, 0.0),
}
# Thumb CMC (carpometacarpal) base on the radial side of the palm (mm).
THUMB_CMC_LOCAL_MM = (36.0, -18.0, -4.0)

# Wrist → palm origin: the palm frame origin sits at the wrist; the MCP
# row is ~ this far distal. Buchholz palm length ~ 95 mm.
PALM_LENGTH_MM = 95.0


@dataclass(frozen=True)
class JointLimit:
    """A single DoF limit with provenance."""
    lo_deg: float
    hi_deg: float
    source: str

    @property
    def lo(self) -> float:
        return self.lo_deg * D2R

    @property
    def hi(self) -> float:
        return self.hi_deg * D2R


# --------------------------------------------------------------------------
# [ROM]/[SPLAY] Per-DoF physiological limits.
# Extension is negative (hyperextension), flexion positive.
# --------------------------------------------------------------------------
def _finger_limits(finger: str) -> dict[str, JointLimit]:
    # Index/little abduct more than middle/ring [SPLAY]. Fretting-hand MCP
    # abduction is small — a real fretting hand stays compact, fingers close
    # together, not splayed like a resting relaxed hand. Tight caps
    # (~±8-10°, i.e. ~±0.14-0.17 rad) keep poses natural while still leaving
    # enough sideways reach for the tips to land on adjacent-string frets.
    abduct = {"index": 10.0, "middle": 8.0, "ring": 8.0, "pinky": 10.0}[finger]
    return {
        # MCP flexion: slight hyperextension to ~90° flexion [ROM AAOS].
        "mcp_flex": JointLimit(-20.0, 90.0, "AAOS MCP 0-90, hyperext -20"),
        # MCP abduction: ± per finger [SPLAY AAOS].
        "mcp_abd": JointLimit(-abduct, abduct, "AAOS finger abduction ±20"),
        # PIP: 0 to ~110° [ROM AAOS / OpenSim passive].
        "pip_flex": JointLimit(0.0, 110.0, "AAOS PIP 0-110"),
        # DIP: 0 to ~80° [ROM AAOS]; coupled to PIP [COUP].
        "dip_flex": JointLimit(-10.0, 80.0, "AAOS DIP 0-80, slight hyperext"),
    }


def _thumb_limits() -> dict[str, JointLimit]:
    return {
        # CMC opposition (palmar abduction toward the little finger).
        "cmc_oppose": JointLimit(0.0, 60.0, "AAOS thumb CMC opposition"),
        # CMC flexion/extension.
        "cmc_flex": JointLimit(-15.0, 45.0, "AAOS thumb CMC flex/ext"),
        # MCP flexion.
        "mcp_flex": JointLimit(-10.0, 55.0, "AAOS thumb MCP 0-55"),
        # IP flexion.
        "ip_flex": JointLimit(-15.0, 80.0, "AAOS thumb IP 0-80"),
    }


# [COUP] DIP is driven by PIP through the extensor mechanism.
# DIP ≈ COUPLING_RATIO · PIP, within ± COUPLING_BAND (engineering approx).
COUPLING_RATIO = 2.0 / 3.0
COUPLING_BAND_DEG = 15.0


@dataclass
class HandModel:
    """Anatomical constants + limits for one hand. Guitar-agnostic."""
    hand_scale: float = 1.0  # 1.0 = male 50th pct; scales all lengths
    segments: dict[str, tuple] = field(default_factory=dict)
    thumb_segments: tuple = ()
    mcp_local: dict[str, np.ndarray] = field(default_factory=dict)
    thumb_cmc_local: np.ndarray = None
    palm_length: float = 0.0
    finger_limits: dict[str, dict[str, JointLimit]] = field(default_factory=dict)
    thumb_limits: dict[str, JointLimit] = field(default_factory=dict)

    @classmethod
    def default(cls, hand_scale: float = 1.0) -> "HandModel":
        s = hand_scale
        return cls(
            hand_scale=s,
            segments={f: tuple(v * s for v in SEGMENTS_MM[f]) for f in FINGERS},
            thumb_segments=tuple(v * s for v in THUMB_SEGMENTS_MM),
            mcp_local={f: np.array(MCP_LOCAL_MM[f]) * s for f in FINGERS},
            thumb_cmc_local=np.array(THUMB_CMC_LOCAL_MM) * s,
            palm_length=PALM_LENGTH_MM * s,
            finger_limits={f: _finger_limits(f) for f in FINGERS},
            thumb_limits=_thumb_limits(),
        )

    def finger_length(self, finger: str) -> float:
        return sum(self.segments[finger])


# --------------------------------------------------------------------------
# State vector packing/unpacking.
# Layout (26): [wx wy wz  rx ry rz  |  per finger: mcpF mcpA pip dip (×4) |
#               thumb: cmcO cmcF mcpF ip]
# --------------------------------------------------------------------------
STATE_SIZE = 6 + 4 * 4 + 4  # 26

FINGER_DOF = ("mcp_flex", "mcp_abd", "pip_flex", "dip_flex")
THUMB_DOF = ("cmc_oppose", "cmc_flex", "mcp_flex", "ip_flex")


@dataclass
class HandState:
    wrist_pos: np.ndarray            # (3,) world mm
    wrist_rot: np.ndarray            # (3,) euler XYZ radians
    finger: dict[str, np.ndarray]    # per finger (4,) radians in FINGER_DOF order
    thumb: np.ndarray                # (4,) radians in THUMB_DOF order

    def to_vector(self) -> np.ndarray:
        parts = [self.wrist_pos, self.wrist_rot]
        for f in FINGERS:
            parts.append(self.finger[f])
        parts.append(self.thumb)
        return np.concatenate(parts)

    @classmethod
    def from_vector(cls, v: np.ndarray) -> "HandState":
        wrist_pos = v[0:3]
        wrist_rot = v[3:6]
        finger = {}
        off = 6
        for f in FINGERS:
            finger[f] = v[off:off + 4]
            off += 4
        thumb = v[off:off + 4]
        return cls(wrist_pos, wrist_rot, finger, thumb)


def bounds(model: HandModel, wrist_pos_hint: np.ndarray, reach: float):
    """Box bounds for the state vector: joints at physiological limits,
    wrist translation within a generous box around the hint."""
    lo = np.empty(STATE_SIZE)
    hi = np.empty(STATE_SIZE)
    # Wrist translation: ± full-hand reach around the hint.
    lo[0:3] = wrist_pos_hint - reach
    hi[0:3] = wrist_pos_hint + reach
    # Wrist rotation: generous but bounded (no full spins).
    lo[3:6] = -np.pi
    hi[3:6] = np.pi
    off = 6
    for f in FINGERS:
        lims = model.finger_limits[f]
        for j, dof in enumerate(FINGER_DOF):
            lo[off + j] = lims[dof].lo
            hi[off + j] = lims[dof].hi
        off += 4
    tl = model.thumb_limits
    for j, dof in enumerate(THUMB_DOF):
        lo[off + j] = tl[dof].lo
        hi[off + j] = tl[dof].hi
    return lo, hi


# --------------------------------------------------------------------------
# Forward kinematics.
# --------------------------------------------------------------------------
def euler_matrix(rx: float, ry: float, rz: float) -> np.ndarray:
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _axis_rot(axis: np.ndarray, angle: float) -> np.ndarray:
    a = axis / (np.linalg.norm(axis) + 1e-12)
    c, s = np.cos(angle), np.sin(angle)
    x, y, z = a
    C = 1 - c
    return np.array([
        [c + x*x*C, x*y*C - z*s, x*z*C + y*s],
        [y*x*C + z*s, c + y*y*C, y*z*C - x*s],
        [z*x*C - y*s, z*y*C + x*s, c + z*z*C],
    ])


@dataclass
class FingerFK:
    mcp: np.ndarray
    pip: np.ndarray
    dip: np.ndarray
    tip: np.ndarray

    def joints(self):
        return [self.mcp, self.pip, self.dip, self.tip]


def forward_kinematics(model: HandModel, state: HandState):
    """Return world-space joint chains for all fingers + thumb.

    Palm frame: local axes as documented at module top. Fingers extend
    along +y in rest; MCP abduction rotates the finger plane about the
    palm normal (+z), MCP/PIP/DIP flexion curls the finger toward the
    palm (rotation about the finger's lateral axis).
    """
    Rw = euler_matrix(*state.wrist_rot)
    origin = state.wrist_pos

    def to_world(local):
        return origin + Rw @ local

    fingers = {}
    for f in FINGERS:
        mcp_local = model.mcp_local[f].astype(float)
        # Knuckle row is PALM_LENGTH distal from the wrist origin.
        mcp_local = mcp_local + np.array([0.0, model.palm_length, 0.0])
        mcpF, mcpA, pip, dip = state.finger[f]
        L1, L2, L3 = model.segments[f]

        # Finger plane: start pointing +y, abduct about palm normal +z.
        Rabd = _axis_rot(np.array([0, 0, 1.0]), mcpA)
        # Lateral (flexion) axis after abduction. Using −x so POSITIVE
        # flexion curls the finger toward the palm (−z, palmar), which
        # matches the physiological sign of the joint limits.
        lateral = Rabd @ np.array([-1.0, 0.0, 0.0])
        forward0 = Rabd @ np.array([0.0, 1.0, 0.0])

        # Proximal: flex about lateral by mcpF (curl toward palm = −z).
        Rm = _axis_rot(lateral, mcpF)
        dir1 = Rm @ forward0
        p_mcp = mcp_local
        p_pip = p_mcp + dir1 * L1

        # Middle: additional PIP flexion about the same lateral axis.
        Rp = _axis_rot(lateral, mcpF + pip)
        dir2 = Rp @ forward0
        p_dip = p_pip + dir2 * L2

        # Distal: DIP flexion.
        Rd = _axis_rot(lateral, mcpF + pip + dip)
        dir3 = Rd @ forward0
        p_tip = p_dip + dir3 * L3

        fingers[f] = FingerFK(
            to_world(p_mcp), to_world(p_pip), to_world(p_dip), to_world(p_tip))

    # Thumb: opposition rotates the metacarpal across the palm, then
    # flex/IP curl it. Simplified 3-segment chain.
    cmcO, cmcF, mcpF, ip = state.thumb
    T1, T2, T3 = model.thumb_segments
    base = model.thumb_cmc_local.astype(float)
    # Opposition about +y (distal) swings the thumb toward the palm;
    # cmc flex about lateral (+x) drops it behind. Rest thumb points
    # −y and −x (toward the little-finger side, down).
    rest = np.array([-0.5, -0.4, -0.77])
    rest = rest / np.linalg.norm(rest)
    Ropp = _axis_rot(np.array([0, 1.0, 0]), cmcO)
    Rflex = _axis_rot(np.array([1.0, 0, 0]), cmcF)
    dirT1 = Rflex @ (Ropp @ rest)
    t_cmc = base
    t_mcp = t_cmc + dirT1 * T1
    Rm = _axis_rot(np.array([1.0, 0, 0]), cmcF + mcpF)
    dirT2 = Rm @ (Ropp @ rest)
    t_ip = t_mcp + dirT2 * T2
    Ri = _axis_rot(np.array([1.0, 0, 0]), cmcF + mcpF + ip)
    dirT3 = Ri @ (Ropp @ rest)
    t_tip = t_ip + dirT3 * T3
    thumb = FingerFK(to_world(t_cmc), to_world(t_mcp), to_world(t_ip),
                     to_world(t_tip))
    return fingers, thumb, Rw


# --------------------------------------------------------------------------
# Anatomical soft penalties (guitar-agnostic).
# --------------------------------------------------------------------------
def coupling_penalty(state: HandState) -> float:
    """[COUP] DIP should track (2/3)·PIP within a band."""
    total = 0.0
    band = COUPLING_BAND_DEG * D2R
    for f in FINGERS:
        _, _, pip, dip = state.finger[f]
        target = COUPLING_RATIO * pip
        d = abs(dip - target)
        if d > band:
            total += (d - band) ** 2
    return total


def strain_penalty(state: HandState) -> float:
    """Deviation from a FRETTING-READY resting pose, per joint. Once a
    hand approaches a neck it does not relax flat — it assumes a compact
    ready curl (MCP flexed, fingers curled toward the strings, abduction
    near zero). Unused fingers pull to THIS, so they stay compact and
    available instead of splaying open. Provenance: general fretting
    posture (MCP ~28°, PIP ~45°, DIP ~30° ≈ ⅔·PIP; near-zero splay)."""
    neutral = np.array([28.0, 0.0, 45.0, 30.0]) * D2R
    weights = np.array([1.0, 2.0, 0.9, 0.6])  # abduction strains most
    total = 0.0
    for f in FINGERS:
        d = state.finger[f] - neutral
        total += float(np.sum(weights * d * d))
    return total


def splay_penalty(state: HandState) -> float:
    """Excess MCP abduction beyond a comfortable ±8°, and abduction
    that disagrees with the neighbour (adjacent-finger coupling)."""
    comfort = 4.0 * D2R
    total = 0.0
    abds = [state.finger[f][1] for f in FINGERS]
    for a in abds:
        e = abs(a) - comfort
        if e > 0:
            total += e * e
    # Neighbour coupling: ring can't splay far without pinky following.
    for i in range(len(abds) - 1):
        total += 0.5 * (abds[i] - abds[i + 1]) ** 2
    return total
