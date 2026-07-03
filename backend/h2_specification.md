# H2 Signal — Frozen Specification

**Status:** FROZEN as of 2026-06-21 (Step 1 of the post-corpus-freeze
roadmap).

This document is the *contract* the H2 extractor must implement. The
contract is frozen against the numbers in `signal_taxonomy_report.md`
and `/tmp/phase0c_scripts/step4_taxonomy_persection.json`. Any
implementation that does not bit-equivalent-reproduce those per-section
values on the canonical 6 bundles is non-conformant.

The spec is intentionally over-explicit. The extractor is supposed to
be mechanically derivable from this document; design freedom is
elsewhere.

---

## 1. Purpose

H2 is the **per-section chord-trigram recurrence fraction**. It
answers, for one section of a song, the question: *what fraction of
this section's chord-trigrams also appear elsewhere in the song?* It
is the only structural signal Decision B promoted to primary, with
the documented caveat that it is CONDITIONAL (collapses on
arrangement-homogeneous songs whose chord vocab is so narrow that
every trigram already repeats everywhere — the pop-punk failure mode
documented in `signal_taxonomy_report.md` Section C).

H2 is intentionally distinct from H1 (song-level chord-trigram repeat
fraction, scalar). H1 is a confidence modifier; H2 is the per-section
signal the classifier consumes.

---

## 2. Inputs

The extractor consumes a single persisted analysis bundle. No audio
re-decode. No re-running of chord detection.

```python
def extract_h2(bundle: dict) -> H2Result: ...
```

Required bundle fields, all read at the path documented:

| Path                                         | Type       | Used for                                  |
|----------------------------------------------|------------|-------------------------------------------|
| `bundle["chords"]`                           | `list[dict]` | Chord symbol sequence + temporal anchors  |
| `bundle["chords"][i]["start_s"]`             | `float`    | Chord onset in seconds                    |
| `bundle["chords"][i]["end_s"]`               | `float`    | Chord offset in seconds                   |
| `bundle["chords"][i]["symbol"]`              | `str`      | Chord label (e.g. `"Am"`, `"C#sus4"`)     |
| `bundle["sections"]`                         | `list[dict]` | Section boundaries                       |
| `bundle["sections"][j]["start_s"]` *or* `["start_time"]` | `float` | Section start in seconds      |
| `bundle["sections"][j]["end_s"]` *or* `["end_time"]`     | `float` | Section end in seconds        |

**Dual-name section bounds:** the persisted bundle schema is
inconsistent — chords use `start_s`/`end_s`, sections use
`start_time`/`end_time`. The extractor MUST accept both name pairs
for sections (preferring `start_s`/`end_s` when present, falling
back to `start_time`/`end_time`). Chord field names are not dual.

Optional (used only for diagnostic output, never affects H2 values):

- `bundle["sections"][j]["name"]`, `["label"]`, or `["type"]` —
  passed through to the per-section emit for debugging. `type` is
  the section-form label produced by the unified pipeline
  (`"intro"`, `"verse"`, `"chorus"`, etc.).

Fields *not* used: `confidence`, `debug_features`, `midi_stems`,
`detected_key`, `tempo_bpm`. H2 is purely a structural signal computed
over the chord sequence.

---

## 3. Algorithm (deterministic, single-pass)

### 3.1 Chord-symbol → pitch class

