# Phase 2 Feature-Mask Validation Experiment

**Hypothesis under test**: `attack_ms`, `decay_ms`, and `pitch_stability`
are unreliable on polyphonic content and are dominating z-norm distance,
burying the discriminating features (brightness, warmth, air, sustain_ratio,
harmonic_ratio).

**Method**: re-rank known queries with these 3 features zeroed out in the
z-norm vector. Production code unchanged.

**Catalog**: 5 chains from `tone_forge/monitor/chains/`
```
  tfc.ambient               [  0.0657   0.4904   0.      16.8254 456.3265   0.1318   0.0005   0.    ]
  tfc.classic_rock          [   0.1122    0.734     0.       16.9161 1185.9864    0.1426    0.0004
    0.    ]
  tfc.clean_strat           [  0.2097   0.4594   0.0001  17.1429 483.2653   0.0921   0.0004   0.    ]
  tfc.edge_of_breakup       [  0.1551   0.4064   0.      17.1429 485.4875   0.0939   0.0004   0.    ]
  tfc.modern_gain           [  0.2162   0.5678   0.0007  33.424  527.2109   0.133    0.0004   0.    ]
  mean                            [  0.1518   0.5316   0.0002  20.2902 627.6553   0.1187   0.0004   0.    ]
  std                             [  0.0574   0.1139   0.001    6.5681 280.0844   0.0213   0.001    0.001 ]
```

**Masked features**: attack_ms, decay_ms, pitch_stability

---

## Test queries

### Group A — Identity sanity checks (catalog → itself)

Each WAV used to build a fingerprint is re-extracted and ranked. 
These should ALL rank themselves #1 in both modes. If they don't, 
the extractor is non-deterministic or the catalog is corrupt.

### A. Identity: tfc.ambient
_Expected top-1_: **tfc.ambient**

```
query_vector = [  0.0657   0.4904   0.      16.8254 456.3265   0.1318   0.0005   0.0322]
```

**BEFORE (full 8 features)** (mode=production)
```
  1. tfc.ambient                d= 32.2334  conf=0.100
  2. tfc.edge_of_breakup        d= 32.3287  conf=0.099
  3. tfc.clean_strat            d= 32.3861  conf=0.099
  4. tfc.classic_rock           d= 32.4233  conf=0.099
  5. tfc.modern_gain            d= 32.4537  conf=0.098
  → confidence=0.1000  margin=0.0030
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability     1038.9947    100.0%
```

**AFTER  (masked 3 features)** (mode=experiment)
```
  1. tfc.ambient                d=  0.0000  conf=1.000
  2. tfc.classic_rock           d=  2.3424  conf=0.846
  3. tfc.edge_of_breakup        d=  2.4766  conf=0.838
  4. tfc.modern_gain            d=  2.7918  conf=0.819
  5. tfc.clean_strat            d=  3.1394  conf=0.799
  → confidence=1.0000  margin=n/a
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000      0.0%
```

_Δ summary_:
```
  top-1: tfc.ambient  →  tfc.ambient  
  conf:  0.1000  →  1.0000
  margin: 0.0029537618520556466  →  None
  expected (tfc.ambient): rank 1 → rank 1
```

### A. Identity: tfc.classic_rock
_Expected top-1_: **tfc.classic_rock**

```
query_vector = [   0.1122    0.734     0.       16.9161 1185.9864    0.1426    0.0004
    0.    ]
```

**BEFORE (full 8 features)** (mode=production)
```
  1. tfc.classic_rock           d=  0.0000  conf=1.000
  2. tfc.ambient                d=  3.5034  conf=0.779
  3. tfc.modern_gain            d=  4.2324  conf=0.739
  4. tfc.edge_of_breakup        d=  4.5064  conf=0.725
  5. tfc.clean_strat            d=  4.5412  conf=0.723
  → confidence=1.0000  margin=52334346.9442
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000    100.0%
```

