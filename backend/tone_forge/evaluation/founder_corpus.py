"""Founder Validation Corpus — pure-logic comparators + report formatting.

This module is the load-bearing trust artifact behind every other Jam
feature. The premise is simple: a small, fixed set of songs the founder
has personally validated as "the analyzer got this right", re-run end-to-
end through the pipeline on demand, with per-field deltas reported.
Anything that drifts on this corpus is a regression in something
guitar-facing — by construction.

Scope of this module:
  - Load + validate the manifest (the registry of corpus entries).
  - Load + validate per-entry expected-output JSON.
  - Compare an expected payload against a pipeline result and emit a
    structured per-field result list.
  - Format that result list as a Markdown report.
  - Roll up the per-field results into a single exit code.

Scope of *other* code (kept out of this module to keep the comparator
pure and trivially testable):
  - Actually running the pipeline (see scripts/run_founder_validation.py).
  - Discovering / persisting baselines (also the runner).
  - I/O around the report file (the runner).
  - Pytest wiring (tests/test_founder_corpus_integrity.py).

The shape of an expected file is intentionally permissive: only the
fields *present* in the JSON are checked. This lets the corpus grow
field-by-field as the founder validates additional aspects of each
song without breaking older entries.

Expected JSON field shape (all top-level keys optional):

    {
      "schema_version": 1,
      "song_id": "founder_001",
      "source_notes": "human-readable provenance",

      "duration_s":              {"value": 12.3, "tolerance_s": 0.5, "gate": "hard"},
      "tempo_bpm":               {"value": 120.0, "tolerance_bpm": 2.0, "gate": "hard"},
      "key":                     {"value": "C major", "allow_relative_minor": true, "gate": "soft"},
      "detected_type":           {"value": "guitar", "gate": "hard"},
      "section_count":           {"value": 4, "tolerance": 1, "gate": "soft"},
      "chord_count":             {"min": 4, "max": 20, "gate": "soft"},
      "guitar_midi_note_count":  {"min": 8, "max": 200, "gate": "soft"}
    }

Default gate is "soft" — only "hard" failures roll up into a non-zero
exit code. This keeps the harness usable for fields where the founder
has confidence in a *range* but not an exact ground-truth number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import json


# ---------------------------------------------------------------------------
# Manifest + expected loaders
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
VALID_TIERS = frozenset({"smoke", "full"})
VALID_GATES = frozenset({"hard", "soft"})


@dataclass(frozen=True)
class CorpusEntry:
    """One row in the manifest."""
    id: str
    audio_path: Path           # absolute, resolved against corpus_root
    expected_path: Path        # absolute, resolved against corpus_root
    tier: str                  # "smoke" | "full"
    notes: str = ""


@dataclass(frozen=True)
class CorpusManifest:
    """Parsed manifest.yaml."""
    schema_version: int
    corpus_root: Path
    entries: Tuple[CorpusEntry, ...]

    def filter_by_tier(self, tier: Optional[str]) -> Tuple[CorpusEntry, ...]:
        """Return entries matching the tier filter. None / 'all' returns all."""
        if tier is None or tier == "all":
            return self.entries
        if tier not in VALID_TIERS:
            raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(VALID_TIERS)} or 'all'")
        return tuple(e for e in self.entries if e.tier == tier)


def load_manifest(manifest_path: Path) -> CorpusManifest:
    """Parse and validate manifest.yaml.

    Raises ValueError on any schema violation. We deliberately fail loud
    here — a malformed manifest is a developer bug, not a runtime
    condition to be coddled.
    """
    import yaml  # local import: only the harness needs PyYAML

    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    corpus_root = manifest_path.parent
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{manifest_path}: top level must be a mapping")

    sv = raw.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise ValueError(
            f"{manifest_path}: schema_version must be {SCHEMA_VERSION}, got {sv!r}"
        )

    raw_entries = raw.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError(f"{manifest_path}: 'entries' must be a non-empty list")

    parsed: List[CorpusEntry] = []
    seen_ids = set()
    for idx, row in enumerate(raw_entries):
        if not isinstance(row, dict):
            raise ValueError(f"{manifest_path}: entry[{idx}] must be a mapping")
        entry_id = row.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError(f"{manifest_path}: entry[{idx}].id must be a non-empty string")
        if entry_id in seen_ids:
            raise ValueError(f"{manifest_path}: duplicate entry id {entry_id!r}")
        seen_ids.add(entry_id)

        audio = row.get("audio")
        expected = row.get("expected")
        if not isinstance(audio, str) or not isinstance(expected, str):
            raise ValueError(
                f"{manifest_path}: entry {entry_id!r} requires 'audio' and 'expected' string paths"
            )

        tier = row.get("tier", "full")
        if tier not in VALID_TIERS:
            raise ValueError(
                f"{manifest_path}: entry {entry_id!r} tier {tier!r} not in {sorted(VALID_TIERS)}"
            )

        notes = row.get("notes", "")
        if not isinstance(notes, str):
            raise ValueError(f"{manifest_path}: entry {entry_id!r} notes must be a string")

        parsed.append(CorpusEntry(
            id=entry_id,
            audio_path=(corpus_root / audio).resolve(),
            expected_path=(corpus_root / expected).resolve(),
            tier=tier,
            notes=notes,
        ))

    return CorpusManifest(
        schema_version=sv,
        corpus_root=corpus_root,
        entries=tuple(parsed),
    )


def load_expected(expected_path: Path) -> Dict[str, Any]:
    """Parse and shallow-validate one expected JSON.

    Returns the raw dict (the per-field comparators do their own structural
    validation). We only verify schema_version and song_id here so a
    missing-field bug surfaces close to its source.
    """
    expected_path = Path(expected_path)
    if not expected_path.exists():
        raise FileNotFoundError(f"expected file not found: {expected_path}")
    payload = json.loads(expected_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{expected_path}: top level must be an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{expected_path}: schema_version must be {SCHEMA_VERSION}, got {payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("song_id"), str):
        raise ValueError(f"{expected_path}: song_id must be a string")
    return payload


# ---------------------------------------------------------------------------
# Per-field result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldResult:
    """The outcome of one field comparison."""
    field: str               # e.g. "tempo_bpm"
    gate: str                # "hard" | "soft"
    passed: bool
    expected_repr: str       # human-readable, e.g. "120.0 ± 2.0 bpm"
    actual_repr: str         # human-readable, e.g. "118.6 bpm"
    message: str = ""        # optional explanation when failed (or empty)

    @property
    def severity(self) -> str:
        if self.passed:
            return "PASS"
        return "FAIL" if self.gate == "hard" else "WARN"


# ---------------------------------------------------------------------------
# Field comparators
# ---------------------------------------------------------------------------
# Each comparator takes:
#   - the expected spec dict (the value of the field's key in the JSON)
#   - the actual pipeline result (an AnalysisResult.to_dict() output)
# and returns a FieldResult. If the actual is not extractable, the
# comparator returns a failed FieldResult with an informative message.

def _gate_of(spec: Mapping[str, Any]) -> str:
    gate = spec.get("gate", "soft")
    if gate not in VALID_GATES:
        raise ValueError(f"gate must be one of {sorted(VALID_GATES)}, got {gate!r}")
    return gate


def _cmp_duration_s(spec: Mapping[str, Any], actual: Mapping[str, Any]) -> FieldResult:
    gate = _gate_of(spec)
    target = float(spec["value"])
    tol = float(spec.get("tolerance_s", 0.5))
    actual_val = actual.get("duration_sec")
    if not isinstance(actual_val, (int, float)):
        return FieldResult("duration_s", gate, False,
                           f"{target:.2f}s ± {tol:.2f}",
                           "missing",
                           message="pipeline did not report duration_sec")
    delta = abs(float(actual_val) - target)
    passed = delta <= tol
    return FieldResult("duration_s", gate, passed,
                       f"{target:.2f}s ± {tol:.2f}",
                       f"{float(actual_val):.2f}s (Δ {delta:.2f})")


def _cmp_tempo_bpm(spec: Mapping[str, Any], actual: Mapping[str, Any]) -> FieldResult:
    gate = _gate_of(spec)
    target = float(spec["value"])
    tol = float(spec.get("tolerance_bpm", 2.0))
    midi = actual.get("midi") or {}
    actual_val = midi.get("tempo")
    if actual_val is None:
        # Fall back to midi_stats if present.
        midi_stats = actual.get("midi_stats") or {}
        actual_val = midi_stats.get("tempo")
    if not isinstance(actual_val, (int, float)):
        return FieldResult("tempo_bpm", gate, False,
                           f"{target:.1f} ± {tol:.1f} bpm",
                           "missing",
                           message="pipeline did not report midi.tempo")
    delta = abs(float(actual_val) - target)
    passed = delta <= tol
    return FieldResult("tempo_bpm", gate, passed,
                       f"{target:.1f} ± {tol:.1f} bpm",
                       f"{float(actual_val):.1f} bpm (Δ {delta:.1f})")


_KEY_RELATIVE = {
    "C major": "A minor", "A minor": "C major",
    "G major": "E minor", "E minor": "G major",
    "D major": "B minor", "B minor": "D major",
    "A major": "F# minor", "F# minor": "A major",
    "E major": "C# minor", "C# minor": "E major",
    "B major": "G# minor", "G# minor": "B major",
    "F# major": "D# minor", "D# minor": "F# major",
    "C# major": "A# minor", "A# minor": "C# major",
    "F major": "D minor", "D minor": "F major",
    "Bb major": "G minor", "G minor": "Bb major",
    "Eb major": "C minor", "C minor": "Eb major",
    "Ab major": "F minor", "F minor": "Ab major",
    "Db major": "Bb minor", "Bb minor": "Db major",
    "Gb major": "Eb minor", "Eb minor": "Gb major",
    "Cb major": "Ab minor", "Ab minor": "Cb major",
}


def _cmp_key(spec: Mapping[str, Any], actual: Mapping[str, Any]) -> FieldResult:
    gate = _gate_of(spec)
    target = spec["value"]
    allow_rel = bool(spec.get("allow_relative_minor", False))
    # Key is sourced from understanding bundle, but unified_pipeline does not
    # populate it directly in AnalysisResult.to_dict() (it lives in
    # SongUnderstanding which is assembled downstream). We probe a couple of
    # plausible locations and emit "missing" if nothing is set.
    actual_val = None
    for path in [("key",), ("understanding", "key"), ("midi", "key")]:
        ref: Any = actual
        for k in path:
            if isinstance(ref, Mapping):
                ref = ref.get(k)
            else:
                ref = None
                break
        if isinstance(ref, str) and ref:
            actual_val = ref
            break
    if actual_val is None:
        return FieldResult("key", gate, False,
                           f"{target}" + (" (rel-minor ok)" if allow_rel else ""),
                           "missing",
                           message="pipeline did not surface a key")
    accepted = {target}
    if allow_rel and target in _KEY_RELATIVE:
        accepted.add(_KEY_RELATIVE[target])
    passed = actual_val in accepted
    return FieldResult("key", gate, passed,
                       f"{target}" + (" (rel-minor ok)" if allow_rel else ""),
                       actual_val)


def _cmp_detected_type(spec: Mapping[str, Any], actual: Mapping[str, Any]) -> FieldResult:
    gate = _gate_of(spec)
    target = spec["value"]
    actual_val = actual.get("detected_type")
    if not isinstance(actual_val, str):
        return FieldResult("detected_type", gate, False, str(target), "missing",
                           message="pipeline did not report detected_type")
    passed = actual_val == target
    return FieldResult("detected_type", gate, passed, str(target), actual_val)


def _cmp_section_count(spec: Mapping[str, Any], actual: Mapping[str, Any]) -> FieldResult:
    gate = _gate_of(spec)
    target = int(spec["value"])
    tol = int(spec.get("tolerance", 1))
    sections = actual.get("sections") or []
    actual_count = len(sections) if isinstance(sections, list) else 0
    delta = abs(actual_count - target)
    passed = delta <= tol
    return FieldResult("section_count", gate, passed,
                       f"{target} ± {tol}",
                       f"{actual_count} (Δ {delta})")


def _cmp_chord_count(spec: Mapping[str, Any], actual: Mapping[str, Any]) -> FieldResult:
    gate = _gate_of(spec)
    lo = int(spec.get("min", 0))
    hi = int(spec.get("max", 10**9))
    chords = actual.get("chords") or []
    actual_count = len(chords) if isinstance(chords, list) else 0
    passed = lo <= actual_count <= hi
    return FieldResult("chord_count", gate, passed,
                       f"[{lo}, {hi}]",
                       str(actual_count))


def _cmp_guitar_midi_note_count(spec: Mapping[str, Any], actual: Mapping[str, Any]) -> FieldResult:
    gate = _gate_of(spec)
    lo = int(spec.get("min", 0))
    hi = int(spec.get("max", 10**9))
    # Prefer per-stem guitar count; fall back to top-level midi note_count.
    midi_stems = actual.get("midi_stems") or {}
    actual_count: Optional[int] = None
    if isinstance(midi_stems, Mapping):
        guitar_stem = midi_stems.get("guitar")
        if isinstance(guitar_stem, Mapping):
            nc = guitar_stem.get("note_count")
            if isinstance(nc, int):
                actual_count = nc
    if actual_count is None:
        midi = actual.get("midi") or {}
        if isinstance(midi, Mapping):
            nc = midi.get("note_count")
            if isinstance(nc, int):
                actual_count = nc
    if actual_count is None:
        return FieldResult("guitar_midi_note_count", gate, False,
                           f"[{lo}, {hi}]", "missing",
                           message="pipeline did not report a guitar note count")
    passed = lo <= actual_count <= hi
    return FieldResult("guitar_midi_note_count", gate, passed,
                       f"[{lo}, {hi}]", str(actual_count))


# Registry: maps top-level expected key -> comparator function.
# Adding a new gated field is a one-line change here plus a comparator
# above. Unknown keys in the expected JSON are silently ignored to keep
# the contract permissive.
_COMPARATORS = {
    "duration_s":              _cmp_duration_s,
    "tempo_bpm":               _cmp_tempo_bpm,
    "key":                     _cmp_key,
    "detected_type":           _cmp_detected_type,
    "section_count":           _cmp_section_count,
    "chord_count":             _cmp_chord_count,
    "guitar_midi_note_count":  _cmp_guitar_midi_note_count,
}


def compare(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> List[FieldResult]:
    """Run every comparator for which a spec is present in `expected`.

    Returns the list of FieldResults in the order they appear in
    `_COMPARATORS` (stable, so reports diff cleanly across runs).
    """
    results: List[FieldResult] = []
    for key, cmp in _COMPARATORS.items():
        spec = expected.get(key)
        if spec is None:
            continue
        if not isinstance(spec, Mapping):
            raise ValueError(f"expected[{key!r}] must be an object, got {type(spec).__name__}")
        results.append(cmp(spec, actual))
    return results


# ---------------------------------------------------------------------------
# Exit-code rollup
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_HARD_FAIL = 1
EXIT_HARNESS_ERROR = 2


def compute_exit_code(all_results: Sequence[Tuple[CorpusEntry, Sequence[FieldResult]]]) -> int:
    """Walk all per-entry results; return EXIT_HARD_FAIL on any hard FAIL."""
    for _entry, results in all_results:
        for r in results:
            if not r.passed and r.gate == "hard":
                return EXIT_HARD_FAIL
    return EXIT_OK


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def format_markdown_report(
    all_results: Sequence[Tuple[CorpusEntry, Sequence[FieldResult]]],
    *,
    run_iso: str,
    tier_filter: str,
    pipeline_version: str = "(unknown)",
    runtime_s: Optional[float] = None,
    errors: Optional[Sequence[Tuple[str, str]]] = None,
) -> str:
    """Render a single human-readable Markdown report.

    `errors` is a list of (entry_id, message) for entries the harness
    failed to run at all (e.g. missing audio file). They're shown in a
    distinct section so an operator can tell "regression" from "harness
    couldn't even reach the pipeline".
    """
    lines: List[str] = []
    n_entries = len(all_results)
    n_fields = sum(len(r) for _, r in all_results)
    n_pass = sum(1 for _, rs in all_results for r in rs if r.passed)
    n_warn = sum(1 for _, rs in all_results for r in rs if not r.passed and r.gate == "soft")
    n_fail = sum(1 for _, rs in all_results for r in rs if not r.passed and r.gate == "hard")
    err_list = list(errors or [])

    overall = "PASS" if n_fail == 0 and not err_list else "FAIL"
    if n_fail == 0 and err_list:
        overall = "ERROR"  # harness couldn't run; not a regression but not a pass either

    lines.append(f"# Founder Validation Corpus — {overall}")
    lines.append("")
    lines.append(f"- **Run**: `{run_iso}`")
    lines.append(f"- **Tier filter**: `{tier_filter}`")
    lines.append(f"- **Pipeline version**: `{pipeline_version}`")
    if runtime_s is not None:
        lines.append(f"- **Wall time**: {runtime_s:.1f}s")
    lines.append(f"- **Entries run**: {n_entries}")
    lines.append(f"- **Fields checked**: {n_fields}  (PASS={n_pass}  WARN={n_warn}  FAIL={n_fail})")
    if err_list:
        lines.append(f"- **Harness errors**: {len(err_list)}")
    lines.append("")

    if err_list:
        lines.append("## Harness errors")
        lines.append("")
        lines.append("| Entry | Message |")
        lines.append("|---|---|")
        for entry_id, msg in err_list:
            lines.append(f"| `{entry_id}` | {msg} |")
        lines.append("")

    lines.append("## Per-entry results")
    lines.append("")
    for entry, results in all_results:
        worst = "PASS"
        for r in results:
            if not r.passed and r.gate == "hard":
                worst = "FAIL"
                break
            if not r.passed and r.gate == "soft":
                worst = "WARN"
        lines.append(f"### `{entry.id}` — {worst}  *(tier: {entry.tier})*")
        if entry.notes:
            lines.append("")
            lines.append(f"> {entry.notes}")
        lines.append("")
        if not results:
            lines.append("_no expected fields declared_")
            lines.append("")
            continue
        lines.append("| Field | Gate | Status | Expected | Actual | Note |")
        lines.append("|---|---|---|---|---|---|")
        for r in results:
            lines.append(
                f"| `{r.field}` | {r.gate} | **{r.severity}** | "
                f"{r.expected_repr} | {r.actual_repr} | {r.message or ''} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"
