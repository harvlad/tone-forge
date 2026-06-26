# Structural-Role Classifier — Design

**Status:** Proposed (Phase 5 directive, 2026-06-21)
**Predecessor:** H2 specification frozen at `backend/h2_specification.md`.
**Successor:** Implementation as `tone_forge/song_form/role_classifier.py` (out of scope here).

---

## 0. Scope (what this is, what this is not)

This is **not** a verse/chorus/bridge classifier. The current corpus has no
calibrated evidence to support form-name inference. The user directive
explicitly forbids that step.

The classifier consumes only what H2 demonstrably measures:

> Per-section chord-trigram recurrence — how much of a section's chord
> material participates in the song's globally recurring n-gram inventory.

From that signal it emits one of three **structural-role labels**:

| Role          | Operational meaning (purely H2-based)                                     |
|---------------|---------------------------------------------------------------------------|
| `ANCHOR`      | Highly recurring material — represents the song's recurring identity.     |
| `DEVELOPMENT` | Moderately recurring — derived from anchor material but not the anchor.   |
| `UNIQUE`      | Little or no recurrence — intros, bridges, transitions, one-off events.   |

A future milestone may map `ANCHOR → probable chorus`, etc. **That mapping
is out of scope.** This phase exposes the rails JAM can use today for
navigation, looping, and practice workflows.

Inputs (only):

- H2 per-section vector
- H2 song-level statistic (`h2_sep`)
- Section ordering
- Section count

Inputs NOT used:

- vocal RMS, drum density, chord SSM, lyrics, energy heuristics,
  song-form labels.

---

## A. Distribution analysis

H2 was extracted for all 11 corpus bundles (6 canonical calibration set,
5 held-out validation set per `corpus_freeze.md`). Per-song summary:

```
slug             N    min    med   mean    max  h2_sep  #0.0  #>=.9  gap_t-b
stairway        12  0.000  0.367  0.424  1.000   0.808     3      2    0.781   [CAL]
hotel_calif      9  0.000  0.500  0.413  1.000   0.845     3      1    0.778   [CAL]
wish_you         8  0.000  0.303  0.275  0.564   0.670     2      0    0.504   [CAL]
romance         15  0.000  0.333  0.430  1.000   0.837     4      3    0.833   [CAL]
sex_on_fire     20  0.000  0.648  0.564  1.000   0.702     5      6    0.952   [CAL]
wmaa             8  0.565  0.861  0.831  1.000   0.198     0      3    0.413   [CAL]
ent_sandman     17  0.000  0.250  0.419  1.000   0.931     5      3    0.929   [VAL]
skinny_love     16  0.000  0.450  0.527  1.000   0.738     3      5    0.920   [VAL]
get_lucky       18  0.000  0.600  0.543  1.000   0.786     6      7    1.000   [VAL]
humble          18  0.000  0.667  0.653  1.000   0.448     1      5    0.650   [VAL]
bad_guy         16  0.000  0.267  0.454  1.000   0.917     5      5    0.983   [VAL]
```

(`#0.0` = exact-zero sections; `#>=.9` = high-recurrence sections;
`gap_t-b` = mean of top-tertile minus mean of bottom-tertile.)

Pooled histogram across all 157 sections:

```
  [0.00, 0.10)   37 (23.6%)  ###########
  [0.10, 0.20)    3 ( 1.9%)
  [0.20, 0.30)   17 (10.8%)  #####
  [0.30, 0.40)    9 ( 5.7%)  ##
  [0.40, 0.50)    6 ( 3.8%)  #
  [0.50, 0.60)   13 ( 8.3%)  ####
  [0.60, 0.70)   16 (10.2%)  #####
  [0.70, 0.80)    8 ( 5.1%)  ##
  [0.80, 0.90)    8 ( 5.1%)  ##
  [0.90, 1.00)   40 (25.5%)  ############
  Exact 0.0:      37 (23.6%)
  Exact 1.0:      39 (24.8%)
```

### Key findings

1. **The pooled distribution is strongly bimodal.** ~49% of all sections
   land in the extreme bins `[0.0, 0.1)` or `[0.9, 1.0]`. Exact-0 and
   exact-1 alone account for 48% of sections. The "middle ground" is
   genuinely uncommon. This validates the three-bucket structural model:
   most sections are either clearly recurring or clearly unique.