**AFTER  (masked 3 features)** (mode=experiment)
```
  1. tfc.classic_rock           d=  0.0000  conf=1.000
  2. tfc.ambient                d=  2.3424  conf=0.846
  3. tfc.modern_gain            d=  2.4625  conf=0.839
  4. tfc.edge_of_breakup        d=  3.7485  conf=0.765
  5. tfc.clean_strat            d=  3.7850  conf=0.763
  → confidence=1.0000  margin=n/a
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000      0.0%
```

_Δ summary_:
```
  top-1: tfc.classic_rock  →  tfc.classic_rock  
  conf:  1.0000  →  1.0000
  margin: 52334346.94421284  →  None
  expected (tfc.classic_rock): rank 1 → rank 1
```

### A. Identity: tfc.clean_strat
_Expected top-1_: **tfc.clean_strat**

```
query_vector = [  0.2097   0.4594   0.0001  17.1429 483.2653   0.0921   0.0004   0.    ]
```

**BEFORE (full 8 features)** (mode=production)
```
  1. tfc.clean_strat            d=  0.0000  conf=1.000
  2. tfc.edge_of_breakup        d=  1.0719  conf=0.926
  3. tfc.ambient                d=  3.1413  conf=0.799
  4. tfc.modern_gain            d=  3.3256  conf=0.789
  5. tfc.classic_rock           d=  4.5412  conf=0.723
  → confidence=1.0000  margin=14329462.5908
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000    100.0%
```

**AFTER  (masked 3 features)** (mode=experiment)
```
  1. tfc.clean_strat            d=  0.0000  conf=1.000
  2. tfc.edge_of_breakup        d=  1.0719  conf=0.926
  3. tfc.modern_gain            d=  2.2114  conf=0.854
  4. tfc.ambient                d=  3.1394  conf=0.799
  5. tfc.classic_rock           d=  3.7850  conf=0.763
  → confidence=1.0000  margin=n/a
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000      0.0%
```

_Δ summary_:
```
  top-1: tfc.clean_strat  →  tfc.clean_strat  
  conf:  1.0000  →  1.0000
  margin: 14329462.590774717  →  None
  expected (tfc.clean_strat): rank 1 → rank 1
```

### A. Identity: tfc.edge_of_breakup
_Expected top-1_: **tfc.edge_of_breakup**

```
query_vector = [  0.1551   0.4064   0.      17.1429 485.4875   0.0939   0.0004   0.    ]
```

**BEFORE (full 8 features)** (mode=production)
```
  1. tfc.edge_of_breakup        d=  0.0000  conf=1.000
  2. tfc.clean_strat            d=  1.0719  conf=0.926
  3. tfc.ambient                d=  2.4793  conf=0.838
  4. tfc.modern_gain            d=  3.6240  conf=0.772
  5. tfc.classic_rock           d=  4.5064  conf=0.725
  → confidence=1.0000  margin=117653953.2548
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000    100.0%
```

**AFTER  (masked 3 features)** (mode=experiment)
```
  1. tfc.edge_of_breakup        d=  0.0000  conf=1.000
  2. tfc.clean_strat            d=  1.0719  conf=0.926
  3. tfc.ambient                d=  2.4766  conf=0.838
  4. tfc.modern_gain            d=  2.6394  conf=0.828
  5. tfc.classic_rock           d=  3.7485  conf=0.765
  → confidence=1.0000  margin=n/a
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000      0.0%
```

_Δ summary_:
```
  top-1: tfc.edge_of_breakup  →  tfc.edge_of_breakup  
  conf:  1.0000  →  1.0000
  margin: 117653953.2547624  →  None
  expected (tfc.edge_of_breakup): rank 1 → rank 1
```

### A. Identity: tfc.modern_gain
_Expected top-1_: **tfc.modern_gain**

```
query_vector = [  0.2162   0.5678   0.0007  33.424  527.2109   0.133    0.0004   0.    ]
```

