# Rigged Anatomical Hand — Asset Research & Reference Pipeline

Status: research complete, reference build in progress.
Supersedes the procedural-2D approach (declared FAILED; see
`docs/FRETTING_HAND_SPIKE.md` for its post-mortem). Principle: **do not draw a
hand — pose an existing anatomically correct rigged hand and derive the JAMN
silhouette from it.**

---

## 1. Top candidates

### A. Blender Studio "Human Base Meshes" bundle — RECOMMENDED
- What: 17 character meshes from Blender Studio, including photorealistic
  male/female bodies and separate body parts, with basic armature rigging on the
  realistic bodies (full finger chains: per-finger MCP/PIP/DIP + articulated
  thumb, skinned).
- License: **CC0** — explicitly free for commercial use, no attribution, no
  redistribution restrictions. The only candidate with zero license risk on both
  the asset AND anything derived from it (pose data, silhouettes, exports).
- Format: native `.blend` (Blender asset bundle) — the exact tool we want as the
  reference solver. Direct download from blender.org demo files
  (v1.4.1, Jan 2026).
- Skeleton quality: deformation-grade basic rig (not a film-grade Rigify control
  rig, but correct hierarchy + realistic adult proportions + clean skinning).
  IK can be added per finger in a few lines of bpy.
- Verdict: best license, best format, adequate rig. **Chosen.**

### B. MakeHuman / MPFB2 (MakeHuman Plugin For Blender)
- What: parametric human generator; exports are **CC0 by explicit license
  grant** (official unmodified MakeHuman export). Full finger skeleton
  (game/default rigs include all phalanges), adult proportions adjustable.
- License: CC0 on exported models — commercially safe.
- Cost: extra tooling (plugin + asset download), rig is game-oriented; more
  setup than A for the same outcome.
- Verdict: strong fallback if A's basic rig proves too coarse; same legal
  safety.

### C. Mixamo (Adobe) auto-rigged character
- What: web auto-rigger; rigs include finger bones; royalty-free commercial use
  *within a project*.
- Disqualifiers: characters "cannot be redistributed as standalone assets" —
  murky for derived pose-data files we'd commit to the repo; web-only pipeline
  (no API), not scriptable; Adobe account dependency.
- Verdict: rejected — license friction + non-reproducible pipeline.

### Rejected earlier / for completeness
- **MANO** (MPI parametric hand): best anatomy in the field, but license
  prohibits commercial use outright. Blocked (verified license page).
- **Sketchfab/BlendSwap one-off hands**: quality and license vary per item; no
  single vetted asset beats A/B, and provenance is weaker.
- **ManoMotion**: tracking SDK, not a pose source. Irrelevant here.

Sources: [MakeHuman license explanation](http://www.makehumancommunity.org/content/license_explanation.html),
[Blender Studio Human Base Meshes](https://www.cgchannel.com/2023/06/download-blender-studios-free-human-base-meshes/),
[bundle downloads](https://download.blender.org/demo/asset-bundles/human-base-meshes/),
[Mixamo FAQ](https://helpx.adobe.com/creative-cloud/faq/mixamo-faq.html),
[MANO license](https://mano.is.tue.mpg.de/license.html).

## 2. Recommendation

**Blender Studio Human Base Meshes (CC0) + Blender as the reference solver.**
Isolate the left hand + forearm from the realistic male mesh; keep its finger
skeleton; add per-finger IK targets. MakeHuman/MPFB2 is the vetted fallback.

## 3. Reference pipeline (this spike)

```
GuitarVoicing fingerings (G, D, Em, C)
        ↓ (string, fret, finger) — existing Swift data, exported as JSON
physical contact points, guitar space (mm):
        x = 648 · (1 − 2^(−fret/12)) minus 30% of slot; y = string offset
        (nut width 43mm → string span 35mm nut / 52mm saddle; board radius
        ignored at this scale); z = 0 at board plane
        ↓
Blender scene (bpy script, headless):
        physically dimensioned neck (scale length, taper, wire positions)
      + CC0 hand mesh with finger skeleton
      + per-finger IK targets at contact points; unused fingers relaxed curl;
        thumb posed opposing the middle MCP behind the neck (no target)
        ↓  manual pose refinement in Blender allowed for the 4 canonical chords
render: front orthographic camera (neck horizontal, fingers approach from
        below, thumb OCCLUDED by the neck), flat emission fill + Freestyle
        outline → JAMN-style silhouette PNGs
export: per-chord bone transforms + fingertip/palm/wrist/thumb positions → JSON
        (canonical anatomical pose data, committed to repo)
animate: G → D → Em → C by interpolating bone transforms; silhouette follows
```

Real-guitarist validation (MediaPipe Hand Landmarker, offline, Apache-2.0) is
queued after the rigged poses exist: record G/D/Em/C from the same camera
orientation, extract joints, compare spread/MCP line/PIP-DIP flexion/wrist
angle. Never runtime.

## 4. iOS strategy (decided AFTER the reference proves out)

Hypothesis to test (matches the user's): Blender offline → canonical anatomical
pose data → lightweight iOS skeleton interpolation → projected 2D silhouette.
Alternatives to score once poses exist: RealityKit runtime skeleton (needs
iOS 18), precomputed vector silhouettes + morphing (loses articulation),
full 3D renderer (rejected on weight). Left-handed = mirror the pose data.