2. **Per-song gap_t-b ≥ 0.65 for 10 of 11 songs.** Songs naturally
   separate their sections into a high-recurrence and low-recurrence
   group. The lone exception is **wmaa** (gap 0.413, min H2 0.565), a
   uniformly-recurrent punk song with no genuinely UNIQUE section.

3. **Wmaa is a categorical outlier and must be handled explicitly.**
   Its `h2_sep` (0.198) is half the next-lowest (humble, 0.448). It is
   the only bundle where every section participates in the recurring
   inventory. Any classifier that forces a tertile split mis-labels it.

4. **wish_you is a milder version of the same problem.** Its `max(H2)`
   is 0.564 — no section crosses a "natural anchor" threshold ≥ 0.66.
   Yet the song does have a dominant motif; the 0.564 section is the
   recurring guitar figure. Calling no section ANCHOR would lose useful
   structure.

5. **Fixed absolute thresholds are viable** *because* the pooled
   distribution is bimodal — most sections fall comfortably inside an
   extreme bin. But fixed thresholds must be augmented with **two
   escapes**: uniform-song (wmaa) and no-natural-anchor (wish_you).

---

## B. Classifier alternatives

Four families were evaluated; pros, failure modes, and per-song
behaviour observed on the canonical 6:

### 1. Fixed absolute thresholds

Rule: `ANCHOR if h ≥ 0.66`, `UNIQUE if h < 0.33`, else `DEVELOPMENT`.

| Pro | Con |
|---|---|
| Stable across songs — same threshold means the same thing globally. | wish_you gets ZERO anchors (max H2 = 0.564 < 0.66). |
| Exploits the bimodal distribution; few middle-band sections at all. | wmaa: 6 of 8 → ANCHOR is correct, but the two h≈0.6 sections drop to DEVELOPMENT despite the song being uniformly anchor-shaped. |
| Single tuning surface (two constants). | Needs explicit escapes for the two failure modes above. |
| Trivial to explain to UI / users. | |

Observed cross-corpus split: `A 42.7% · D 21.0% · U 36.3%`.

### 2. Percentile-based (tertile by rank)

Rule: Sort sections; bottom third → UNIQUE, top third → ANCHOR, middle
→ DEVELOPMENT.

| Pro | Con |
|---|---|
| Every song gets equal A/D/U distribution. | **Forces wmaa to label its 0.565 and 0.609 sections as UNIQUE** — they are emphatically not unique. |
| Auto-normalizes per song (handles wish_you cleanly: top sections → ANCHOR even if absolute H2 is moderate). | A song that genuinely has no anchor (e.g. all sections H2 ≈ 0.0) still gets forced anchors. |
| No threshold tuning. | Equal-distribution assumption is anti-evidence: the pooled distribution is bimodal, not uniform. |
| | Confidence becomes meaningless — every song looks equally certain. |

Observed cross-corpus split: `A 31.2% · D 37.6% · U 31.2%`.

### 3. Ranking-based assignment (top-K / bottom-K)

Rule: Top K sections by H2 → ANCHOR; bottom K → UNIQUE; rest → DEVELOPMENT. K typically `max(1, ⌊N/3⌋)`.

| Pro | Con |
|---|---|
| Guarantees at least one ANCHOR per song (rescues wish_you). | Same failure mode as percentile on wmaa: forces fake UNIQUEs. |
| Simple to implement. | K is arbitrary; sensitivity to N is high for short songs (N=8 → K=2). |
| Confidence still meaningful from the actual H2 value. | Doesn't honour the bimodal distribution. |

Behaviourally near-identical to percentile for these corpus sizes.

### 4. Clustering (1D k-means, k=3)

Rule: Run k-means on each song's H2 vector with k=3; sort centers and
label the lowest cluster UNIQUE, middle DEVELOPMENT, top ANCHOR.

