"""VirtualStudio — manufactures supervised (mixture, guitar_target) pairs from a
guitar Asset + a Scenario + a ScenarioProvider.

Ground truth is exact by construction: per-stem processing (gain -> soft-comp ->
pan) keeps the mix LINEAR, so mixture == guitar_target + sum(treated backings), and
the guitar_target IS the guitar's exact contribution to the mixture. Fully
deterministic (seed derived from guitar content-hash + scenario signature) ->
byte-identical on replay. Every mixing decision is recorded for perfect replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from .asset import Asset, Kind, Role, Status
from .catalog import AssetCatalog
from .pipeline import PipelineRunner
from .scenario import Scenario
from .backing import ScenarioProvider, SR
from .. import config
from ..hashing import file_sha256

_MIX_VERSION = "vstudio/1"


def _db(x): return float(10.0 ** (x / 20.0))


def _to_stereo(y: np.ndarray) -> np.ndarray:
    if y.ndim == 1:
        return np.stack([y, y], axis=1)
    if y.shape[1] == 1:
        return np.repeat(y, 2, axis=1)
    return y[:, :2]


def _pan(y_stereo: np.ndarray, pan: float) -> np.ndarray:
    # equal-power pan
    ang = (pan + 1) * 0.25 * np.pi
    return y_stereo * np.array([np.cos(ang), np.sin(ang)])


def _softcomp(y: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return y
    drive = 1.0 + 3.0 * amount
    return np.tanh(y * drive) / drive


@dataclass
class StudioReport:
    pairs: list = field(default_factory=list)      # (mixture_asset, target_asset)
    rejected: list = field(default_factory=list)   # (target_asset, reasons)
    def counts(self):
        return {"pairs": len(self.pairs), "rejected": len(self.rejected)}


class VirtualStudio:
    def __init__(self, catalog: AssetCatalog, runner: PipelineRunner,
                 out_dir: Optional[Path] = None):
        self.catalog = catalog
        self.runner = runner
        self.out = Path(out_dir) if out_dir else (config.FACTORY_DIR / "studio")

    def _seed(self, guitar: Asset, scenario: Scenario) -> int:
        return (int(guitar.content_hash[:8], 16) ^ int(scenario.signature()[4:12], 16)) & 0x7FFFFFFF

    def generate(self, guitar: Asset, scenario: Scenario, provider: ScenarioProvider):
        """Produce one (mixture, target) pair. Deterministic + fully provenanced."""
        seed = self._seed(guitar, scenario)
        g, sr = sf.read(str(guitar.path), always_2d=True)
        g = g.astype(np.float32)
        n = g.shape[0]

        # guitar-in-mix (the exact target): gain -> comp -> pan
        gt = _pan(_softcomp(_to_stereo(g) * _db(scenario.guitar_level_db), scenario.compression),
                  scenario.guitar_pan)

        backings = []
        mix = gt.copy()
        for rs in scenario.roles:
            b = provider.backing_for(rs, scenario, seed, n)              # mono, deterministic
            lvl = rs.level_db + (scenario.masking_db if rs.is_masker else 0.0)
            bt = _pan(_softcomp(_to_stereo(b) * _db(lvl), scenario.compression), rs.pan)
            mix = mix + bt
            backings.append({
                "role": rs.role, "voice": rs.voice, "level_db": rs.level_db,
                "applied_db": round(lvl, 3), "pan": rs.pan, "is_masker": rs.is_masker,
                "backing_sha256": _hash_arr(b),
            })

        # single normalization applied to BOTH -> target stays the exact guitar component
        peak = float(np.max(np.abs(mix)) + 1e-9)
        norm = 0.89 / peak if peak > 0.89 else 1.0
        mix = (mix * norm).astype(np.float32)
        gt = (gt * norm).astype(np.float32)

        base = self.out / scenario.name / guitar.asset_id[:12]
        base.mkdir(parents=True, exist_ok=True)
        mix_path = base / "mixture.wav"
        tgt_path = base / "guitar_target.wav"
        sf.write(str(mix_path), mix, sr)
        sf.write(str(tgt_path), gt, sr)

        prov = {
            "scenario": scenario.name, "scenario_version": scenario.version,
            "scenario_signature": scenario.signature(), "benchmark_regime": scenario.benchmark_regime,
            "provider": provider.id, "backing_dataset": provider.dataset_key,
            "backings": backings,
            "guitar_level_db": scenario.guitar_level_db, "guitar_pan": scenario.guitar_pan,
            "masking_db": scenario.masking_db, "compression": scenario.compression,
            "norm_gain": round(norm, 6), "seed": seed,
            "dsp": "gain->softcomp->pan->sum->normalize (per-stem, linear sum)",
            "mix_version": _MIX_VERSION, "software": _MIX_VERSION,
        }
        pair_id = f"{scenario.signature()}:{guitar.asset_id}"

        target = guitar.derive(tgt_path, stage=f"scenario_target:{scenario.label}",
                               params={**prov, "pair_id": pair_id, "role": "guitar_in_mix"},
                               kind=Kind.STEM, role=Role.GUITAR)
        tmd = dict(target.metadata); tmd.update({"scenario": scenario.name,
                    "scenario_version": scenario.version, "benchmark_regime": scenario.benchmark_regime,
                    "pair_id": pair_id})
        target = target.evolve(stage="tag", metadata=tmd)

        mixture = guitar.derive(mix_path, stage=f"scenario:{scenario.label}",
                                params={**prov, "pair_id": pair_id, "target_asset": target.asset_id},
                                kind=Kind.MIXTURE, role=Role.MIX)
        mmd = dict(mixture.metadata); mmd.update({"scenario": scenario.name,
                    "scenario_version": scenario.version, "benchmark_regime": scenario.benchmark_regime,
                    "pair_id": pair_id, "roles": [r.role for r in scenario.roles]})
        mixture = mixture.evolve(stage="tag", metadata=mmd)

        # audit the guitar TARGET (the thing whose quality gates the pair)
        target = self.runner.audit(target)
        return mixture, target

    def generate_dataset(self, guitar_assets: list, scenarios: list,
                         provider: ScenarioProvider) -> StudioReport:
        report = StudioReport()
        for guitar in guitar_assets:
            for scenario in scenarios:
                mixture, target = self.generate(guitar, scenario, provider)
                if target.audit_status == Status.REJECT:
                    report.rejected.append((target, target.audit.get("reasons") if target.audit else None))
                    continue
                self.catalog.add(target)
                self.catalog.add(mixture)
                report.pairs.append((mixture, target))
        return report


def _hash_arr(y: np.ndarray) -> str:
    import hashlib
    return hashlib.sha256(np.ascontiguousarray(y.astype(np.float32)).tobytes()).hexdigest()[:16]


def build_supervised_manifest(catalog: AssetCatalog, name: str, out_dir: Path, *,
                              intended_use: str = "commercial_eligible") -> Path:
    """Emit a (mixture, target) training manifest from catalog pairs. Pairs are
    matched by `pair_id`. Reuses training_data.build_manifest (license gate)."""
    from .. import training_data

    def _backing_ds(mixture) -> Optional[str]:
        for s in mixture.lineage:
            if s.stage.startswith("scenario:"):
                return s.params.get("backing_dataset")
        return None

    # a mixture combines guitar + backing; BOTH datasets must clear the gate.
    if intended_use == "commercial_eligible":
        backing_ds = {_backing_ds(a) for a in catalog.all() if a.kind == Kind.MIXTURE}
        for ds in backing_ds - {None}:
            facts = training_data.dataset_facts(ds)
            if not facts["commercial_training_allowed"]:
                raise training_data.ProvenanceError(
                    f"backing dataset '{ds}' ({facts['license']}) is NOT commercial-eligible; "
                    "the mixture inherits its most-restrictive component")
    targets = {a.metadata.get("pair_id"): a for a in catalog.all()
               if a.kind == Kind.STEM and a.metadata.get("pair_id")}
    tracks = []
    for a in catalog.all():
        if a.kind != Kind.MIXTURE:
            continue
        pid = a.metadata.get("pair_id")
        tgt = targets.get(pid)
        if tgt is None or tgt.audit_status == Status.REJECT:
            continue
        tracks.append({
            "dataset": a.dataset_key,                 # guitar source (licensable); backing gated separately
            "backing_dataset": _backing_ds(a),
            "track_id": pid,
            "target_stem": "guitar",
            "mixture_path": a.path,
            "target_path": tgt.path,
            "scenario": a.metadata.get("scenario"),
            "benchmark_regime": a.metadata.get("benchmark_regime"),
            "recipe": a.metadata.get("recipe"),
            "quality_score": tgt.quality_score,
        })
    return training_data.build_manifest(name, tracks, intended_use=intended_use, out_dir=out_dir)
