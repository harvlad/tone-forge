"""CorpusValidator — the whole-corpus quality gate for manufactured (mixture, target)
pairs. The per-asset Dataset Auditor (dataset_auditor.py) judges ONE file in
isolation; this gate judges the CORPUS: cross-pair duplication, pair integrity,
balance across coverage dimensions, and manifest validity — things no single-file
audit can see.

Every failing asset is marked REVIEW or REJECT automatically (an immutable evolve()
with a `corpus_gate` lineage step recording the reason), so a bad pair can never
silently enter a manifest. Read-only over audio (no mutation of the wav files).

Design mirrors the rest of the factory: reuse existing primitives (Status, Kind,
coverage.DIMENSIONS, training_data license gate) rather than reinventing them.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from .asset import Asset, Kind, Status
from .catalog import AssetCatalog
from .coverage import DIMENSIONS

# ---- thresholds (one place; every gate reads from here) ----
SILENCE_RMS = 1e-4          # below this a stem is effectively silent
CLIP_PEAK = 0.999           # sample peak at/above this = clipping
CLIP_FRAC = 1e-3            # fraction of clipped samples that trips REVIEW
IMBALANCE_RATIO = 40.0      # max/min bucket ratio (over populated buckets) before a dim is "imbalanced"
# Only the axes the Virtual Studio CONTROLS gate the "balanced" verdict. Intrinsic
# guitar axes (key/pickup/tempo/genre) are surfaced as imbalance INFO and become the
# Coverage Planner's acquisition priorities — an intrinsic gap is a to-do, not a defect.
BALANCE_GATE_DIMS = ("masking_level", "acoustic_vs_electric", "difficulty")
MIN_LINEAGE = 3             # ingest + (>=1 transform/tag) + audit
REQUIRED_MIX = ("pair_id", "scenario", "benchmark_regime", "roles")
REQUIRED_TGT = ("pair_id", "scenario", "guitar_type")


@dataclass
class Check:
    name: str
    failed: list = field(default_factory=list)   # human-readable offenders (pair_id / detail)
    action: str = "none"                          # "review" | "reject" | "none"
    @property
    def ok(self) -> bool:
        return not self.failed
    def to_dict(self) -> dict:
        return {"ok": self.ok, "n_failed": len(self.failed),
                "action": self.action, "examples": self.failed[:8]}


@dataclass
class CorpusReport:
    n_pairs: int = 0
    n_mixtures: int = 0
    n_targets: int = 0
    checks: dict = field(default_factory=dict)     # name -> Check
    marked_review: int = 0
    marked_reject: int = 0
    balanced: bool = True
    imbalance: dict = field(default_factory=dict)  # dim -> ratio (only imbalanced dims)
    manifest_ok: bool = False
    manifest_tracks: int = 0
    manifest_error: Optional[str] = None

    @property
    def clean(self) -> bool:
        """The ADMITTED corpus is production-ready. Reject-gates firing is the gate
        doing its job — those pairs are marked REJECT and excluded from the manifest;
        what matters is that the manifest that ships is valid + coverage is balanced.
        (marked_review is advisory and never blocks.)"""
        return self.manifest_ok and self.balanced

    def to_dict(self) -> dict:
        return {
            "n_pairs": self.n_pairs, "n_mixtures": self.n_mixtures, "n_targets": self.n_targets,
            "checks": {k: v.to_dict() for k, v in self.checks.items()},
            "marked_review": self.marked_review, "marked_reject": self.marked_reject,
            "balanced": self.balanced, "imbalance": self.imbalance,
            "manifest_ok": self.manifest_ok, "manifest_tracks": self.manifest_tracks,
            "manifest_error": self.manifest_error, "clean": self.clean,
        }


def _prov_sig(asset: Asset) -> str:
    """Timestamp-free signature of an asset's full lineage — two DISTINCT pairs must
    never share it (a collision means a provenance/identity bug)."""
    parts = []
    for s in asset.lineage:
        p = {k: v for k, v in dict(s.params or {}).items()
             if k not in ("ingested_at", "derived_at", "at", "verified_at")}
        parts.append([s.stage, json.dumps(p, sort_keys=True, default=str)])
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def _audio_stats(path: str) -> dict:
    """rms + peak + clip fraction; finite-safe. One cheap read, no librosa. A missing
    or unreadable file returns error=... instead of raising — a corpus gate must never
    crash on one bad file (same worker discipline as the rest of the factory)."""
    base = {"rms": 0.0, "peak": 0.0, "clip_frac": 0.0, "finite": False, "error": None}
    try:
        y, _ = sf.read(str(path), always_2d=True)
    except Exception as e:  # noqa: BLE001 — missing/corrupt/unreadable
        return {**base, "error": f"{type(e).__name__}: {str(e)[:80]}"}
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return {**base, "finite": True, "error": "empty"}
    finite = bool(np.all(np.isfinite(y)))
    if not finite:
        y = np.nan_to_num(y)
    return {"rms": float(np.sqrt(np.mean(y ** 2))), "peak": float(np.max(np.abs(y))),
            "clip_frac": float(np.mean(np.abs(y) >= CLIP_PEAK)), "finite": finite, "error": None}


class CorpusValidator:
    def __init__(self, catalog: AssetCatalog):
        self.catalog = catalog

    def _mark(self, asset: Asset, status: str, reason: str) -> None:
        """Immutable status change + provenance of WHY (re-add overwrites same asset_id)."""
        self.catalog.add(asset.evolve(stage="corpus_gate", audit_status=status,
                                      params={"corpus_gate": status, "reason": reason}))

    def validate(self, *, mark: bool = True) -> CorpusReport:
        rep = CorpusReport()
        assets = self.catalog.all()
        mixtures = [a for a in assets if a.kind == Kind.MIXTURE]
        targets = [a for a in assets if a.kind == Kind.STEM and a.metadata.get("pair_id")]
        rep.n_mixtures, rep.n_targets = len(mixtures), len(targets)

        tgt_by_pid = defaultdict(list)
        for t in targets:
            tgt_by_pid[t.metadata["pair_id"]].append(t)

        checks = {n: Check(n) for n in (
            "orphan_pair", "collapsed_pair", "missing_audio", "silent_mixture", "silent_target",
            "invalid_rms", "clipping", "duplicate_pair", "duplicate_provenance",
            "duplicate_mixture", "duplicate_target", "missing_metadata", "missing_lineage")}
        checks["missing_audio"].action = "reject"
        checks["orphan_pair"].action = "reject"
        checks["collapsed_pair"].action = "reject"
        checks["silent_mixture"].action = "reject"
        checks["silent_target"].action = "reject"
        checks["invalid_rms"].action = "reject"
        checks["clipping"].action = "review"
        checks["duplicate_pair"].action = "review"
        checks["duplicate_provenance"].action = "reject"
        checks["duplicate_mixture"].action = "reject"
        checks["duplicate_target"].action = "reject"
        checks["missing_metadata"].action = "review"
        checks["missing_lineage"].action = "review"

        seen_pairhash: dict = {}
        seen_provsig: dict = {}
        mix_hash_pids = defaultdict(set)
        tgt_hash_pids = defaultdict(set)
        to_review: list = []
        to_reject: list = []

        for mix in mixtures:
            pid = mix.metadata.get("pair_id")
            tgts = tgt_by_pid.get(pid, [])
            if not tgts:
                checks["orphan_pair"].failed.append(f"{pid}: mixture without target")
                to_reject.append((mix, "orphan: no target"))
                continue
            tgt = tgts[0]

            # collapsed pair (mixture bytes == target bytes -> silent-backing bug signature)
            if mix.content_hash == tgt.content_hash:
                checks["collapsed_pair"].failed.append(f"{pid}: mixture==target content")
                to_reject += [(mix, "collapsed: mix==tgt"), (tgt, "collapsed: mix==tgt")]

            # cross-corpus content-hash collisions (distinct pairs, same audio)
            mix_hash_pids[mix.content_hash].add(pid)
            tgt_hash_pids[tgt.content_hash].add(pid)

            # duplicate exact pair (same mix+tgt content on two pair_ids -> redundant data)
            ph = f"{mix.content_hash}:{tgt.content_hash}"
            if ph in seen_pairhash and seen_pairhash[ph] != pid:
                checks["duplicate_pair"].failed.append(f"{pid} == {seen_pairhash[ph]}")
                to_review += [(mix, f"dup pair of {seen_pairhash[ph]}")]
            else:
                seen_pairhash.setdefault(ph, pid)

            # duplicate provenance chain
            ps = _prov_sig(mix)
            if ps in seen_provsig and seen_provsig[ps] != pid:
                checks["duplicate_provenance"].failed.append(f"{pid} chain==={seen_provsig[ps]}")
                to_reject.append((mix, f"dup provenance of {seen_provsig[ps]}"))
            else:
                seen_provsig.setdefault(ps, pid)

            # required metadata
            miss_m = [k for k in REQUIRED_MIX if k not in mix.metadata]
            miss_t = [k for k in REQUIRED_TGT if k not in tgt.metadata]
            if miss_m or miss_t:
                checks["missing_metadata"].failed.append(f"{pid}: mix{miss_m} tgt{miss_t}")
                if miss_m:
                    to_review.append((mix, f"missing meta {miss_m}"))
                if miss_t:
                    to_review.append((tgt, f"missing meta {miss_t}"))

            # lineage depth
            if len(mix.lineage) < MIN_LINEAGE:
                checks["missing_lineage"].failed.append(f"{pid}: mix lineage {len(mix.lineage)}")
                to_review.append((mix, "shallow lineage"))
            if len(tgt.lineage) < MIN_LINEAGE:
                checks["missing_lineage"].failed.append(f"{pid}: tgt lineage {len(tgt.lineage)}")
                to_review.append((tgt, "shallow lineage"))

            # audio-level gates (one cheap read each)
            ms = _audio_stats(mix.path)
            ts = _audio_stats(tgt.path)
            if ms["error"] or ts["error"]:
                checks["missing_audio"].failed.append(
                    f"{pid}: mix={ms['error']} tgt={ts['error']}")
                if ms["error"]:
                    to_reject.append((mix, f"unreadable audio: {ms['error']}"))
                if ts["error"]:
                    to_reject.append((tgt, f"unreadable audio: {ts['error']}"))
                continue                                  # can't run audio gates on unreadable files
            if not ms["finite"] or not ts["finite"] or math.isnan(ms["rms"]) or math.isnan(ts["rms"]):
                checks["invalid_rms"].failed.append(f"{pid}: non-finite audio")
                to_reject += [(mix, "non-finite"), (tgt, "non-finite")]
            if ms["rms"] < SILENCE_RMS:
                checks["silent_mixture"].failed.append(f"{pid}: mix rms {ms['rms']:.2e}")
                to_reject.append((mix, "silent mixture"))
            if ts["rms"] < SILENCE_RMS:
                checks["silent_target"].failed.append(f"{pid}: tgt rms {ts['rms']:.2e}")
                to_reject.append((tgt, "silent target"))
            if ms["clip_frac"] > CLIP_FRAC or ms["peak"] >= CLIP_PEAK:
                checks["clipping"].failed.append(f"{pid}: mix clip {ms['clip_frac']:.2e} peak {ms['peak']:.3f}")
                to_review.append((mix, "clipping"))

        # cross-corpus duplicate content (a hash shared by >1 pair_id)
        for h, pids in mix_hash_pids.items():
            if len(pids) > 1:
                checks["duplicate_mixture"].failed.append(f"{sorted(pids)} share mixture audio")
        for h, pids in tgt_hash_pids.items():
            if len(pids) > 1:
                checks["duplicate_target"].failed.append(f"{sorted(pids)} share target audio")

        # coverage balance over mixtures
        rep.balanced, rep.imbalance = self._balance(mixtures)

        rep.checks = checks
        rep.n_pairs = len([m for m in mixtures if tgt_by_pid.get(m.metadata.get("pair_id"))])

        # apply status marks (reject wins over review for the same asset)
        if mark:
            prev = self.catalog._autoflush
            self.catalog._autoflush = False               # one flush for the whole marking pass
            try:
                reject_ids = set()
                for a, why in to_reject:
                    self._mark(a, Status.REJECT, why)
                    reject_ids.add(a.asset_id)
                rep.marked_reject = len(reject_ids)
                review_ids = set()
                for a, why in to_review:
                    if a.asset_id in reject_ids:
                        continue
                    self._mark(a, Status.REVIEW, why)
                    review_ids.add(a.asset_id)
                rep.marked_review = len(review_ids)
            finally:
                self.catalog.flush()
                self.catalog._autoflush = prev

        return rep

    def _balance(self, mixtures: list) -> tuple:
        """A dimension is imbalanced if its busiest populated bucket outweighs its
        emptiest by more than IMBALANCE_RATIO (unknowns excluded). ALL dims are
        reported; only BALANCE_GATE_DIMS (studio-controlled) decide the pass/fail."""
        imbalance = {}
        for name, fn in DIMENSIONS.items():
            hist = Counter(fn(a) for a in mixtures)
            hist.pop("unknown", None)
            hist.pop("none", None)
            vals = [c for c in hist.values() if c > 0]
            if len(vals) < 2:
                continue
            ratio = max(vals) / min(vals)
            if ratio > IMBALANCE_RATIO:
                imbalance[name] = round(ratio, 1)
        balanced = not any(d in imbalance for d in BALANCE_GATE_DIMS)
        return (balanced, imbalance)


def validate_manifest(manifest_path: str | Path) -> dict:
    """Independently re-verify a written manifest: sha256 recompute + license gate."""
    from .. import training_data
    man = json.loads(Path(manifest_path).read_text())
    stored = man.get("manifest_sha256")
    recomputed = None
    if stored is not None:
        body = {k: v for k, v in man.items() if k != "manifest_sha256"}
        recomputed = hashlib.sha256(
            json.dumps(body, indent=1, sort_keys=True).encode()).hexdigest()
    lic_ok, lic_err = True, None
    try:
        if man.get("intended_use") == "commercial_eligible":
            for ds, facts in man.get("dataset_lineage", {}).items():
                if not facts.get("commercial_training_allowed"):
                    lic_ok, lic_err = False, f"{ds} not commercial"
                    break
    except Exception as e:  # noqa: BLE001
        lic_ok, lic_err = False, str(e)
    return {"n_tracks": man.get("n_tracks", 0),
            "sha_ok": (stored is not None and stored == recomputed),
            "license_ok": lic_ok, "license_error": lic_err,
            "intended_use": man.get("intended_use")}