| Pro | Con |
|---|---|
| Adapts cluster boundaries to each song. | Degenerates when distribution is unimodal: on wmaa it places centers at 0.59/0.82/1.0 and **labels h=0.565 as UNIQUE** — same failure as percentile. |
| Naturally finds bimodal splits where they exist. | Cluster identity is unstable: wish_you's k-means centers were `0.0/0.3/0.5`, calling 0.56 an ANCHOR — but that's a low absolute value being elevated by relative position alone. |
| Non-parametric beyond k. | Confidence is opaque (distance to cluster mean isn't probability). |
| | k-means on 8–20 1D points is overkill machinery. |

Observed cross-corpus split: `A 33.1% · D 36.3% · U 30.6%`.

### Cross-classifier comparison summary

```
classifier   A%      D%      U%      wmaa OK?    wish_you OK?
fixed        42.7    21.0    36.3    ✗ (loses) ✗ (no anchor)
percentile   31.2    37.6    31.2    ✗ (loses) ✓
ranking      ~31     ~37     ~31     ✗ (loses) ✓
kmeans1d     33.1    36.3    30.6    ✗ (loses) partial
```

**No single pure family handles both edge cases.** The recommended
design is a **hybrid: fixed thresholds + two narrow escape rules** —
absolute-threshold semantics in the common case, with deterministic
overrides where the data demands it.

---

## C. Recommended design: `HYBRID_FIXED_RESCUE`

```python
@dataclass(frozen=True)
class RoleThresholds:
    """All constants live here. Defaults calibrated on canonical 6;
    validation on held-out 5 holds without retuning (see §D)."""
    anchor_floor:   float = 0.66   # H2 >= → ANCHOR by default
    unique_ceiling: float = 0.20   # H2 < → UNIQUE by default (excluding exact 0.0)
    uniform_floor:  float = 0.25   # h2_sep < → "uniform song" escape engaged
    uniform_anchor_threshold: float = 0.50  # in uniform-song mode

@dataclass(frozen=True)
class RoleDecision:
    role: Literal["ANCHOR", "DEVELOPMENT", "UNIQUE"]
    confidence: float    # 0..1; see formulas below

def classify_roles(
    per_section_h2: tuple[float, ...],
    h2_sep: float,
    t: RoleThresholds = RoleThresholds(),
) -> tuple[RoleDecision, ...]:
    ...
```

### Decision rule

For each section with value `h`:

```
# ---- Escape 1: uniform song ----
if h2_sep < t.uniform_floor:
    damp = h2_sep / t.uniform_floor
    if h >= t.uniform_anchor_threshold:
        return ANCHOR, conf = clip(h*damp + (1-damp)*0.5, 0, 1)
    else:
        return DEVELOPMENT, conf = damp

# ---- Standard path ----
# Pre-compute once per song:
has_natural_anchor = any(h >= t.anchor_floor for h in per_section_h2)
rescue_idx = argmax(per_section_h2) if (not has_natural_anchor
                                         and max(per_section_h2) > 0.0)
                                    else None

# Per section:
if h == 0.0:
    return UNIQUE, conf = 1.0                  # absolute floor
if h >= t.anchor_floor:
    return ANCHOR, conf = h                    # natural anchor
if section_index == rescue_idx:
    return ANCHOR, conf = h * 0.75             # ---- Escape 2: rescue ----
if h < t.unique_ceiling:
    return UNIQUE, conf = 1.0 - h
return DEVELOPMENT, conf = 1.0 - abs(h - 0.5) * 2.0
```

### Why this shape

| Failure mode the data showed | Handled by |
|---|---|
| Bimodal pooled distribution (49% in extremes) | Fixed absolute thresholds in standard path. |
| Exact-0 sections (23.6%, semantically unambiguous) | Hard floor: `h == 0.0 → UNIQUE` regardless. |
| Uniform-song (wmaa, h2_sep < 0.25) | Escape 1: forces all-ANCHOR with damped confidence. |
| Songs with no section ≥ anchor_floor (wish_you) | Escape 2: argmax rescue, one ANCHOR per song. |
| Confidence interpretability | Linear in H2 for ANCHOR/UNIQUE; peaks at H2=0.5 for DEVELOPMENT. |

### Confidence semantics

| Role | Formula | Range |
|---|---|---|
| `ANCHOR` (natural) | `h` | `[0.66, 1.0]` |
| `ANCHOR` (rescue) | `h * 0.75` | `(0.0, 0.495]` — explicitly low, signals "best available" |
| `ANCHOR` (uniform) | `clip(h*damp + (1-damp)*0.5, 0, 1)` | typically `[0.4, 0.95]` |
| `UNIQUE` (exact 0) | `1.0` | exact |
| `UNIQUE` (low) | `1 - h` | `(0.80, 1.0)` |
| `DEVELOPMENT` (standard) | `1 - 2·|h - 0.5|` | `(0, 1.0]`, peaks at 0.5 |
| `DEVELOPMENT` (uniform) | `damp` | `< 1.0`, signals uncertain song |

UI guidance: confidence `< 0.5` should be rendered as a tentative
badge. Confidence `< 0.3` (only possible via the rescue rule) should
visibly flag "weak structural evidence — interpret with caution."

---

## D. Corpus evaluation

Per-section output of the recommended classifier, all 11 corpus
bundles. Format per section: `H2 / role-letter / confidence`.
`A` = ANCHOR, `D` = DEVELOPMENT, `U` = UNIQUE.

### D.1 Canonical (calibration set)

```
stairway          h2_sep=0.808
  0.62/D/0.75  0.33/D/0.67  0.25/D/0.50  0.40/D/0.80  0.44/D/0.89  0.29/D/0.57
  0.00/U/1.00  0.00/U/1.00  0.00/U/1.00  0.75/A/0.75  1.00/A/1.00  1.00/A/1.00

hotel_calif       h2_sep=0.845
  0.22/D/0.43  0.67/A/0.67  0.67/A/0.67  0.00/U/1.00  0.00/U/1.00
  0.50/D/1.00  1.00/A/1.00  0.67/A/0.67  0.00/U/1.00

wish_you          h2_sep=0.670   ←   no-natural-anchor song (escape 2 engaged)
  0.33/D/0.67  0.25/D/0.50  0.56/A/0.42 ←rescue   0.00/U/1.00
  0.44/D/0.89  0.27/D/0.55  0.33/D/0.67  0.00/U/1.00

romance           h2_sep=0.837
  0.33/D/0.67  0.33/D/0.67  0.29/D/0.57  0.67/A/0.67  0.00/U/1.00
  1.00/A/1.00  1.00/A/1.00  0.00/U/1.00  0.17/U/0.83  0.67/A/0.67
  0.00/U/1.00  1.00/A/1.00  0.50/D/1.00  0.50/D/1.00  0.00/U/1.00

sex_on_fire       h2_sep=0.702
  0.29/D/0.57  0.60/D/0.80  0.86/A/0.86  0.00/U/1.00  0.00/U/1.00
  1.00/A/1.00  0.00/U/1.00  0.87/A/0.87  0.00/U/1.00  0.70/A/0.70
  0.33/D/0.67  1.00/A/1.00  1.00/A/1.00  1.00/A/1.00  0.38/D/0.77
  1.00/A/1.00  0.50/D/1.00  0.75/A/0.75  1.00/A/1.00  0.00/U/1.00

wmaa              h2_sep=0.198   ←   uniform song (escape 1 engaged)
  1.00/A/0.90  0.75/A/0.70  1.00/A/0.90  0.57/A/0.55
  1.00/A/0.90  0.61/A/0.59  0.83/A/0.76  0.89/A/0.81
```

#### Highlights

- **stairway** correctly identifies the climactic final three sections
  (the "and she's buying a stairway to heaven" refrain) as ANCHOR. The
  three exact-zero intro sections become UNIQUE. The intervening
  development is correctly DEVELOPMENT with smoothly varying confidence.
- **wish_you** triggers the rescue escape: section 2 (h=0.564) becomes
  ANCHOR with confidence 0.42 — the low confidence is by design,
  signalling "weak structural evidence." This is the right behaviour:
  JAM can still surface a loopable region while not claiming certainty.
- **wmaa** correctly produces all-ANCHOR. Confidences damped by the
  uniformity factor `h2_sep / uniform_floor = 0.79`, signalling "song
  is uniformly anchor-shaped, no internal structure to navigate." JAM
  can use this to render a single-region practice loop rather than
  section-by-section navigation.
- No section in the calibration set classifies in a way that contradicts
  H2's measured semantics. Ambiguous sections (e.g.
  romance section 8, h=0.17 → UNIQUE with conf 0.83) get confidence
  values that correctly soften the claim.