Parse each `chord["symbol"]` to its **root pitch class** in `0..11`
(C=0, C#=1, ..., B=11). Handle accidentals and ignore quality:

```
ROOT_TO_PC = {"C":0, "D":2, "E":4, "F":5, "G":7, "A":9, "B":11}
```

Symbol regex (anchored): `^([A-G])([#b♯♭]?)`. Apply `#`/`♯` → +1,
`b`/`♭` → −1, modulo 12.

Failure: if the symbol does not match the regex (e.g. `"N.C."`, `""`,
`None`), emit a sentinel `PC_NONE = -1`. PC_NONE entries are dropped
from the chord-PC sequence (do not appear in trigrams).

### 3.2 Chord-midpoint timestamps

For each chord `c` in `bundle["chords"]`, compute `mid_s = (c["start_s"]
+ c["end_s"]) / 2.0`. Use the midpoint, not the start, to be
consistent with the existing chord-ribbon trimming convention noted in
the planning files.

### 3.3 Per-section chord-PC sequence

For each section `s` (in the persisted order, no resorting):

```
section_pcs = [pc for (c, pc) in zip(chords, root_pcs)
               if pc != PC_NONE
               and s["start_s"] <= mid_s(c) < s["end_s"]]
```

Half-open interval `[start_s, end_s)` (matches the existing chord-lane
trim semantic). Order preserved. Duplicates not collapsed (back-to-back
identical chords legitimately produce a duplicate-PC trigram).

### 3.4 N-gram extraction

```
def ngrams(seq: list[int], n: int) -> list[tuple[int, ...]]:
    return [tuple(seq[i:i+n]) for i in range(len(seq) - n + 1)]
```

- **N-order is a song-level switch keyed on the full chord sequence
  length:** `n = 3 if len(full_pc_seq) >= 6 else n = 2`. Every
  section uses the same `n`, so counts are commensurable. (This
  matches the Step 4 taxonomy script exactly; an earlier draft of
  this spec used `longest_section_pcs >= 3` as the trigger, which
  produced numerically different output on the canonical 6 and was
  retired.)
- If even bigrams are not viable (`len(full_pc_seq) < 2`), H2 is
  not defined; emit `0.0` for every section and `h2_sep = 0.0` with
  `degenerate = True`.

The Step 4 taxonomy harness used `n = 3` exclusively for the canonical
6 (every song's full PC sequence is well above 6 chords). The fallback
path is specified here for defensiveness against future single-chord
songs.

### 3.5 Global multiplicity table — built from the FULL chord sequence

The global multiplicity table is built from n-grams over the **whole
song's concatenated chord-PC sequence**, not from per-section
n-grams. This explicitly includes boundary trigrams that span two
adjacent sections.

```
full_pc_seq = [pc for (mid, pc) in chord_pcs if pc != PC_NONE]
global_counts = Counter(ngrams(full_pc_seq, n))
```

Boundary trigrams matter: a trigram `(p_a, p_b, p_c)` where `p_a` is
the last chord of section A and `(p_b, p_c)` are the first two of
section B is counted in the global table, even though it never appears
in any single section's n-gram list. This is the original Step 4
behaviour and reproducing the canonical anchors requires it.

### 3.6 Per-section H2

For each section `s`:

```
section_ngrams = ngrams(section_pcs[s], n)
if not section_ngrams:
    h2_s = 0.0
else:
    repeated = sum(1 for g in section_ngrams if global_counts[g] >= 2)
    h2_s = repeated / len(section_ngrams)
```

`h2_s ∈ [0.0, 1.0]`. Empty sections (no chord midpoints inside) are
`0.0`, not `NaN`. Sections with all-unique trigrams are `0.0`. Sections
where every trigram has a global twin are `1.0`.

**Abstain sentinel.** The `if not section_ngrams: h2_s = 0.0` branch
folds two semantically distinct states onto the same `0.0` value:

* **Genuine no-recurrence** — the section has `>= n` chord symbols
  but none of its n-grams appear elsewhere in the song.
* **Insufficient chord data** — the section has fewer than `n`
  chord symbols in its window (`_ngrams` returns `[]`), so
  recurrence cannot be tested at all.

The `per_section_insufficient` field (see §4) is authoritative for
the distinction. Downstream classifiers should treat insufficient
sections as an abstain signal, not as evidence of uniqueness.
`per_section` values remain unchanged (`0.0` in both states) so no
canonical-6 anchor moves.

### 3.7 Song-level H2 separability

```
import statistics as stats

per_section = [h2_s for each section]
if len(per_section) < 2:
    h2_sep = 0.0
else:
    mu = stats.fmean(per_section)
    sigma = stats.pstdev(per_section)
    h2_sep = sigma / (mu + 1e-9)
    h2_sep = max(0.0, min(1.0, h2_sep))
```

**Population stdev** (`pstdev`), not sample stdev. Matches the Step 4
harness. The `1e-9` epsilon prevents division by zero when every
section reads `0.0`.

---

## 4. Output

```python
@dataclass(frozen=True)
class H2Result:
    per_section: tuple[float, ...]              # one value per input section, in order
    h2_sep: float                                # clipped to [0, 1]
    n_used: int                                  # 3 (primary) or 2 (fallback)
    degenerate: bool                             # True iff insufficient chord data
    section_names: tuple[str, ...]               # passthrough for diagnostic output
    per_section_insufficient: tuple[bool, ...]   # abstain flag, see §3.6
```

`per_section` length must equal `len(bundle["sections"])`. Order is
preserved.

`h2_sep` is the scalar that participates in song-level classification.

`per_section_insufficient` is a parallel tuple (same length and
ordering as `per_section`). `True` at index `i` means "section `i`
had fewer than `n_used` chord symbols in its window; the matching
`per_section[i]` is a `0.0` sentinel, not a measured recurrence
value". `False` means "recurrence was tested; the value is real".
On the empty-sections early return it is `()`; on the empty-chords
and degenerate early returns it is `(True,) * len(sections)`.

The field is additive over the pre-fix schema: the frozen dataclass
default is `()` so external constructors that predate the field
still compile, and the value tuple never mutates existing
`per_section` values — canonical-6 anchors (§5) hold byte-identical.

---

## 5. Reproducibility contract

The extractor's output on the canonical 6 bundles (per
`corpus_freeze.md` Section A) must match the following anchors, drawn
from `step4_taxonomy_persection.json`:

### Stairway (`73b5931b`) — n_used=3, 12 sections

`per_section[0..11] ≈`
`(0.625, 0.333, 0.250, 0.400, 0.444, 0.286, 0.000, 0.000, 0.000, 0.750, 1.000, 1.000)`

### Hotel California (`07320370`) — n_used=3, 9 sections

`per_section[0..8] ≈`
`(0.217, 0.667, 0.667, 0.000, 0.000, 0.500, 1.000, 0.667, 0.000)`

### Wish You Were Here (`9fb65b01`) — n_used=3, 8 sections

`per_section[0..7] ≈`
`(0.333, 0.250, 0.564, 0.000, 0.444, 0.273, 0.333, 0.000)`

### Romance de Amor (`5365ab83`) — n_used=3, 15 sections

`per_section[0..14] ≈`
`(0.333, 0.333, 0.286, 0.667, 0.000, 1.000, 1.000, 0.000, 0.167, 0.667, 0.000, 1.000, 0.500, 0.500, 0.000)`

### Sex on Fire (`b640c78a`) — n_used=3, 20 sections

`per_section[0..19] ≈`
`(0.286, 0.600, 0.857, 0.000, 0.000, 1.000, 0.000, 0.867, 0.000, 0.696, 0.333, 1.000, 1.000, 1.000, 0.385, 1.000, 0.500, 0.750, 1.000, 0.000)`

### Whats My Age Again (`29b31695`) — n_used=3, 8 sections

`per_section[0..7] ≈`
`(1.000, 0.750, 1.000, 0.565, 1.000, 0.609, 0.833, 0.889)`

**Tolerance:** ±0.001 per entry (rounding artefact from JSON
serialization in the original taxonomy harness). Any larger deviation
is a spec violation that must be explained before the extractor is
accepted.

### Aggregate scalars (from `step4_taxonomy_table.csv`)

| Slug                 | h2_sep |
|----------------------|--------|
| stairway_to_heaven   | 0.808  |
| hotel_california     | 0.845  |
| wish_you_were_here   | 0.670  |
| romance_de_amor      | 0.837  |
| sex_on_fire          | 0.702  |
| whats_my_age_again   | 0.198  |

Same ±0.001 tolerance.

---

## 6. Determinism, performance, no-deps

- **No floating-point order sensitivity.** All summations are integer
  (multiplicity counts). The only float math is `repeated / total`,
  `sigma / (mu + eps)`, and the clip. Python's deterministic int and
  float semantics make the result reproducible across runs and
  machines.
- **No randomness, no hashing-with-salt.** `tuple` as `Counter` key
  uses Python's normal hash; we rely on `Counter` semantics, not on
  iteration order.
- **No NumPy.** The reference algorithm is `collections.Counter` +
  `statistics.fmean` + `statistics.pstdev`. The implementation may use
  NumPy if and only if the canonical-6 anchors still match within
  ±0.001.
- **No I/O beyond the bundle dict.** The extractor takes a dict, not a
  path. Caller is responsible for `json.load`.
- **O(N) in chord count.** Trigram-multiplicity is a single pass.

---

## 7. Out-of-scope for H2 extractor

The extractor is *only* the signal computation. The following are
explicitly out-of-scope and live in a later step:

- Threshold calibration (where on `h2_sep` does "structurally
  classifiable" begin? — Step 5 / classifier territory).
- Combining H2 with D, F, or H1 (those are classifier-level fusion
  decisions, not extractor-level).
- Section labelling (verse/chorus/bridge — that's
  song-form-classifier territory, downstream of the structural
  backbone).
- Failure-mode detection (when to *trust* H2 vs. fall back to D+F).
  H1 is the fallback signal, not part of the H2 extractor.
- Audio-domain features. H2 is structural over the persisted chord
  sequence; the moment audio re-decode enters the pipeline, this spec
  no longer applies.

---

## 8. Failure modes the extractor must handle without crashing

| Input pathology                                          | Expected H2 behaviour                             |
|---------------------------------------------------------|---------------------------------------------------|
| Empty `bundle["chords"]`                                 | `per_section = (0.0,) * len(sections)`, `h2_sep = 0.0`, `degenerate = True` |
| Empty `bundle["sections"]`                               | `per_section = ()`, `h2_sep = 0.0`, `degenerate = True` |
| Section spans zero chords (no midpoint inside)           | `per_section[j] = 0.0`, no exception              |
| Chord symbol parse failure (`N.C.`, `""`, None)          | Chord dropped from PC sequence, no exception      |
| Chord with `start_s > end_s` (corrupt bundle)            | Use midpoint anyway (`(start+end)/2`); no fixup   |
| Sections overlap (corrupt bundle)                        | A chord midpoint may count toward multiple sections — accept it; H2 stays well-defined |
| Section with 1 chord (only after `n` fallback to 2)      | 0 bigrams → `per_section[j] = 0.0`               |
| Entire song's longest section has ≤2 chords              | `n_used = 2` (bigram fallback); per-section computed against bigram multiplicity table |
| Entire song's longest section has ≤1 chord               | `degenerate = True`, all zeros                    |
| Duplicate chord symbol back-to-back                      | Produces a `(p, p, p)` trigram; counted normally  |

No exception is allowed to escape the extractor for any of the above.
Degenerate-but-defined output is always preferable to a raise.

---

## 9. Test surface the extractor must satisfy

(These are not the tests themselves — they are the *checklist* the
extractor-implementation commit must hit.)

1. **Canonical-6 reproducibility test.** For each of the six bundle
   IDs in `corpus_freeze.md` Section A, load the persisted JSON, run
   the extractor, assert per-section values and `h2_sep` match the
   anchors in Section 5 within ±0.001.
2. **Empty-chord test.** Bundle with `chords=[], sections=[…]` →
   `degenerate=True`, all zeros, no exception.
3. **Empty-section test.** Bundle with `sections=[]` → empty
   `per_section`, `h2_sep=0.0`, `degenerate=True`.
4. **Single-chord-section test.** Section containing exactly one chord
   → `per_section[j]=0.0`.
5. **Bigram-fallback test.** Synthetic bundle where the longest section
   has 2 chords → `n_used=2`, extractor still produces well-defined
   output.
6. **Parse-failure test.** Chord with `symbol="N.C."` → dropped from
   sequence, no exception, no warning required.
7. **Determinism test.** Run the extractor twice on the same bundle →
   identical `H2Result`.
8. **Symbol parser unit tests.** `"C"→0`, `"C#"→1`, `"Db"→1`, `"E"→4`,
   `"F#"→6`, `"Gb"→6`, `"B"→11`, `"Bb"→10`, `"N.C."→PC_NONE`,
   `""→PC_NONE`, `None→PC_NONE`.

The Step 2 extractor commit lands only when all eight pass.

---

## 10. What this spec deliberately does *not* say

- No file paths. The extractor lives wherever the project's
  conventions dictate. Suggestion (not requirement):
  `backend/tone_forge/song_form/h2.py`.
- No CLI. The extractor is a function. Wrappers and CLIs are
  downstream.
- No JSON schema. The output dataclass is the schema; serialization
  is the caller's problem.
- No chord-symbol normaliser. The regex above is sufficient for the
  six canonical bundles; if a future bundle uses an exotic symbol
  ("Cm7♭5"), the regex matches the root and ignores the rest, which is
  the desired behaviour.
- No tempo / beat alignment. Section boundaries are seconds. Done.
- No discussion of *what* H2 means musically — that is the taxonomy
  report's job. This spec is purely operational.
