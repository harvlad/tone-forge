# Animated naive vs trajopt demonstrator (observation only)

Visualization of the ACTUAL solved trajectories. No planner, solver, benchmark,
metric, scoring, camera, geometry, or renderer-appearance change was made to
produce this — it renders exactly what naive and trajopt already produced.

## Deliverables (in `results/`)
- `naive_vs_trajopt.gif` — real speed (12 fps). **Authoritative.**
- `naive_vs_trajopt_slow.gif` — 0.5× (6 fps), same frames.
- `naive_vs_trajopt_diagnostic.gif` — real speed + a hand-root position track.

## Source trajectories
- naive: `results/movement_report.json` → `trajectories.one_shift_scale`
- trajopt: `results/trajopt_report.json` → `trajectories.one_shift_scale`
Both are the committed, frozen planner outputs. Not re-solved for the animation.

## Interpolation (identical for both planners)
Piecewise **linear (LERP) in 30-DoF planner state space** between consecutive
solved knots: `state(t) = (1-a)·knot_i + a·knot_{i+1}`, `a = (t-t_i)/(t_{i+1}-t_i)`.
Before the first / after the last knot the pose is held (the still frames).
**No** easing, anticipation, overshoot, secondary motion, or per-planner
smoothing — the only transform is straight-line blending of the real states.
Metacarpal DoF zeroed (inert in V1); `fingers_mm` = FK of the interpolated
state, so render==solver holds (0.000 mm on all 74 frames).

## Timeline
Notes at t = 0, 0.5, 1.0, 1.5 s (90 bpm); last note held to 2.0 s. 12 fps. 0.5 s
still head + 0.5 s still tail. Both planners share this exact timeline; the GIF
loops. The slow version is the same frames at 6 fps (not a re-timed phrase).

## Diagnostic track
Under each panel: hand-root position = `state[0]` (root x, metres) mapped to the
neck axis, normalized over both planners' full ranges, with a fading trail. A
position shift reads as the marker jumping; commitment reads as it holding.

## Caption (actual measured, from the committed reports)
```
              shifts   root_travel   fingertip   MovementScore
naive           2        91.62 mm     889.31 mm      0.661
trajopt         1        93.54 mm     886.63 mm      0.782
```
Honest nuance shown: trajopt's TOTAL root travel is marginally HIGHER (93.5 vs
91.6 mm) even though it makes one fewer discrete shift — the single big
fret-4→9 move dominates both; the win is the avoided fret-9→11 relocation
(shift count) and the resulting score, not total path length.

## Regenerate
```
python3 build_anim_states.py
blender --background --python ../mpfb_render.py -- \
    results/anim_states.json results/anim_frames
python3 assemble_gifs.py
```

## Known, planner-independent weakness (exposed, not fixed)
At frets ~9–11 both planners inherit the FROZEN static solver's weakness: the
hand reads as detached below the neck with the palm rotated upward, believability
degrades. Identical on both sides; not a planner difference. Left visible on
purpose.