#### Ambiguous sections (confidence < 0.5)

| Song | Section | H2 | Role | Conf | Note |
|---|---|---|---|---|---|
| stairway | [2] | 0.250 | DEVELOPMENT | 0.50 | Borderline — close to `unique_ceiling`. |
| hotel_calif | [0] | 0.217 | DEVELOPMENT | 0.43 | Borderline — close to `unique_ceiling`. |
| wish_you | [2] | 0.564 | ANCHOR (rescue) | 0.42 | Best anchor of a no-anchor song. |

These three are the only sub-0.5 confidences in the calibration set.
All three are genuinely borderline cases, and the low confidence is
the correct signal to the UI.

### D.2 Validation (held-out set)

```
ent_sandman       h2_sep=0.931
  0.89/A/0.90  0.00/U/1.00  1.00/A/1.00  0.13/U/0.87  0.75/A/0.75
  0.75/A/0.75  0.00/U/1.00  1.00/A/1.00  0.50/D/1.00  0.00/U/1.00
  0.00/U/1.00  0.00/U/1.00  0.25/D/0.50  0.10/U/0.90  0.50/D/1.00
  0.25/D/0.50  1.00/A/1.00

skinny_love       h2_sep=0.738
  0.80/A/0.80  0.20/D/0.40  1.00/A/1.00  0.00/U/1.00  1.00/A/1.00
  0.40/D/0.80  0.33/D/0.67  1.00/A/1.00  1.00/A/1.00  0.75/A/0.75
  0.00/U/1.00  0.00/U/1.00  0.25/D/0.50  0.50/D/1.00  1.00/A/1.00
  0.20/D/0.40

get_lucky         h2_sep=0.786
  0.00/U/1.00  1.00/A/1.00  0.00/U/1.00  1.00/A/1.00  0.50/D/1.00
  1.00/A/1.00  1.00/A/1.00  0.40/D/0.80  1.00/A/1.00  0.00/U/1.00
  1.00/A/1.00  0.00/U/1.00  0.00/U/1.00  0.60/D/0.80  0.00/U/1.00
  1.00/A/1.00  0.60/D/0.80  0.67/A/0.67

humble            h2_sep=0.448
  0.50/D/1.00  0.50/D/1.00  1.00/A/1.00  0.00/U/1.00  0.67/A/0.67
  0.80/A/0.80  0.50/D/1.00  1.00/A/1.00  1.00/A/1.00  0.25/D/0.50
  0.80/A/0.80  0.25/D/0.50  1.00/A/1.00  0.40/D/0.80  1.00/A/1.00
  0.67/A/0.67  0.67/A/0.67  0.75/A/0.75

bad_guy           h2_sep=0.917
  0.20/D/0.40  1.00/A/1.00  0.75/A/0.75  0.00/U/1.00  1.00/A/1.00
  0.00/U/1.00  1.00/A/1.00  0.00/U/1.00  0.20/D/0.40  0.00/U/1.00
  0.33/D/0.67  0.20/D/0.40  0.92/A/0.92  1.00/A/1.00  0.67/A/0.67
  0.00/U/1.00
```

