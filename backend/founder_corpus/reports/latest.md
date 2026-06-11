# Founder Validation Corpus — PASS

- **Run**: `2026-06-11T15:04:07+00:00`
- **Tier filter**: `all`
- **Pipeline version**: `ff3fe3e`
- **Wall time**: 64.0s
- **Entries run**: 4
- **Fields checked**: 12  (PASS=12  WARN=0  FAIL=0)

## Per-entry results

### `synth_clean_strum` — PASS  *(tier: smoke)*

> Synthetic clean strummed chords; ~4.9s. Smoke baseline for pipeline runability.

| Field | Gate | Status | Expected | Actual | Note |
|---|---|---|---|---|---|
| `duration_s` | hard | **PASS** | 4.90s ± 0.20 | 4.90s (Δ 0.00) |  |
| `chord_count` | soft | **PASS** | [0, 50] | 0 |  |
| `guitar_midi_note_count` | soft | **PASS** | [0, 500] | 1 |  |

### `synth_clean_with_delay` — PASS  *(tier: smoke)*

> Synthetic clean tone with delay; ~6.3s. Covers delay-effect content path.

| Field | Gate | Status | Expected | Actual | Note |
|---|---|---|---|---|---|
| `duration_s` | hard | **PASS** | 6.32s ± 0.20 | 6.32s (Δ 0.00) |  |
| `chord_count` | soft | **PASS** | [0, 50] | 0 |  |
| `guitar_midi_note_count` | soft | **PASS** | [0, 500] | 1 |  |

### `synth_crunch_riff` — PASS  *(tier: smoke)*

> Synthetic crunch riff; ~6.2s. Covers edge-of-breakup detector branch.

| Field | Gate | Status | Expected | Actual | Note |
|---|---|---|---|---|---|
| `duration_s` | hard | **PASS** | 6.24s ± 0.20 | 6.24s (Δ 0.00) |  |
| `chord_count` | soft | **PASS** | [0, 50] | 0 |  |
| `guitar_midi_note_count` | soft | **PASS** | [0, 500] | 1 |  |

### `synth_high_gain_scooped` — PASS  *(tier: smoke)*

> Synthetic high-gain scooped tone; ~3.2s. Covers high-gain detector branch.

| Field | Gate | Status | Expected | Actual | Note |
|---|---|---|---|---|---|
| `duration_s` | hard | **PASS** | 3.20s ± 0.20 | 3.20s (Δ 0.00) |  |
| `chord_count` | soft | **PASS** | [0, 50] | 0 |  |
| `guitar_midi_note_count` | soft | **PASS** | [0, 500] | 1 |  |

