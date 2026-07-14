"""Beat Capture (D-024) correction corpus.

Append-only store of user drum-role corrections uploaded from the iOS +
desktop apps. Each row is 7 analysis features (no audio) plus the model's
original guess, the user's correction, and stamping metadata. The corpus
feeds the off-device CoreML trainer (``scripts/export_beat_corrections``).

Storage is batch-per-object so concurrent uploads never race on a shared
file: every ``append`` writes one immutable JSONL object. R2 when
configured (prefix ``beat-corrections/``), else a local directory under
``backend/data/beat_corrections/``.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional

from . import r2_storage

# Canonical feature order — must match Swift `OnsetFeatures.featureNames`.
FEATURE_NAMES = (
    "centroidHz",
    "zcr",
    "attackSec",
    "durationSec",
    "pitchedness",
    "lowBandRatio",
    "peakRMS",
)

# Valid drum-role labels — must match Swift `DrumRole.rawValue`.
ROLE_LABELS = (
    "kick",
    "snare",
    "closed_hat",
    "open_hat",
    "clap",
    "rim",
    "perc",
)

_R2_PREFIX = "beat-corrections/"
_LOCAL_DIR = Path(__file__).parent.parent / "data" / "beat_corrections"


class CorrectionSchemaError(ValueError):
    """A submitted correction row failed schema validation."""


def validate_row(row: dict) -> dict:
    """Return a normalized correction row or raise CorrectionSchemaError.

    Enforces: every canonical feature present and finite, `original` and
    `corrected` are known role labels, `ts` a string. Unknown extra keys
    are dropped.
    """
    if not isinstance(row, dict):
        raise CorrectionSchemaError("row must be an object")

    features = row.get("features")
    if not isinstance(features, dict):
        raise CorrectionSchemaError("features must be an object")

    clean_features: dict[str, float] = {}
    for name in FEATURE_NAMES:
        if name not in features:
            raise CorrectionSchemaError(f"missing feature: {name}")
        try:
            value = float(features[name])
        except (TypeError, ValueError):
            raise CorrectionSchemaError(f"feature {name} is not numeric")
        if value != value or value in (float("inf"), float("-inf")):
            raise CorrectionSchemaError(f"feature {name} is not finite")
        clean_features[name] = value

    original = row.get("original")
    corrected = row.get("corrected")
    if original not in ROLE_LABELS:
        raise CorrectionSchemaError(f"unknown original role: {original!r}")
    if corrected not in ROLE_LABELS:
        raise CorrectionSchemaError(f"unknown corrected role: {corrected!r}")

    ts = row.get("ts")
    if not isinstance(ts, str) or not ts:
        raise CorrectionSchemaError("ts must be a non-empty string")

    return {
        "features": clean_features,
        "original": original,
        "corrected": corrected,
        "ts": ts,
    }


def append(
    rows: Iterable[dict],
    device_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> int:
    """Persist a validated batch of corrections. Returns rows written.

    Each row is stamped with `device_id`/`owner_id` and a server receive
    time, then the whole batch is written as one immutable JSONL object.
    Raises CorrectionSchemaError if any row is invalid (all-or-nothing).
    """
    received_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stamped: list[dict] = []
    for row in rows:
        clean = validate_row(row)
        if device_id:
            clean["device_id"] = device_id
        if owner_id:
            clean["owner_id"] = owner_id
        clean["received_at"] = received_at
        stamped.append(clean)

    if not stamped:
        return 0

    body = "\n".join(json.dumps(r, default=str) for r in stamped) + "\n"
    batch_id = f"{received_at.replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}"

    if r2_storage.is_configured():
        key = f"{_R2_PREFIX}{batch_id}.jsonl"
        try:
            r2_storage._client().put_object(
                Bucket=r2_storage.bucket_name(),
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/x-ndjson",
            )
            return len(stamped)
        except Exception as exc:  # noqa: BLE001 — fall back to local
            print(f"[beat_corpus] R2 put failed for {key}: {exc}")

    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    (_LOCAL_DIR / f"{batch_id}.jsonl").write_text(body, encoding="utf-8")
    return len(stamped)


def read_all() -> list[dict]:
    """Return every stored correction row (order unspecified).

    Concatenates all batch objects — R2 when configured, else the local
    directory. Used by the trainer export.
    """
    rows: list[dict] = []

    if r2_storage.is_configured():
        try:
            client = r2_storage._client()
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=r2_storage.bucket_name(), Prefix=_R2_PREFIX
            ):
                for obj in page.get("Contents", []):
                    resp = client.get_object(
                        Bucket=r2_storage.bucket_name(), Key=obj["Key"]
                    )
                    text = resp["Body"].read().decode("utf-8")
                    rows.extend(_parse_jsonl(text))
            return rows
        except Exception as exc:  # noqa: BLE001 — fall back to local
            print(f"[beat_corpus] R2 read_all failed: {exc}")

    if _LOCAL_DIR.is_dir():
        for path in sorted(_LOCAL_DIR.glob("*.jsonl")):
            rows.extend(_parse_jsonl(path.read_text(encoding="utf-8")))
    return rows


def _parse_jsonl(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