#### Validation findings

- **Held-out generalization holds with zero threshold retuning.** All
  five extended bundles produce sensible structural splits at the
  default constants. No bundle triggered the uniform escape (no
  validation song has `h2_sep < 0.25`). No bundle required the
  no-natural-anchor rescue (every validation bundle contains at least
  one section with `H2 ≥ 0.66`).
- **bimodality reproduces in the validation set.** ent_sandman, bad_guy,
  get_lucky each have ≥ 30% of sections at exact 0.0 and ≥ 30% at
  exact 1.0 — confirming the canonical-set finding holds out of sample.
- **Ambiguity is sparse.** Only four sections across the validation set
  have confidence < 0.5: bad_guy [0] (0.40), bad_guy [8] (0.40), bad_guy
  [11] (0.40), skinny_love [1] (0.40), skinny_love [15] (0.40). All are
  legitimately near the `unique_ceiling`.
- **humble (h2_sep=0.448) is closest to the uniform-escape boundary.**
  Its 18 sections produce 11 ANCHORs, 7 DEVELOPMENTs, 1 UNIQUE — a
  legitimate "mostly anchor-shaped" song that just clears the uniform
  floor. The classifier doesn't over-commit to all-ANCHOR; the standard
  path is correct here.
- No validation bundle exhibits a classification that contradicts
  the H2 vector or the bimodality observed in distribution analysis.

