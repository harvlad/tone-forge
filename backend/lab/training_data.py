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
        "notes": ("Real solo acoustic guitar, per-string hexaphonic isolation + mic mix. "
                  "Phase-2 GREEN tier: passes both license layers. No mixture context "
                  "(stem source for synthesis)."),
    },
    "guitar_techs": {
        "license": "CC-BY-4.0",
        "commercial_training_allowed": True,
        "redistribution_allowed": True,
        "attribution": "Guitar-TECHS (Pedroza et al., ICASSP 2025), CC BY 4.0, Zenodo 14963133",
        "source": "https://zenodo.org/records/14963133",
        "notes": ("Real electric guitar: DI + micced-amp + multi-mic, synced per-string MIDI. "
                  "GREEN tier. Known <=100ms cross-path misalignment — correct before "
                  "alignment-sensitive use. No mixture context."),
    },
    "egfxset": {
        "license": "CC-BY-4.0",
        "commercial_training_allowed": True,
        "redistribution_allowed": True,
        "attribution": "EGFxSet (Pedroza et al., ISMIR 2022 LBD), CC BY 4.0, Zenodo 7044411",
        "source": "https://zenodo.org/records/7044411",
        "notes": ("Real-hardware guitar FX on isolated single notes (12 effects). GREEN tier. "
                  "Isolated notes only -> distortion/effect TIMBRE bank for augmentation."),
    },
    "rnd_backing": {
        "license": "research-only (mixed provenance)",
        "commercial_training_allowed": False,
        "redistribution_allowed": False,
        "attribution": "R&D backing pool (separated stems from copyrighted material)",
        "source": "internal R&D",
        "notes": ("RESEARCH ONLY. Real backing stems for validating the real-backing "
                  "studio path; firewalled from commercial checkpoints. Commercial runs "
                  "require CC/licensed backing (a separate pool)."),
    },
    "synthetic_backing": {
        "license": "CC0 (owned)",
        "commercial_training_allowed": True,
        "redistribution_allowed": True,
        "attribution": "Riley Virtual Studio synthetic backing (generated, owned)",
        "source": "backend/lab/factory/backing.py",
        "notes": ("Deterministically generated backing stems (drums/bass/vocal/synth) used "
                  "by the Virtual Studio. Owned/CC0. Not realistic music — a license-clean, "
                  "reproducible substrate to validate the studio; real stem pools plug in later."),
    },
    "musan": {
        "license": "CC-BY-4.0",
        "commercial_training_allowed": True,
        "redistribution_allowed": True,
        "attribution": "MUSAN (Snyder et al.), CC BY 4.0, OpenSLR 17",
        "source": "https://www.openslr.org/17/",
        "notes": ("GREEN tier (PD/CC sources). No guitar — noise/interference augmentation "
                  "to harden the separator. 16kHz ceiling."),
    },
    "guitarduets": {
        "license": "CC-BY-4.0",
        "commercial_training_allowed": False,   # VERIFY exact CC variant + real-recording provenance on Zenodo
        "redistribution_allowed": False,
        "attribution": "GuitarDuets (Glytsos et al., ISMIR 2024), Zenodo 12802440",
        "source": "https://zenodo.org/records/12802440",
        "notes": ("AMBER: reported CC-BY but UNVERIFIED variant + real-recording provenance. "
                  "Only guitar-vs-guitar (same-timbre) separation set — high value. Flip to "
                  "commercial_training_allowed=True ONLY after confirming CC-BY-4.0 on the record."),
    },
    "egdb": {
        "license": "unknown (no published license)",
        "commercial_training_allowed": False,
        "redistribution_allowed": False,
        "attribution": "EGDB (Chen et al., ICASSP 2022)",
        "source": "https://ss12f32v.github.io/Guitar-Transcription/",
        "notes": ("AMBER: real electric DI + amp renders (excellent distortion pairs) but NO "
                  "published license — must contact author before any use. Firewalled until cleared."),
    },
    "goat": {
        "license": "access-gated / TBD",
        "commercial_training_allowed": False,
        "redistribution_allowed": False,
        "attribution": "GOAT (Loth et al., ISMIR 2025), Zenodo 17706552 (request access)",
        "source": "https://github.com/JackJamesLoth/GOAT-Dataset",
        "notes": ("AMBER: largest real electric DI set + amp augmentation, but Zenodo "
                  "access-gated and license undetailed. Confirm terms before use."),
    },
    "idmt_smt_guitar": {
        "license": "CC-BY-NC-ND-4.0",
        "commercial_training_allowed": False,
        "redistribution_allowed": False,
        "attribution": "IDMT-SMT-Guitar (Kehling et al., Fraunhofer IDMT)",
        "source": "https://www.idmt.fraunhofer.de/en/publications/datasets/guitar.html",
        "notes": ("RED: CC-BY-NC-ND — NonCommercial AND NoDerivatives. ND arguably bars a "
                  "trained model. Research-only; Fraunhofer may relicense commercially by arrangement."),
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
    "medleydb": {
        "license": "CC-BY-NC-SA-4.0",
        "commercial_training_allowed": False,
        "redistribution_allowed": False,
        "attribution": "MedleyDB (Bittner et al., NYU MARL), CC BY-NC-SA 4.0",
        "source": "https://medleydb.weebly.com/",
        "notes": ("RESEARCH ONLY. Real multitrack with per-instrument (incl. guitar) "
                  "stems, genre-diverse. Firewalled from commercial checkpoints — "
                  "Phase-2 sourcing survey (2026-08): commercial license negotiable with MARL."),
    },
    "cambridge_mt": {
        "license": "educational-only (custom)",
        "commercial_training_allowed": False,
        "redistribution_allowed": False,
        "attribution": "Cambridge-MT 'Mixing Secrets' Free Multitrack Library",
        "source": "https://www.cambridge-mt.com/ms/mtk/",
        "notes": ("RESEARCH ONLY. Large real multitrack library WITH guitar stems, but "
                  "'educational use only; no commercial without per-contributor permission'. "
                  "Firewalled. Per-track CC licenses vary — a curated CC-BY subset could be "
                  "commercial-eligible IF each contributor's license is verified individually."),
    },
    "fma_ccby": {
        "license": "CC-BY-4.0 (subset)",
        "commercial_training_allowed": True,
        "redistribution_allowed": True,
        "attribution": "Free Music Archive — CC-BY / CC-BY-SA subset only (Defferrard et al.)",
        "source": "https://github.com/mdeff/fma",
        "notes": ("Commercial-eligible ONLY for the ~10.5% of tracks under CC-BY/CC-BY-SA/"
                  "OAL/Free-Art. BUT these are MIXED tracks (no isolated guitar stem) -> not "
                  "directly usable as separation ground truth. Candidate for a build-your-own "
                  "stem pipeline, not off-the-shelf."),
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
