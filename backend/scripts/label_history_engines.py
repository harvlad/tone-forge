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


if __name__ == "__main__":
    main()
