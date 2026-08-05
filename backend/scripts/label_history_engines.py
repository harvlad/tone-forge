#!/usr/bin/env python3
"""One-off migration: append an engine tag to every history entry name.

" · exp" for analysis_engine == experimental_specialist, " · std"
otherwise (entries predating the field are current-engine by
definition). Idempotent — already-tagged names are skipped.

Run on the host that owns the history file, with the API stopped or
accepting that the next write wins the R2 race:
    python3 scripts/label_history_engines.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HISTORY = Path(__file__).resolve().parent.parent / "data" / "history.json"
TAGS = (" · exp", " · std")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # R2 is the authoritative store when configured — the service loads
    # from it at startup and would clobber a file-only edit. Source from
    # R2 first, fall back to the local file.
    import sys as _sys
    _sys.path.insert(0, str(HISTORY.parent.parent))
    r2_items = None
    try:
        from tone_forge import r2_storage
        if r2_storage.is_configured():
            r2_items = r2_storage.load_history()
    except Exception as e:  # noqa: BLE001
        print(f"R2 load failed: {e}")
    if r2_items is not None:
        print(f"source: R2 ({len(r2_items)} entries)")
        raw = r2_items
        items = r2_items
    else:
        print("source: local file")
        raw = json.loads(HISTORY.read_text())
        items = raw if isinstance(raw, list) else raw.get("history", [])
    changed = 0
    for it in items:
        name = it.get("name") or ""
        if any(name.endswith(t) for t in TAGS):
            continue
        engine = ((it.get("result") or {}).get("analysis_engine")) or "current"
        tag = " · exp" if engine == "experimental_specialist" else " · std"
        it["name"] = f"{name}{tag}"
        changed += 1
        print(f"  {it.get('id','?')[:12]}  {it['name']}")
    print(f"{changed} entries tagged ({len(items)} total)")
    if changed and not args.dry_run:
        HISTORY.write_text(json.dumps(raw, indent=2))
        print(f"written: {HISTORY}")
        try:
            from tone_forge import r2_storage
            if r2_storage.is_configured() and r2_storage.save_history(items):
                print("pushed to R2")
            else:
                print("R2 not configured/push failed — local file only")
        except Exception as e:  # noqa: BLE001
            print(f"R2 push failed: {e}")


if __name__ == "__main__":
    main()
