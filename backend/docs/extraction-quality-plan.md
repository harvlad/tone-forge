# ToneForge Extraction Quality Improvement Plan

## Status: COMPLETE

All 6 sprints implemented and integrated. 879+ tests passing.

---

## Strategic Vision

Transform ToneForge from **audio decomposition** to **semantic reconstruction** by building contamination-aware, multi-pass pipelines that understand extraction quality.

**Core Insight:** If extraction quality is weak, all downstream systems degrade. The foundation must be solid.

**Target Architecture:**
```
audio
-> source separation
-> contamination analysis
-> spectral disentanglement
-> role classification
-> multi-pass MIDI extraction
-> temporal continuity modeling
-> confidence-aware reconstruction
-> retrieval augmentation
-> export adaptation
```

**Strategic Narrowing:** Focus on **Ableton synthwave reconstruction** as the initial wedge.

---

## Implementation Summary

### Sprint 1: Foundation - COMPLETE
- [x] Create `tone_forge/reconstruction/` package
- [x] Implement StemQualityAnalyzer (`stem_quality.py`)
- [x] Implement ContaminationDetector (`contamination.py`)
- [x] Implement ArtifactDetector (`artifact_detection.py`)
- [x] Implement ConfidenceMapper (`confidence_map.py`)
- [x] Set up QualityReporter (`quality_reporter.py`)

### Sprint 2: Role & Temporal - COMPLETE
- [x] Implement RoleClassifier (`role_classifier.py`)
- [x] Implement TemporalContinuityAnalyzer (`temporal_continuity.py`)
- [x] Integrate role classification into pipeline
- [x] Add tests for sustained audio handling

### Sprint 3: Multi-Pass MIDI (Initial) - COMPLETE
- [x] Create `tone_forge/midi/passes/` structure
- [x] Implement Pass 1: HighConfidencePass (`high_confidence.py`)
- [x] Implement Pass 4: EffectSuppressionPass (`effect_suppression.py`)
- [x] Implement Pass 6: ConfidenceQuantizationPass (`confidence_quantizer.py`)
- [x] Implement base classes (`base.py`)
- [x] Integrate into extraction pipeline (`extraction_pipeline.py`)

### Sprint 4: Quality Gates & Confidence - COMPLETE
- [x] Implement RegionConfidenceMap
- [x] Implement QualityGateSystem (`quality_gates.py`)
- [x] Add quality gates to analysis pipeline
- [x] Surface quality warnings to users

### Sprint 5: Retrieval & Archetypes - COMPLETE
- [x] Create `tone_forge/archetypes/` package
- [x] Implement ProductionArchetype base (`base.py`)
- [x] Create synthwave archetype (`synthwave.py`)
- [x] Create shoegaze archetype (`shoegaze.py`)
- [x] Create ambient archetype (`ambient.py`)
- [x] Create metal archetype (`metal.py`)
- [x] Implement archetype registry (`registry.py`)
- [x] Integrate priors into extraction pipeline

### Sprint 6: Refinement & Validation - COMPLETE
- [x] Implement Pass 2: HarmonicRecoveryPass (`harmonic_recovery.py`)
- [x] Implement Pass 3: PhraseGroupingPass (`phrase_builder.py`)
- [x] Implement Pass 5: GenreRefinementPass (`genre_refinement.py`)
- [x] Implement Pass 7: MusicalityCheckPass (`musicality.py`)
- [x] Update extraction_pipeline.py for all 7 passes
- [x] All tests passing (879 tests)

### Integration: Quality-Aware Confidence - COMPLETE
- [x] Add `_adjust_confidence_for_quality()` to `analyzer.py`
- [x] Accept `stem_quality` and `contamination` parameters
- [x] Confidence adjusts based on:
  - Overall stem quality
  - Harmonic purity (affects amp/cab)
  - Transient integrity (affects gain)
  - Reverb density (affects cab/effects)
  - Contamination score (affects all)
- [x] Wire up automatic quality analysis in `tone_forge_api.py`
- [x] Include `quality_warnings` in API response
- [x] Tests for confidence adjustment (6 new tests)

---

## Files Created

### tone_forge/reconstruction/
| File | Purpose |
|------|---------|
| `__init__.py` | Package exports |
| `stem_quality.py` | StemQualityAnalyzer - per-stem quality metrics |
| `contamination.py` | ContaminationDetector - cross-stem bleed detection |
| `artifact_detection.py` | ArtifactDetector - clipping, noise, distortion |
| `confidence_map.py` | ConfidenceMapper - region-level confidence |
| `role_classifier.py` | RoleClassifier - musical role classification |
| `temporal_continuity.py` | TemporalContinuityAnalyzer - sustained content tracking |
| `quality_gates.py` | QualityGateSystem - threshold-based quality gates |
| `quality_reporter.py` | QualityReporter - unified quality reports |
| `pipeline.py` | ReconstructionPipeline - orchestrates all analysis |

