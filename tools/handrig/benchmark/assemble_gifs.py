"""Animation demonstrator — step 2: assemble synchronized naive|trajopt GIFs.

Composites the per-frame renders side by side (NAIVE | TRAJOPT), identical
crop/scale/labels for both, and writes:
  - naive_vs_trajopt.gif            (real speed, 12 fps)
  - naive_vs_trajopt_slow.gif       (0.5x, 6 fps — same frames)
  - naive_vs_trajopt_diagnostic.gif (real speed + hand-root position track)

Both panels get EXACTLY the same treatment. No per-planner effects. The
diagnostic track plots state[0] (hand-root x) mapped to the neck axis, with a
fading trail, so a shift reads as the marker jumping and a commitment reads as
it holding.
"""
from __future__ import annotations
import os, sys, json
from PIL import Image, ImageDraw, ImageFont
import imageio.v2 as imageio

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
FRAMES = os.path.join(RES, "anim_frames")
PANEL = 460            # downscaled panel size (square)
GAP = 8
LABEL_H = 40
TRACK_H = 54
CAP_H = 64
BG = (14, 14, 18)
FG = (216, 216, 224)
DIM = (120, 124, 140)


def font(sz):
    for p in ("/System/Library/Fonts/SFNSMono.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/Library/Fonts/Arial.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


def panel(planner, fi):
    p = os.path.join(FRAMES, f"best-{planner}__{fi:03d}.png")
    im = Image.open(p).convert("RGB").resize((PANEL, PANEL), Image.LANCZOS)
    return im


def base_frame(fi, cap):
    W = PANEL * 2 + GAP
    H = LABEL_H + PANEL + CAP_H
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)
    f = font(20); fs = font(14)
    d.text((PANEL // 2 - 90, 8), "NAIVE  —  2 shifts", font=f, fill=FG)
    d.text((PANEL + GAP + PANEL // 2 - 96, 8), "TRAJOPT  —  1 shift", font=f, fill=FG)
    canvas.paste(panel("naive", fi), (0, LABEL_H))
    canvas.paste(panel("trajopt", fi), (PANEL + GAP, LABEL_H))
    d.line([(PANEL + GAP // 2, LABEL_H), (PANEL + GAP // 2, LABEL_H + PANEL)], fill=(40, 40, 48))
    # caption footer (actual measured numbers)
    cy = LABEL_H + PANEL + 6
    n, t = cap["naive"], cap["trajopt"]
    d.text((6, cy), f"root {n['root_travel_mm']:.0f}mm   tip {n['fingertip_travel_mm']:.0f}mm   "
                    f"score {n['movement_score']:.3f}", font=fs, fill=DIM)
    d.text((PANEL + GAP + 6, cy), f"root {t['root_travel_mm']:.0f}mm   tip {t['fingertip_travel_mm']:.0f}mm   "
                                  f"score {t['movement_score']:.3f}", font=fs, fill=DIM)
    d.text((6, cy + 22), "one_shift_scale  ·  linear state-space interp between solved knots  ·  "
                         "12fps real time", font=fs, fill=(90, 92, 108))
    return canvas


def with_track(canvas, fi, track, cur_t, times):
    """Add a hand-root position track under each panel with a fading trail."""
    W = canvas.width
    strip = Image.new("RGB", (W, TRACK_H), BG)
    d = ImageDraw.Draw(strip)
    xs_all = [x for p in track for x in track[p]]
    lo, hi = min(xs_all), max(xs_all)
    span = (hi - lo) or 1.0
    for col, planner in ((0, "naive"), (PANEL + GAP, "trajopt")):
        x0 = col + 16; x1 = col + PANEL - 16; y = TRACK_H // 2 + 6
        d.line([(x0, y), (x1, y)], fill=(50, 50, 60), width=2)
        d.text((col + 16, 2), "hand-root position (neck axis)", font=font(11), fill=(90, 92, 108))
        # fading trail up to current frame
        for j in range(fi + 1):
            frac = (track[planner][j] - lo) / span
            px = x0 + frac * (x1 - x0)
            age = fi - j
            fade = max(30, 210 - age * 22)
            r = 3 if j == fi else 2
            colr = (fade, fade, 90) if j == fi else (fade // 2, fade // 2, fade // 2)
            if j == fi:
                colr = (250, 220, 80)
            d.ellipse([px - r, y - r, px + r, y + r], fill=colr)
    out = Image.new("RGB", (W, canvas.height + TRACK_H), BG)
    out.paste(canvas, (0, 0)); out.paste(strip, (0, canvas.height))
    return out


def main():
    meta = json.load(open(os.path.join(RES, "anim_meta.json")))
    cap = meta["caption"]; track = meta["track"]; times = meta["times"]
    n = meta["n_frames"]

    plain = [base_frame(fi, cap) for fi in range(n)]
    diag = [with_track(base_frame(fi, cap), fi, track, times[fi], times) for fi in range(n)]

    def save(path, frames, fps):
        imageio.mimsave(path, [f for f in frames], duration=1.0 / fps, loop=0)
        print(f"wrote {path}  ({len(frames)} frames @ {fps}fps)")

    save(os.path.join(RES, "naive_vs_trajopt.gif"), plain, 12)
    save(os.path.join(RES, "naive_vs_trajopt_slow.gif"), plain, 6)
    save(os.path.join(RES, "naive_vs_trajopt_diagnostic.gif"), diag, 12)


if __name__ == "__main__":
    main()
