# M8 findings — rendering + HTML (the visual gate)

**1. What did we build?**
- `build_render_states.py`: extracts the demo phrase's knot states (naive + trajopt) into
  the frozen `mpfb_render` input format, ZEROING the metacarpal DoF (inert in V1, M1) and
  attaching `fingers_mm` for the render==solver assertion.
- A **phrase-static camera** mode in `mpfb_render` (`__phrase_camera__`): one fixed viewpoint
  for all frames so the hand's position along the neck is comparable frame-to-frame. Default
  Camera A (per-contact tracking) is untouched.
- `build_html.py`: a self-contained `report.html` — score table (naive vs trajopt) + demo
  contact sheet (naive row over trajopt row), PNGs embedded as base64.

**2. What did we learn?**
- **Build gate PASS**: `report.html` opens standalone (15 MB, no external assets), shows the
  demo render + phrase-score table. render==solver = **0.000 mm on all 8 frames** — the
  invariant survives the whole pipeline into pixels.
- A rendering problem M8 exposed and fixed: the frozen Camera A re-centres on each note, so a
  motion contact sheet with it would hide the very thing being judged (the hand always looks
  centred). The phrase-static camera is a genuine M8 need, added without disturbing Camera A.

**3. The visual gate itself (HONEST — NOT self-passed).**
The quantitative M7 win does NOT read clearly to the eye, and the renders surface a new
concern:
- The trajopt win on `one_shift_scale` is a ~31 mm root move avoided between fret 9 and fret
  11 (naive 2 shifts → trajopt 1). At neck scale, adjacent high frets are ~1 cm apart, so
  naive_2 (fret 9) and naive_3 (fret 11) look nearly identical — the "extra shift" is barely
  perceptible. The quantitative win is real but visually subtle.
- **Both planners' high-position poses (frets 9–11) look anatomically weak** — the hand reads
  as detached below the neck with the palm rotated up, fingers not clearly pressing. This is
  the FROZEN static solver's quality at high frets (naive one_shift worst contact 3.73 mm,
  near the 6 mm tol), NOT a planner difference — trajopt tightens contact (0.12 mm) but the
  gross pose/orientation is the same. Out of M8 scope to fix (solver frozen), but a real,
  surfaced finding.

**4. Assumptions disproved.**
The implicit assumption that the shift-economy win would be visually obvious. It is not, at
this camera and phrase. And the assumption that the static solver's poses are believable
everywhere — they weaken markedly at high frets.

**5. What should change (decisions for the user — NOT done unilaterally):**
- The visual gate on `one_shift_scale` does not persuade on its own. If the visual gate
  matters for the verdict, better demonstrators are: a wider positional-leap phrase (e.g. a
  shift across many frets, where the avoided move is large) or an animated GIF interpolating
  knots (motion is more legible than stills).
- The high-fret pose weakness is a static-solver finding worth logging against the frozen
  solver — it caps believability regardless of the motion planner. Revisit only if/when the
  solver is unfrozen.
- Renders/HTML are gitignored (regenerable, 15 MB); the generators + this report are the
  committed record.

M8 BUILD done-when is satisfied (self-contained HTML, demo render + score table, invariant
intact). The VISUAL gate (gate 8) is the user's call; my honest read is that it neither
clearly confirms nor is needed to confirm the M7 verdict — the numbers remain the trustworthy
signal.