### tone_forge/midi/passes/
| File | Purpose |
|------|---------|
| `__init__.py` | Package exports |
| `base.py` | ExtractionPass base class, ExtractedNote, PassResult |
| `high_confidence.py` | Pass 1: Conservative initial detection |
| `harmonic_recovery.py` | Pass 2: Fill gaps using harmonic context |
| `phrase_builder.py` | Pass 3: Group notes into musical phrases |
| `effect_suppression.py` | Pass 4: Remove delay/reverb artifacts |
| `genre_refinement.py` | Pass 5: Apply archetype priors |
| `confidence_quantizer.py` | Pass 6: Quality-aware grid snap |
| `musicality.py` | Pass 7: Validate musical coherence |

### tone_forge/midi/
| File | Purpose |
|------|---------|
| `extraction_pipeline.py` | MultiPassExtractor - orchestrates 7 passes |

### tone_forge/archetypes/
| File | Purpose |
|------|---------|
| `__init__.py` | Package exports |
| `base.py` | ProductionArchetype dataclass, ExtractionPriors |
| `synthwave.py` | Synthwave archetype (reverb-heavy, soft attacks) |
| `shoegaze.py` | Shoegaze archetype (dense, sustained) |
| `ambient.py` | Ambient archetype (sparse, evolving) |
| `metal.py` | Metal archetype (tight, aggressive) |
| `registry.py` | Archetype lookup by genre |

---

## Files Modified

| File | Changes |
|------|---------|
| `analyzer.py` | Added `stem_quality`, `contamination` params; `_adjust_confidence_for_quality()` |
| `tone_forge_api.py` | Automatic quality analysis before guitar analysis; `quality_warnings` in response |

---

## Test Coverage

| Test File | Tests | Status |
|-----------|-------|--------|
| `test_reconstruction.py` | Stem quality, contamination, artifacts | Pass |
| `test_midi_passes_sprint3.py` | Passes 1, 4, 6, pipeline | Pass |
| `test_midi_passes_sprint6.py` | Passes 2, 3, 5, 7 | Pass |
| `test_archetypes.py` | All 4 archetypes, registry | Pass |
| `test_analyzer.py` | Including 6 new quality adjustment tests | Pass |
| `test_api.py` | API integration | Pass |
| **Total** | **879+ tests** | **All passing** |

---

## How It Works Now

### API Flow (Automatic)
```
POST /api/analyze
  |
  v
Load audio
  |
  v
Run ReconstructionPipeline.analyze_only()
  -> StemQualityAnalyzer
  -> ContaminationDetector
  -> ArtifactDetector
  -> ConfidenceMapper
  -> RoleClassifier
  |
  v
Pass stem_quality + contamination to analyzer.analyze()
  |
  v
_adjust_confidence_for_quality() reduces confidence for:
  - Low overall quality
  - Poor harmonic purity
  - Low transient integrity
  - High reverb density
  - High contamination
  |
  v
Lower confidence triggers rules_engine:
  - <0.7: Show alternate amp picks
  - <0.6: Add tweak hints about uncertainty
  |
  v
Response includes:
  - Adjusted confidence scores
  - quality_warnings array
  - Alternate recommendations
```

### MIDI Extraction Flow
```
MultiPassExtractor.extract()
  |
  v
Pass 1: HighConfidencePass (conservative detection)
  |
  v
Pass 2: HarmonicRecoveryPass (fill gaps)
  |
  v
Pass 3: PhraseGroupingPass (musical phrases)
  |
  v
Pass 4: EffectSuppressionPass (remove echoes)
  |
  v
Pass 5: GenreRefinementPass (archetype priors)
  |
  v
Pass 6: ConfidenceQuantizationPass (grid snap)
  |
  v
Pass 7: MusicalityCheckPass (validate coherence)
  |
  v
MIDIExtractionResult with per-note confidence
```

---

## Success Criteria Status

| Criterion | Target | Status |
|-----------|--------|--------|
| Stem Quality Detection | >70% accuracy | Implemented |
| MIDI Quality | Note F1 +15% | 7-pass pipeline |
| Confidence Calibration | Matches accuracy | Quality-aware adjustment |
| User Satisfaction | Improved | Warnings surfaced |
| Synthwave Pads | Clean extraction | Archetype + effect suppression |

---

## Verification Commands

```bash
# Run all tests
pytest tests/ -v

# Run reconstruction tests
pytest tests/test_reconstruction.py -v

# Run MIDI pass tests
pytest tests/test_midi_passes_sprint3.py tests/test_midi_passes_sprint6.py -v

# Run archetype tests
pytest tests/test_archetypes.py -v

# Run analyzer tests (includes quality adjustment)
pytest tests/test_analyzer.py -v

# Run API tests
pytest tests/test_api.py -v

# Start server and test
python3 -m uvicorn tone_forge_api:app --reload --port 8000
```