**BEFORE (full 8 features)** (mode=production)
```
  1. tfc.modern_gain            d=  0.0000  conf=1.000
  2. tfc.clean_strat            d=  3.3256  conf=0.789
  3. tfc.edge_of_breakup        d=  3.6240  conf=0.772
  4. tfc.ambient                d=  3.7742  conf=0.764
  5. tfc.classic_rock           d=  4.2324  conf=0.739
  → confidence=1.0000  margin=22158607.8145
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000    100.0%
```

**AFTER  (masked 3 features)** (mode=experiment)
```
  1. tfc.modern_gain            d=  0.0000  conf=1.000
  2. tfc.clean_strat            d=  2.2114  conf=0.854
  3. tfc.classic_rock           d=  2.4625  conf=0.839
  4. tfc.edge_of_breakup        d=  2.6394  conf=0.828
  5. tfc.ambient                d=  2.7918  conf=0.819
  → confidence=1.0000  margin=n/a
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.0000      0.0%
       warmth                 0.0000      0.0%
       air                    0.0000      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.0000      0.0%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000      0.0%
```

_Δ summary_:
```
  top-1: tfc.modern_gain  →  tfc.modern_gain  
  conf:  1.0000  →  1.0000
  margin: 22158607.814524364  →  None
  expected (tfc.modern_gain): rank 1 → rank 1
```

### Group B — Real songs (polyphonic queries)

### B. ALCEST — Flamme Jumelle
_Expected top-1_: **tfc.ambient**

```
query_vector = [   0.1913    0.2906    0.     5661.678  2332.7438    0.5691    0.0004
    0.    ]
```

**BEFORE (full 8 features)** (mode=production)
```
  1. tfc.modern_gain            d=857.1817  conf=0.000
  2. tfc.classic_rock           d=859.6754  conf=0.000
  3. tfc.edge_of_breakup        d=859.7036  conf=0.000
  4. tfc.clean_strat            d=859.7063  conf=0.000
  5. tfc.ambient                d=859.7121  conf=0.000
  → confidence=0.0000  margin=0.0029
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.1882      0.0%
       warmth                 5.9263      0.0%
       air                    0.3944      0.0%
      *attack_ms          734294.1100     99.9%
      *decay_ms              41.5559      0.0%
       sustain_ratio        418.2611      0.1%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000      0.0%
```

**AFTER  (masked 3 features)** (mode=experiment)
```
  1. tfc.classic_rock           d= 20.4251  conf=0.232
  2. tfc.modern_gain            d= 20.6100  conf=0.229
  3. tfc.ambient                d= 20.6999  conf=0.228
  4. tfc.edge_of_breakup        d= 22.3200  conf=0.203
  5. tfc.clean_strat            d= 22.4240  conf=0.202
  → confidence=0.2325  margin=0.0091
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             1.8959      0.5%
       warmth                15.1614      3.6%
       air                    0.0019      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio        400.1220     95.9%
       harmonic_ratio         0.0032      0.0%
      *pitch_stability        0.0000      0.0%
```

_Δ summary_:
```
  top-1: tfc.modern_gain  →  tfc.classic_rock  (CHANGED)
  conf:  0.0000  →  0.2325
  margin: 0.0029092198562879713  →  0.00905052548645011
  expected (tfc.ambient): rank 5 → rank 3
```

### B. ALCEST — Kodama
_Expected top-1_: **tfc.ambient**

```
query_vector = [   0.1589    0.4544    0.     3120.9524  514.4218    0.4721    0.0005
    0.    ]
```

**BEFORE (full 8 features)** (mode=production)
```
  1. tfc.modern_gain            d=470.3520  conf=0.000
  2. tfc.classic_rock           d=472.8597  conf=0.000
  3. tfc.ambient                d=472.8800  conf=0.000
  4. tfc.edge_of_breakup        d=472.8924  conf=0.000
  5. tfc.clean_strat            d=472.8962  conf=0.000
  → confidence=0.0000  margin=0.0053
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.9975      0.0%
       warmth                 0.9919      0.0%
       air                    0.4109      0.0%
      *attack_ms          220975.7307     99.9%
      *decay_ms               0.0021      0.0%
       sustain_ratio        252.8598      0.1%
       harmonic_ratio         0.0156      0.0%
      *pitch_stability        0.0000      0.0%
```

