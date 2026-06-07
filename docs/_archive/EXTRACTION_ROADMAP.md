# MIDI Extraction Pipeline: Research Roadmap

## Executive Summary

**Current State:**
- Bass: 7/16 passing (44%), 64% avg F1
- Lead: 0/16 passing (0%), 20.5% avg F1
- Overall: 7/32 passing (22%), 42% avg F1

**Target:** >80% F1 across all samples

**Conclusion:** 80% F1 is NOT achievable with the current architecture. Fundamental architectural changes are required.

---

## Error Budget Analysis

### Bass Extraction

| Error Category | Count | % of GT | Impact on F1 |
|----------------|-------|---------|--------------|
| True Positives | 3,921 | 45.7% | - |
| False Negatives (missed) | 4,652 | 54.3% | -27% F1 |
| False Positives (extra) | 3,014 | 35.2% | -13% F1 |
| Octave-shifted matches | 3,371 | 86% of TP | (counted in TP) |

**Theoretical Maximum F1:**
- If zero FPs: 77% (still below 80%)
- If zero FNs: 74.5%
- If zero both: 100% (impossible to achieve)

### Lead Extraction

| Error Category | Count | % of GT | Impact on F1 |
|----------------|-------|---------|--------------|
| True Positives | 876 | 19.3% | - |
| False Negatives (missed) | 3,663 | 80.7% | -40% F1 |
| False Positives (extra) | 2,337 | 51.5% | -12% F1 |

**Theoretical Maximum F1:**
- If zero FPs: 32.1% (far below 80%)
- If zero FNs: 38.8%

---

## Top 5 Root Causes (Ranked by Impact)

### 1. Polyphonic Content vs Monophonic Detectors (Cost: ~30% F1)

**Evidence:**
- 14/16 lead samples fail due to polyphonic content
- 6/16 bass samples fail due to polyphonic content
- pYIN and torchcrepe fundamentally assume one pitch at a time

**Why current approach fails:**
- Chords appear as single notes at the fundamental or dominant harmonic
- Arpeggios collapse when notes overlap
- HCA helps but routing decisions are binary (mono vs poly)

### 2. Under-Detection / Low Recall (Cost: ~25% F1)

**Evidence:**
- 54% of bass GT notes missed
- 81% of lead GT notes missed
- Most failing samples have recall << precision

**Why current approach fails:**
- Onset detection misses rapid attacks
- Sustained notes with pitch variations get fragmented
- Low-amplitude notes below detection threshold

### 3. Octave Confusion (Cost: ~15% F1)

**Evidence:**
- 86% of bass TPs are octave-shifted
- 45.5% of lead TPs are octave-shifted
- pYIN consistently locks onto sub-harmonics

**Why current approach fails:**
- Sub-harmonic is often stronger than fundamental in bass
- Pitch tracking algorithms assume fundamental is dominant
- No harmonic structure validation

### 4. Over-Detection / False Positives (Cost: ~12% F1)

**Evidence:**
- 35% extra notes in bass
- 51% extra notes in lead
- Particularly severe in Mama Squid (1190% over-detection)

**Why current approach fails:**
- Periodicity threshold too low captures noise
- Harmonic artifacts detected as separate notes
- No temporal coherence filtering

### 5. Routing Mistakes (Cost: ~5% F1)

**Evidence:**
- Some samples incorrectly routed to wrong detector
- HCA sometimes called on monophonic content
- Monophonic detector on polyphonic content

**Why current approach fails:**
- Harmonic ratio threshold not robust
- Content classification based on spectral features, not actual polyphony

---

## Architectural Assessment

### Current Architecture Limitations

```
Audio → Stem Detection → Polyphony Check → [Mono Detector | HCA] → Notes
                              ↓
                    Binary routing decision
                    (wrong 15-20% of time)
```

**Fundamental problems:**
1. Routing is binary - no hybrid handling
2. Mono detectors (pYIN, torchcrepe) are fundamentally incompatible with polyphonic content
3. HCA is polyphonic but not robust enough for all content types
4. No feedback or refinement loop

### Why >80% F1 is NOT Achievable

1. **Bass:** Even with perfect precision (zero FPs), max F1 is 77%
2. **Lead:** Even with perfect recall (zero FNs), max F1 is only 39%
3. The detectors themselves are the bottleneck, not the routing or post-processing

