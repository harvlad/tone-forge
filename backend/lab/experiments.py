"""Experiment registry — research memory, not just logs.

Append-only JSONL.  Every benchmark/analysis run records enough to
reconstruct HOW a number was produced six months later.
"""
from __future__ import annotations

import datetime
import json
import subprocess
import uuid
from typing import List, Optional

from . import config


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              cwd=config.REPO_ROOT, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


def new_experiment_id(prefix: str = "exp") -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:6]}"


def record(kind: str, *, models: List[str], dataset: str = "", split: str = "",
           tier: str = "", inst_class: Optional[str] = None,
           n_stems: int = 0, eval_config: Optional[dict] = None,
           result_summary: Optional[dict] = None, status: str = "complete",
           runtime_seconds: Optional[float] = None, device: str = "local",
           cost: Optional[dict] = None, notes: str = "", decision: str = "",
           artifacts: Optional[List[str]] = None,
           provenance: str = "lab", experiment_id: Optional[str] = None) -> str:
    """Append an experiment record; returns its ID."""
    config.EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    exp_id = experiment_id or new_experiment_id()
    rec = {
        "experiment_id": exp_id,
        "kind": kind,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "models": models,
        "dataset": dataset, "split": split, "tier": tier, "inst_class": inst_class,
        "n_stems": n_stems,
        "eval_config": eval_config or {},
        "result_summary": result_summary or {},
        "status": status,
        "runtime_seconds": runtime_seconds,
        "device": device,
        "cost": cost,
        "notes": notes,
        "decision": decision,
        "artifacts": artifacts or [],
        "provenance": provenance,
    }
    with open(config.EXPERIMENTS_LOG, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return exp_id


def load_all() -> List[dict]:
    if not config.EXPERIMENTS_LOG.exists():
        return []
    out = []
    for line in config.EXPERIMENTS_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