**AFTER  (masked 3 features)** (mode=experiment)
```
  1. tfc.classic_rock           d= 15.6682  conf=0.327
  2. tfc.modern_gain            d= 15.9773  conf=0.319
  3. tfc.ambient                d= 16.0443  conf=0.318
  4. tfc.edge_of_breakup        d= 17.7432  conf=0.282
  5. tfc.clean_strat            d= 17.8449  conf=0.280
  → confidence=0.3266  margin=0.0197
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.6593      0.3%
       warmth                 6.0284      2.5%
       air                    0.0009      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio        238.8009     97.3%
       harmonic_ratio         0.0039      0.0%
      *pitch_stability        0.0000      0.0%
```

_Δ summary_:
```
  top-1: tfc.modern_gain  →  tfc.classic_rock  (CHANGED)
  conf:  0.0000  →  0.3266
  margin: 0.005331426081735865  →  0.019729219539230188
  expected (tfc.ambient): rank 3 → rank 3
```

### B. ALCEST — Flamme Jumelle (early run)
_Expected top-1_: **tfc.ambient**

```
query_vector = [   0.1925    0.2787    0.     5661.678  2197.7778    0.5603    0.0004
    0.    ]
```

**BEFORE (full 8 features)** (mode=production)
```
  1. tfc.modern_gain            d=857.1688  conf=0.000
  2. tfc.classic_rock           d=859.6643  conf=0.000
  3. tfc.edge_of_breakup        d=859.6896  conf=0.000
  4. tfc.clean_strat            d=859.6923  conf=0.000
  5. tfc.ambient                d=859.6991  conf=0.000
  → confidence=0.0000  margin=0.0029
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.1709      0.0%
       warmth                 6.4437      0.0%
       air                    0.3906      0.0%
      *attack_ms          734294.1100     99.9%
      *decay_ms              35.5754      0.0%
       sustain_ratio        401.5951      0.1%
       harmonic_ratio         0.0000      0.0%
      *pitch_stability        0.0000      0.0%
```

**AFTER  (masked 3 features)** (mode=experiment)
```
  1. tfc.classic_rock           d= 20.0441  conf=0.239
  2. tfc.modern_gain            d= 20.2139  conf=0.236
  3. tfc.ambient                d= 20.3037  conf=0.235
  4. tfc.edge_of_breakup        d= 21.9147  conf=0.209
  5. tfc.clean_strat            d= 22.0203  conf=0.207
  → confidence=0.2389  margin=0.0085
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             1.9526      0.5%
       warmth                15.9825      4.0%
       air                    0.0021      0.0%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio        383.8251     95.5%
       harmonic_ratio         0.0036      0.0%
      *pitch_stability        0.0000      0.0%
```

_Δ summary_:
```
  top-1: tfc.modern_gain  →  tfc.classic_rock  (CHANGED)
  conf:  0.0000  →  0.2389
  margin: 0.0029113552793683687  →  0.008469623488637702
  expected (tfc.ambient): rank 5 → rank 3
```

### Group C — Clean DI control (Dry_Guitar_sustain.wav)

This is the source DI used to render the catalog. It contains no amp/cab 
processing. Closest chain should be `tfc.clean_strat` (the least-processed 
entry in the catalog).

### C. Dry DI (Dry_Guitar_sustain.wav)
_Expected top-1_: **tfc.clean_strat**

```
query_vector = [  0.1733   0.6094   0.      16.8254 365.8957   0.0812   0.0005   0.    ]
```

