"""Training-data provenance registry — hard engineering property.

Every dataset that can appear in a training manifest MUST be registered
here with machine-readable license facts. Manifest builders REFUSE
tracks from unregistered datasets, and REFUSE to build a
commercial-eligible manifest containing any dataset where
commercial_training_allowed is not True.

Checkpoint provenance embeds the full dataset lineage so "exactly how
was this checkpoint produced?" stays answerable forever.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import List

from . import config

TRAINING_DATA_REGISTRY = {
    "slakh2100": {
        "license": "CC-BY-4.0",
        "commercial_training_allowed": True,
        "redistribution_allowed": True,
        "attribution": "Slakh2100 (Manilow et al., MERL), CC BY 4.0, Zenodo 4599666",
        "source": "https://zenodo.org/records/4599666",
        "notes": ("Synthetic (Kontakt-class rendered). CC-BY grant from authors; "
                  "sample-library EULA asterisk noted in SEPARATOR_TIER2_PROPOSAL "
                  "§39 — counsel review before production weights ship."),
    },
    "babyslakh": {
        "license": "CC-BY-4.0",
        "commercial_training_allowed": True,
        "redistribution_allowed": True,
        "attribution": "BabySlakh (MERL), CC BY 4.0",
        "source": "http://www.slakh.com/",
        "notes": "16kHz toy subset; same terms as slakh2100.",
    },
    "guitarset": {
        "license": "CC-BY-4.0",
        "commercial_training_allowed": True,
        "redistribution_allowed": True,
        "attribution": "GuitarSet (Xi et al.), CC BY 4.0, Zenodo 3371780",
        "source": "https://zenodo.org/records/3371780",
        "notes": "Real solo acoustic guitar recordings.",
    },
    # Research-only entries (benchmarking ONLY; never in commercial manifests)
    "moisesdb": {
        "license": "CC-BY-NC-SA-4.0",
        "commercial_training_allowed": False,
        "redistribution_allowed": False,
        "attribution": "MoisesDB (Moises.ai), CC BY-NC-SA 4.0",
        "source": "https://github.com/moises-ai/moises-db",
        "notes": "RESEARCH ONLY. Firewalled from commercial checkpoints.",
    },
    "musdb18hq": {
        "license": "educational-only (custom)",
        "commercial_training_allowed": False,
        "redistribution_allowed": False,
        "attribution": "MUSDB18-HQ (Rafii et al.)",
        "source": "https://zenodo.org/records/3338373",
        "notes": "RESEARCH ONLY. Firewalled from commercial checkpoints.",
    },
}


class ProvenanceError(RuntimeError):
    pass


def dataset_facts(dataset: str) -> dict:
    facts = TRAINING_DATA_REGISTRY.get(dataset)
    if facts is None:
        raise ProvenanceError(
            f"dataset '{dataset}' is not in TRAINING_DATA_REGISTRY — register "
            "it with license facts before any training use")
    return facts


def build_manifest(name: str, tracks: List[dict], *, intended_use: str,
                   out_dir: Path) -> Path:
    """Write a training manifest with embedded per-dataset provenance.

    tracks: [{dataset, track_id, target_stem, mixture_path, target_path, ...}]
    intended_use: "research_only" | "commercial_eligible"
    Raises ProvenanceError if commercial_eligible and any dataset is not
    commercial_training_allowed, or any dataset is unregistered.
    """
    if intended_use not in ("research_only", "commercial_eligible"):
        raise ProvenanceError(f"invalid intended_use {intended_use!r}")
    datasets = sorted({t["dataset"] for t in tracks})
    lineage = {}
    for ds in datasets:
        facts = dataset_facts(ds)
        if intended_use == "commercial_eligible" and not facts["commercial_training_allowed"]:
            raise ProvenanceError(
                f"dataset '{ds}' ({facts['license']}) is NOT commercial-training-"
                "eligible; it cannot enter a commercial_eligible manifest")
        lineage[ds] = facts
    manifest = {
        "name": name,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "intended_use": intended_use,
        "n_tracks": len(tracks),
        "dataset_lineage": lineage,
        "tracks": tracks,
    }
    payload = json.dumps(manifest, indent=1, sort_keys=True)
    manifest["manifest_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.manifest.json"
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return path


def checkpoint_provenance(*, manifest_path: Path, git_commit: str,
                          architecture: str, initial_weights: str,
                          initial_weights_provenance: str, config_ref: str,
                          seed: int, extra: dict | None = None) -> dict:
    """Provenance block to store NEXT TO every trained checkpoint."""
    manifest = json.loads(Path(manifest_path).read_text())
    return {
        "git_commit": git_commit,
        "architecture": architecture,
        "initial_weights": initial_weights,
        "initial_weights_provenance": initial_weights_provenance,
        "config": config_ref,
        "seed": seed,
        "training_manifest": manifest["name"],
        "training_manifest_sha256": manifest.get("manifest_sha256"),
        "intended_use": manifest["intended_use"],
        "dataset_lineage": manifest["dataset_lineage"],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **(extra or {}),
    }