---

## E. Decision

**Recommended classifier:** the `HYBRID_FIXED_RESCUE` design specified
in §C — fixed absolute thresholds with two narrow deterministic escape
rules (uniform-song and no-natural-anchor). Defaults:

- `anchor_floor = 0.66`
- `unique_ceiling = 0.20`
- `uniform_floor = 0.25`
- `uniform_anchor_threshold = 0.50`

These constants are calibrated on the canonical 6 and validated on the
held-out 5 without retuning. They are exposed as a frozen dataclass
(`RoleThresholds`) so tuning has a single, auditable surface.

### Why this over the alternatives

| Property | Recommended | Fixed | Percentile | Ranking | K-means |
|---|---|---|---|---|---|
| Handles uniform-song (wmaa) | ✓ | ✗ | ✗ | ✗ | ✗ |
| Handles no-natural-anchor (wish_you) | ✓ | ✗ | ✓ | ✓ | partial |
| Honours bimodal distribution | ✓ | ✓ | ✗ | ✗ | partial |
| Confidence is interpretable | ✓ | partial | ✗ | partial | ✗ |
| Stable across N (section count) | ✓ | ✓ | partial | ✗ | partial |
| Single tuning surface | ✓ (one dataclass) | ✓ | n/a | ✓ | ✗ |
| Tests against held-out set | ✓ (zero retuning) | partial | partial | partial | partial |

---

## F. Test surface (sketch for the implementation milestone)

Hermetic unit tests against synthesised H2 vectors:

1. All-zero vector → all UNIQUE with confidence 1.0.
2. All-one vector → all ANCHOR with confidence 1.0.
3. Vector with `h2_sep = 0.1` → uniform escape; all ANCHOR/DEVELOPMENT,
   damped confidences.
4. Vector where `max(H2) = 0.5` → rescue rule fires; exactly one ANCHOR
   with confidence ≤ 0.5; others UNIQUE/DEVELOPMENT per fixed rules.
5. Bimodal vector `(0.0, 0.0, 1.0, 1.0)` → two UNIQUE conf 1.0, two
   ANCHOR conf 1.0; no DEVELOPMENT.
6. Borderline vector `(0.65, 0.66)` → first DEVELOPMENT, second ANCHOR
   (threshold boundary).
7. Determinism: same input twice → same output, identical floats.

Canonical-corpus gate test mirroring `test_h2_canonical_corpus.py`:

8. Each of the 6 canonical bundles produces the role vector documented
   in §D.1 (frozen as an anchor table).

Held-out validation test mirroring `test_h2_extended_corpus.py`:

9. Each of the 5 extended bundles classifies with no exception, every
   confidence ∈ `[0, 1]`, and at least one ANCHOR per song.

---

## G. Out of scope (for this phase)

- **Form labels** (verse / chorus / bridge / pre-chorus / etc.). The
  directive is explicit: do not invent these.
- **Cross-stem aggregation.** The classifier consumes the single H2
  vector emitted by the song-level extractor; per-stem features and
  the multi-stem guidance-mode work described in
  `~/.claude/plans/effervescent-twirling-neumann.md` belong to a
  separate milestone.
- **UI rendering.** JAM integration is the next phase (todo item
  "Reintroduce form labels into UI" — should be renamed to
  "Reintroduce structural roles into UI").
- **Threshold auto-tuning** from user behaviour. The constants are
  frozen at the design defaults; any future re-tune requires
  re-running this evaluation on a published corpus delta.

---

## H. Implementation note

When this design is implemented (`tone_forge/song_form/role_classifier.py`),
the public API should be:

```python
from tone_forge.song_form.h2 import H2Result
from tone_forge.song_form.role_classifier import (
    RoleDecision, RoleThresholds, classify_roles,
)

result: H2Result = extract_h2(bundle)
roles: tuple[RoleDecision, ...] = classify_roles(
    result.per_section, result.h2_sep
)
```

The function is pure, deterministic, no I/O, and the only mutable
input is the `RoleThresholds` instance (frozen dataclass; passed by
value).
