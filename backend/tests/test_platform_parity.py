"""Platform-parity doctrine enforcement (see /PARITY.yaml).

Every user-facing feature carries an explicit per-platform status in
the root PARITY.yaml. This test keeps that matrix honest:

* schema: known platforms, known statuses, one entry per platform;
* ``done``/``partial`` must cite evidence as ``path#symbol`` — the
  file must exist and contain the symbol, so renames/deletions break
  CI instead of silently rotting the matrix into fiction;
* ``missing``/``na`` must carry a note — an unexplained "na" is a gap
  hiding behind a label.

The matrix cannot prove a feature WORKS — it proves the claim points
at real code and that every platform decision was made consciously.
Behavioral truth stays with each platform's own tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is a dev dependency")

REPO_ROOT = Path(__file__).resolve().parents[2]
PARITY_FILE = REPO_ROOT / "PARITY.yaml"

PLATFORMS = {"web", "ios", "desktop", "plugin", "connect"}
STATUSES = {"done", "partial", "missing", "na"}


def _load() -> list[dict]:
    assert PARITY_FILE.is_file(), f"missing {PARITY_FILE}"
    data = yaml.safe_load(PARITY_FILE.read_text(encoding="utf-8"))
    features = data.get("features")
    assert isinstance(features, list) and features, "PARITY.yaml: no features"
    return features


def test_schema() -> None:
    seen_ids: set[str] = set()
    for feat in _load():
        fid = feat.get("id")
        assert isinstance(fid, str) and fid, f"feature without id: {feat}"
        assert fid not in seen_ids, f"duplicate feature id {fid}"
        seen_ids.add(fid)
        assert feat.get("name"), f"{fid}: missing name"
        platforms = feat.get("platforms") or {}
        assert set(platforms) == PLATFORMS, (
            f"{fid}: platforms must be exactly {sorted(PLATFORMS)}, "
            f"got {sorted(platforms)} — every cell is a decision"
        )
        for plat, cell in platforms.items():
            status = (cell or {}).get("status")
            assert status in STATUSES, f"{fid}/{plat}: bad status {status!r}"
            if status in ("missing", "na"):
                assert (cell.get("note") or "").strip(), (
                    f"{fid}/{plat}: {status} requires a note explaining why"
                )
            if status == "partial":
                assert (cell.get("note") or "").strip(), (
                    f"{fid}/{plat}: partial requires a note (what's missing)"
                )


def test_evidence_anchors_exist() -> None:
    """done/partial evidence must point at real code that still exists."""
    problems: list[str] = []
    for feat in _load():
        fid = feat["id"]
        for plat, cell in (feat.get("platforms") or {}).items():
            if (cell or {}).get("status") not in ("done", "partial"):
                continue
            evidence = (cell or {}).get("evidence") or ""
            if "#" not in evidence:
                problems.append(f"{fid}/{plat}: evidence must be 'path#symbol', got {evidence!r}")
                continue
            rel, symbol = evidence.split("#", 1)
            if not symbol.strip():
                problems.append(f"{fid}/{plat}: empty symbol in {evidence!r}")
                continue
            path = REPO_ROOT / rel
            if not path.is_file():
                problems.append(f"{fid}/{plat}: evidence file missing: {rel}")
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                problems.append(f"{fid}/{plat}: cannot read {rel}: {exc}")
                continue
            if symbol not in text:
                problems.append(
                    f"{fid}/{plat}: symbol {symbol!r} not found in {rel} "
                    "(renamed or removed? update PARITY.yaml)"
                )
    assert not problems, "PARITY.yaml evidence rot:\n" + "\n".join(problems)
