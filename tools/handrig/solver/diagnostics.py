"""Diagnostic renders for solver validation. Matplotlib (BSD) — offline.

Draws fretboard, strings, contact targets, solved skeleton, fingertip
locations, thumb, and collision/limit markers. Two panels: front view
(what JAMN shows, x–z plane) and side view (x–y, showing depth / thumb
behind neck).
"""

from __future__ import annotations
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import anatomy as A
import guitar as G


FINGER_COLORS = {"index": "#2ec4ff", "middle": "#ffd23f",
                 "ring": "#ff7a3d", "pinky": "#ff3d6e"}


def _draw_neck_front(ax):
    zlo, zhi = G.board_z_range()
    x0, x1 = -G.wire(6), 0
    ax.add_patch(plt.Rectangle((x0, zlo), x1 - x0, zhi - zlo,
                               color="#1a120b", zorder=0))
    for n in range(0, 7):
        x = -G.wire(n)
        ax.plot([x, x], [zlo, zhi], color="#888", lw=1.2, zorder=1)
    for s in range(6):
        z = G.string_z(s, G.wire(6) / 2)
        ax.plot([x0, x1], [z, z], color="#bbb", lw=0.8, zorder=1)


def render(result, contacts, barre, title, path):
    fig, (axf, axs) = plt.subplots(1, 2, figsize=(15, 7))
    fig.patch.set_facecolor("#0b0b10")

    fingers = result.fk["fingers"]
    thumb = result.fk["thumb"]

    # ---- Front view (x–z) ----
    axf.set_facecolor("#0b0b10")
    _draw_neck_front(axf)
    for f in A.FINGERS:
        js = fingers[f].joints()
        xs = [p[0] for p in js]
        zs = [p[2] for p in js]
        axf.plot(xs, zs, "-o", color=FINGER_COLORS[f], lw=2.5, ms=5, zorder=3)
    # Thumb (behind neck → dashed).
    tj = thumb.joints()
    axf.plot([p[0] for p in tj], [p[2] for p in tj], "--o",
             color="#8f8", lw=1.5, ms=3, alpha=0.6, zorder=2)
    # Contact targets.
    for c in contacts:
        t = c.target()
        axf.plot(t[0], t[2], "x", color="#fff", ms=12, mew=2, zorder=4)
    if barre is not None:
        for t in barre.targets():
            axf.plot(t[0], t[2], "x", color="#fff", ms=10, mew=2, zorder=4)
    axf.set_title(f"{title} — FRONT (JAMN view)", color="#ddd")
    axf.set_xlabel("x (along neck, mm)", color="#999")
    axf.set_ylabel("z (across strings, mm)", color="#999")
    axf.set_aspect("equal"); axf.invert_xaxis()
    axf.tick_params(colors="#666")

    # ---- Side view (x–y): depth, thumb behind neck ----
    axs.set_facecolor("#0b0b10")
    x0, x1 = -G.wire(6), 0
    axs.axhspan(0, G.NECK_THICK, color="#2a1d10", zorder=0)  # neck slab
    axs.axhline(0, color="#888", lw=1)  # board front
    axs.axhline(G.NECK_THICK, color="#654", lw=1)  # rear
    for f in A.FINGERS:
        js = fingers[f].joints()
        axs.plot([p[0] for p in js], [p[1] for p in js], "-o",
                 color=FINGER_COLORS[f], lw=2, ms=4, zorder=3)
    axs.plot([p[0] for p in tj], [p[1] for p in tj], "--o",
             color="#8f8", lw=1.5, ms=4, zorder=2)
    axs.set_title("SIDE (depth; neck slab shaded, thumb should be behind)",
                  color="#ddd")
    axs.set_xlabel("x (mm)", color="#999")
    axs.set_ylabel("y depth (mm; +y behind board)", color="#999")
    axs.invert_xaxis()
    axs.tick_params(colors="#666")

    # ---- Metrics box ----
    c = result.costs
    lines = [
        f"STATUS: {result.status}",
        f"max contact: {result.max_contact_mm:.1f} mm",
        f"collision pen: {result.collision_penetration_mm:.1f} mm",
        f"board pen: {result.board_penetration_mm:.1f} mm",
        f"joint viol: {len(result.joint_limit_violations)}",
        f"thumb behind neck: {result.thumb_behind_neck}",
        f"naturalness: {result.naturalness:.2f}",
        "— costs —",
        f"contact {c.contact_error:.1f}  strain {c.strain_cost:.2f}",
        f"splay {c.splay_cost:.2f}  couple {c.coupling_cost:.2f}",
        f"cross {c.crossing_cost:.1f}  thumb {c.thumb_support_cost:.2f}",
        f"total {c.total:.1f}",
    ]
    color = "#6f6" if result.status == "OK" else "#f66"
    axf.text(0.02, 0.98, "\n".join(lines), transform=axf.transAxes,
             va="top", ha="left", color=color, fontsize=8,
             family="monospace",
             bbox=dict(facecolor="#000", alpha=0.6, edgecolor=color))

    fig.tight_layout()
    fig.savefig(path, dpi=90, facecolor="#0b0b10")
    plt.close(fig)
