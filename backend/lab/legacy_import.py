"""Rescue + register legacy research artifacts.

1. Copies volatile /tmp per-note experiment dumps into lab_data/legacy/
   (they are the ONLY per-note records of past experiments and /tmp does
   not survive reboots).
2. Registers historical experiments (validated BP/MT3 comparison,
   fine-tune experiments) in the experiment registry with provenance
   `imported_from_legacy_experiment`.

NOTE ON PROVENANCE: the legacy per-note dumps came from the PRODUCTION
ensemble extractor (extract_midi_lead_ensemble), NOT the validated
official-BP benchmark path, and they don't embed model config.  They are
preserved for analysis but are NOT imported into the prediction cache as
canonical predictions.  The validated comparison_results.json is
aggregate-only (no per-note data existed to import).
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

from . import config, experiments

TMP_ARTIFACTS = [
    ("/tmp/octave_oracle_data.json", "per-note octave oracle dump (production ensemble)"),
    ("/tmp/offset_oracle_data.json", "per-note offset oracle dump (production ensemble)"),
    ("/tmp/basic_pitch_internal.json", "per-note BP internal activations dump"),
    ("/tmp/duration_analysis.json", "per-note duration analysis dump"),
    ("/tmp/duration_correction_results.json", "duration correction simulation results"),
    ("/tmp/error_taxonomy_full.json", "per-stem error taxonomy results"),
    ("/tmp/taxonomy_checkpoint.json", "error taxonomy checkpoint"),
    ("/tmp/audio_offset_results.json", "audio offset experiment results"),
    ("/tmp/slakh_bench_checkpoint.json", "slakh benchmark checkpoint"),
    ("/tmp/ensemble_benchmark_results.json", "ensemble benchmark results"),
]

LEGACY_EXPERIMENTS = [
    {
        "kind": "legacy_benchmark",
        "models": ["basic_pitch", "mt3"],
        "artifact": "backend/experiments/mt3_comparison/comparison_results.json",
        "notes": ("VALIDATED BP vs MT3 benchmark: 100 validation stems, 100ms "
                  "tolerance, official bp_predict (onset 0.5, frame 0.3, "
                  "min_note 127.7ms, melodia) vs mt3_infer mt3_pytorch. "
                  "AGGREGATE ONLY — per-note predictions were not persisted. "
                  "BP F1 0.313/recall 0.423; MT3 F1 0.132/recall 0.110; "
                  "per-note oracle recall 0.472."),
        "decision": "MT3 = Bass/Synth-Lead specialist candidate; BP = incumbent generalist.",
    },
    {
        "kind": "legacy_finetune",
        "models": ["basic_pitch"],
        "artifact": "backend/experiments/basic_pitch_finetune/",
        "notes": ("Fine-tune experiments A/B/B3/D1/D2 (Slakh training). "
                  "SavedModels + per-epoch aggregate metrics preserved in place. "
                  "Fine-tuned synth-lead model rejected for production "
                  "(ICASSP 53% vs finetune 20% on internal check)."),
        "decision": "Fine-tuning path parked; pretrained ICASSP remains production.",
    },
]


def run(dry_run: bool = False) -> dict:
    config.ensure_dirs()
    rescued, missing = [], []
    manifest_path = config.LEGACY_DIR / "MANIFEST.json"
    manifest = {"rescued_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "artifacts": []}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            pass
    known = {a["dest"] for a in manifest["artifacts"]}

    for src_str, desc in TMP_ARTIFACTS:
        src = Path(src_str)
        dest = config.LEGACY_DIR / src.name
        if not src.exists():
            if not dest.exists():
                missing.append(src_str)
            continue
        if dest.exists() and dest.stat().st_size == src.stat().st_size:
            continue  # already rescued
        if not dry_run:
            shutil.copy2(src, dest)
            entry = {
                "dest": str(dest.relative_to(config.LAB_DATA)),
                "source": src_str,
                "description": desc,
                "provenance": "imported_from_legacy_experiment",
                "trust": "uncertain — production-ensemble output, config not embedded",
                "size": src.stat().st_size,
                "source_mtime": src.stat().st_mtime,
            }
            if entry["dest"] not in known:
                manifest["artifacts"].append(entry)
        rescued.append(src_str)

    if not dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=1))
        _register_legacy_experiments()

    print(f"rescued from /tmp: {len(rescued)}")
    for r in rescued:
        print(f"  + {r}")
    if missing:
        print(f"not found (possibly already lost to reboot): {len(missing)}")
        for m in missing:
            print(f"  - {m}")
    print(f"legacy manifest: {manifest_path}")
    return {"rescued": rescued, "missing": missing}


def _register_legacy_experiments() -> None:
    existing = {(r.get("kind"), r.get("notes", "")[:60])
                for r in experiments.load_all()}
    for e in LEGACY_EXPERIMENTS:
        if (e["kind"], e["notes"][:60]) in existing:
            continue
        summary = {}
        artifact_path = config.REPO_ROOT / e["artifact"]
        if artifact_path.suffix == ".json" and artifact_path.exists():
            try:
                raw = json.loads(artifact_path.read_text())
                summary = {m: raw[m]["global"] for m in ("basic_pitch", "mt3")
                           if m in raw and "global" in raw[m]}
                if "head_to_head" in raw:
                    summary["head_to_head"] = raw["head_to_head"].get("global", {})
            except Exception:
                pass
        experiments.record(
            e["kind"], models=e["models"], dataset="slakh2100",
            split="validation", tier="validated100",
            n_stems=100 if e["kind"] == "legacy_benchmark" else 0,
            result_summary=summary, notes=e["notes"], decision=e["decision"],
            artifacts=[e["artifact"]],
            provenance="imported_from_legacy_experiment")