**BEFORE (full 8 features)** (mode=production)
```
  1. tfc.clean_strat            d=  1.6116  conf=0.891
  2. tfc.edge_of_breakup        d=  1.9541  conf=0.870
  3. tfc.ambient                d=  3.2153  conf=0.795
  4. tfc.modern_gain            d=  3.7117  conf=0.767
  5. tfc.classic_rock           d=  4.3805  conf=0.731
  → confidence=0.8913  margin=0.2126
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.4027     15.5%
       warmth                 1.7356     66.8%
       air                    0.0202      0.8%
      *attack_ms              0.0023      0.1%
      *decay_ms               0.1756      6.8%
       sustain_ratio          0.2602     10.0%
       harmonic_ratio         0.0005      0.0%
      *pitch_stability        0.0000      0.0%
```

**AFTER  (masked 3 features)** (mode=experiment)
```
  1. tfc.clean_strat            d=  1.5554  conf=0.895
  2. tfc.edge_of_breakup        d=  1.9063  conf=0.873
  3. tfc.modern_gain            d=  2.6568  conf=0.827
  4. tfc.ambient                d=  3.1991  conf=0.796
  5. tfc.classic_rock           d=  3.2581  conf=0.792
  → confidence=0.8949  margin=0.2256
```

_Per-feature contribution to top-1 d² (* = masked):_
```
      feature                  sq-z   % of d²
       brightness             0.4027     16.6%
       warmth                 1.7356     71.7%
       air                    0.0202      0.8%
      *attack_ms              0.0000      0.0%
      *decay_ms               0.0000      0.0%
       sustain_ratio          0.2602     10.8%
       harmonic_ratio         0.0005      0.0%
      *pitch_stability        0.0000      0.0%
```

_Δ summary_:
```
  top-1: tfc.clean_strat  →  tfc.clean_strat  
  conf:  0.8913  →  0.8949
  margin: 0.21256100150810744  →  0.2256109510660997
  expected (tfc.clean_strat): rank 1 → rank 1
```

---

## Summary table

| case | expected | full top-1 | masked top-1 | full conf | masked conf | change |
|---|---|---|---|---|---|---|
| identity:tfc.ambient | tfc.ambient | tfc.ambient | tfc.ambient | 0.100 | 1.000 | conf rose |
| identity:tfc.classic_rock | tfc.classic_rock | tfc.classic_rock | tfc.classic_rock | 1.000 | 1.000 | — |
| identity:tfc.clean_strat | tfc.clean_strat | tfc.clean_strat | tfc.clean_strat | 1.000 | 1.000 | — |
| identity:tfc.edge_of_breakup | tfc.edge_of_breakup | tfc.edge_of_breakup | tfc.edge_of_breakup | 1.000 | 1.000 | — |
| identity:tfc.modern_gain | tfc.modern_gain | tfc.modern_gain | tfc.modern_gain | 1.000 | 1.000 | — |
| ALCEST — Flamme Jumelle | tfc.ambient | tfc.modern_gain | tfc.classic_rock | 0.000 | 0.232 | changed, conf rose |
| ALCEST — Kodama | tfc.ambient | tfc.modern_gain | tfc.classic_rock | 0.000 | 0.327 | changed, conf rose |
| ALCEST — Flamme Jumelle (early run) | tfc.ambient | tfc.modern_gain | tfc.classic_rock | 0.000 | 0.239 | changed, conf rose |
| dry_di | tfc.clean_strat | tfc.clean_strat | tfc.clean_strat | 0.891 | 0.895 | — |

## Verdict

**Pass criteria**:
- [x] Identity checks pass in both modes
- [ ] Ambient becomes top-1 on at least one shoegaze case
- [x] Real-song confidence rises out of the 0.00 floor
- [x] Clean DI still matches clean_strat after masking

**Result**: 3/4 pass criteria met.

→ **HYPOTHESIS PARTIALLY SUPPORTED**: masking helps but is not sufficient. 
There may be additional issues (catalog content mismatch, calibration, etc.).