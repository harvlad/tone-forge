"""Backfill performance graphs for EXISTING songs under the current builder.

Why: graphs are derived once at analysis time and cached on the entry, so
quality-pipeline improvements (the best-version-per-pad guarantee: stereo-aware
loader, collapse_ratio/parent_overlap signals, the parent-vs-children duel)
only reach songs analyzed after the change. Old entries persisted BOTH the raw
parent stem and the pan-split children in stems_paths, so their graphs can be
re-derived on the serving box from R2 stems — CPU only, no GPU re-analysis.

Run ON THE VPS (needs the R2 env):
    cd /opt/toneforge/backend
    set -a; . /opt/toneforge/.env; set +a
    nice -n 19 /opt/toneforge/venv/bin/python scripts/backfill_performance_graphs.py [--dry-run] [--only ENTRY_ID]

Safety:
  - Re-loads history and patches ONE entry per save, so the race window
    against the live app's own history writes is a single entry, not the
    whole run. Only the performance_graph key is touched.
  - An entry whose re-derive fails (missing stems, empty graph) is left
    exactly as it was — fail-open, logged.
  - module_version/config gate: entries whose stored graph already carries
    the current builder config hash are skipped (idempotent re-runs).
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_history():
    from tone_forge import r2_storage

    if r2_storage.is_configured():
        h = r2_storage.load_history()
        if h is not None:
            return h, "r2"
    p = Path(__file__).resolve().parent.parent / "data" / "history.json"
    if p.exists():
        import json

        return json.loads(p.read_text()), "local"
    return [], "none"


def _save_entry(entry_id: str, graph_dict: dict) -> bool:
    """Re-load history fresh, patch one entry's graph, save via the same
    R2+local path the app uses."""
    import json

    from tone_forge import r2_storage

    history, src = _load_history()
    hit = False
    for e in history:
        if str(e.get("id")) == entry_id and isinstance(e.get("result"), dict):
            e["result"]["performance_graph"] = graph_dict
            hit = True
            break
    if not hit:
        return False
    local = Path(__file__).resolve().parent.parent / "data" / "history.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps(history, indent=2, default=str))
    if src == "r2":
        r2_storage.save_history(history)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="single entry id")
    args = ap.parse_args()

    from tone_forge.performance.builder import _CONFIG, PerformanceBuilder
    from tone_forge.performance.serve import _content_hash
    from tone_forge.stem_fetch import materialize_stems

    try:
        from lab.hashing import config_hash  # type: ignore
    except Exception:
        from tone_forge.performance.graph import config_hash  # type: ignore

    current_cfg = config_hash(_CONFIG)
    history, src = _load_history()
    print(f"history: {len(history)} entries from {src}; "
          f"builder cfg={current_cfg[:12]}")

    builder = PerformanceBuilder()
    done = skipped = failed = 0
    for e in history:
        eid = str(e.get("id") or "")
        result = e.get("result")
        if not eid or not isinstance(result, dict):
            continue
        if args.only and eid != args.only:
            continue
        stored = result.get("performance_graph")
        if isinstance(stored, dict) and stored.get("config_hash") == current_cfg:
            skipped += 1
            continue
        name = str(e.get("name") or eid)[:40]
        print(f"-> {eid} ({name}) ...", flush=True)
        if args.dry_run:
            done += 1
            continue
        t0 = time.time()
        try:
            with tempfile.TemporaryDirectory(prefix="tf_backfill_") as td:
                stems = materialize_stems(result, Path(td))
                if not stems:
                    print("   no stems materialized — left untouched")
                    failed += 1
                    continue
                shadow = dict(result)
                shadow["stems_local"] = {k: str(v) for k, v in stems.items()}
                g = builder.build(
                    result=shadow, song_id=eid,
                    content_hash=_content_hash(eid, result),
                    use_cache=False,
                )
            if not g.assets:
                print("   empty graph — left untouched")
                failed += 1
                continue
            if _save_entry(eid, g.to_dict()):
                done += 1
                print(f"   ok: {len(g.assets)} assets, "
                      f"{len(g.phrases)} phrases in {time.time()-t0:.0f}s")
            else:
                failed += 1
                print("   save miss (entry vanished mid-run?)")
        except Exception as exc:  # noqa: BLE001 — one bad entry never stops the run
            failed += 1
            print(f"   FAILED: {exc}")
    print(f"backfill: {done} rebuilt, {skipped} current, {failed} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