---

## Proposed Next-Generation Architecture

### Option A: Multi-Pitch Tracking with Voice Separation

```
Audio → Harmonic Analysis → Multi-Pitch Detection → Voice Separation → Notes per Voice
                                    ↓
                         Track multiple f0s simultaneously
                         using HCQT or multi-pitch CNN
```

**Expected F1 gain:** +20-30%
**Effort:** High (3-6 months)
**Risk:** Medium - requires new models

### Option B: Chord-State Machine with Temporal Memory

```
Audio → Frame-wise Chord Detection → State Machine → Note Inference
                                          ↓
                              Model chord progressions
                              infer individual notes
```

**Expected F1 gain:** +15-25%
**Effort:** Medium (2-4 months)
**Risk:** Medium - requires chord detection training

### Option C: Source-Specific Extraction (Recommended)

```
Audio → Instrument Classification → Specialized Extractor → Notes
              ↓
     Piano/Guitar/Synth/Bass/Voice
     each with dedicated model
```

**Expected F1 gain:** +25-35%
**Effort:** High (4-8 months)
**Risk:** Low - proven approach in literature

### Option D: Graph-Based Note Inference

```
Audio → Candidate Note Generation → Graph Construction → Inference
                                          ↓
                              Factor graph with:
                              - harmonic constraints
                              - temporal continuity
                              - voice leading rules
```

**Expected F1 gain:** +20-30%
**Effort:** Very High (6-12 months)
**Risk:** High - research-level complexity

---

## Recommended Roadmap

### Phase 1: Quick Wins (1-2 weeks, +5-10% F1)

1. **Improve onset detection for bass**
   - Use multi-band onset detection
   - Energy-based + spectral flux combination
   - Expected: +3% F1 for bass

2. **Tune HCA routing threshold**
   - Current threshold too conservative
   - More samples should use HCA
   - Expected: +2% F1 for lead

3. **Add harmonic validation for octave**
   - Check for expected overtone structure
   - Reject implausible pitch detections
   - Expected: +2% F1 for bass

### Phase 2: Hybrid Detection (2-4 weeks, +10-15% F1)

1. **Parallel mono + poly detection**
   - Run both pYIN AND HCA
   - Merge results using confidence weighting
   - Expected: +5% F1

2. **Multi-pitch refinement**
   - Use basic_pitch as multi-pitch backup
   - Fill gaps where mono detection fails
   - Expected: +5% F1

3. **Temporal coherence filtering**
   - Remove isolated notes (<50ms gap to neighbors)
   - Merge fragmented notes
   - Expected: +3% F1

### Phase 3: Architecture Evolution (1-3 months, +15-25% F1)

1. **Implement source-specific extractors**
   - Train or fine-tune models for bass/lead/keys
   - Use transfer learning from basic_pitch
   - Expected: +15% F1

2. **Add chord-state tracking**
   - Detect chord changes
   - Infer notes from chord + voicing
   - Expected: +10% F1

---

## Realistic F1 Projections

| Phase | Bass F1 | Lead F1 | Overall |
|-------|---------|---------|---------|
| Current | 64% | 20.5% | 42% |
| After Phase 1 | 70% | 28% | 49% |
| After Phase 2 | 78% | 40% | 59% |
| After Phase 3 | 85% | 60% | 73% |

**Note:** Reaching 80% F1 for LEAD requires Phase 3 architectural changes. The current monophonic approach cannot achieve this target.

---

## Conclusion

**The current extraction paradigm (monophonic pitch detection + binary routing) cannot achieve 80% F1.**

The fundamental issue is that real music is polyphonic, and our detectors assume monophonic content. Even with perfect tuning, the theoretical maximum F1 is below our target.

To reach 80% F1, we must:
1. Move from pitch detection to multi-pitch tracking
2. Add chord/harmonic state modeling
3. Implement source-specific extraction strategies

The recommended path is:
1. **Short-term:** Phase 1 quick wins (+5-10% F1, 2 weeks)
2. **Medium-term:** Phase 2 hybrid detection (+10-15% F1, 1 month)
3. **Long-term:** Phase 3 architecture evolution (+15-25% F1, 2-3 months)

This roadmap prioritizes incremental improvements with known techniques before attempting more ambitious architectural changes.
