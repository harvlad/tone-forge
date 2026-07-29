"""Phase 2 — Python forward kinematics matching Blender's MPFB skeleton.

Blender pose FK, per bone:
    M_pose[b] = M_pose[parent] @ M_local[parent]^-1 @ M_local[b] @ B[b]
where M_local is the bone's armature-space REST matrix and B is the
local rotation channel (matrix_basis). World = armature_world @ M_pose.

For a finger we pose only the three phalanges; the metacarpal parent
stays at rest, so its contribution cancels and the first phalanx reduces
to M_pose[{d}-1] = M_local[{d}-1] @ B1. This module reproduces exactly
that, reading the rig description dumped by mpfb_characterize.py.

Pure numpy. No Blender. Validated to sub-mm by validate_fk (run in
Blender) before any optimization trusts it.
"""
from __future__ import annotations
import json
import numpy as np

FINGER_MAP = {"index": "finger2", "middle": "finger3",
              "ring": "finger4", "pinky": "finger5", "thumb": "finger1"}


def _euler_xyz(rx, ry, rz):
    """Blender Euler('XYZ').to_matrix(): intrinsic X then Y then Z,
    composed as R = Rz @ Ry @ Rx (validated numerically)."""
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _basis(rx, ry, rz):
    M = np.eye(4)
    M[:3, :3] = _euler_xyz(rx, ry, rz)
    return M


class MPFBHand:
    def __init__(self, rig_path):
        d = json.load(open(rig_path))
        self.bones = {n: {**b, "M": np.array(b["matrix_local"]),
                          "Minv": np.linalg.inv(np.array(b["matrix_local"]))}
                      for n, b in d["bones"].items()}
        self.finger_map = d.get("finger_map", FINGER_MAP)
        self.rest_tips = d.get("rest_tips", {})

    def chain(self, digit):
        return [f"{digit}-1.L", f"{digit}-2.L", f"{digit}-3.L"]

    def finger_fk(self, digit, angles, armature_world):
        """angles: list of three (rx,ry,rz) tuples for the 3 phalanges.
        Returns armature-world joint positions [base, pip, dip, tip] (m).
        """
        names = self.chain(digit)
        # First phalanx: parent (metacarpal) at rest → M_pose = M_local @ B.
        b0 = self.bones[names[0]]
        Mp = b0["M"] @ _basis(*angles[0])
        joints_local = [Mp[:3, 3].copy()]
        prev_name = names[0]
        Mprev = Mp
        for i in (1, 2):
            b = self.bones[names[i]]
            bp = self.bones[prev_name]
            Mprev = Mprev @ bp["Minv"] @ b["M"] @ _basis(*angles[i])
            joints_local.append(Mprev[:3, 3].copy())
            prev_name = names[i]
        # Tip: distal bone origin + length along its local +Y.
        tip_local = (Mprev @ np.array([0, self.bones[names[2]]["length"], 0, 1]))[:3]
        joints_local.append(tip_local)
        # To world.
        AW = np.array(armature_world)
        out = []
        for p in joints_local:
            out.append((AW @ np.array([*p, 1.0]))[:3])
        return out
