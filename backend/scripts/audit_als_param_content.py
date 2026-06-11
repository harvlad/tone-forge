#!/usr/bin/env python3
"""Pre-render structural audit: every ALS must contain a non-trivial
instrument-device parameter tree.

This catches the regression behind ``RENDER_PIPELINE_RCA.md`` — empty
device bodies that nominally point at a `.adv` via ``<LastPresetRef>``
but contain zero parameter children, causing Ableton to render the
default patch instead of the referenced preset.

The audit is multi-instrument: it discovers each ALS's device tag at
runtime and looks up the appropriate ``DeviceConfig`` thresholds from
``tone_forge.preset_catalog.preset_als_generator``. ALS files for any
instrument with a registered DEVICE_CONFIG entry are auditable.

Run between Step 1 (generate) and Step 2 (operator Ableton render) of the
Preset Rendering Pipeline v2 runbook. Exit non-zero on any failure so
``build_preset_catalog.py generate`` can abort cleanly before any
Ableton time is spent.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Pull the DEVICE_CONFIG mapping in from the generator so the audit is
# guaranteed to use the same thresholds the generator validated against.
# This is a runtime-only dependency; the script's working directory is
# the project root (set by build_preset_catalog.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tone_forge.preset_catalog.preset_als_generator import (  # noqa: E402
    DEVICE_CONFIG,
    DeviceConfig,
)


def _count_direct_children(body: str) -> List[str]:
    """Return the names of direct (depth-1) children of a device body."""
    depth = 0
    names: List[str] = []
    for tok in re.finditer(r"<(/?)([A-Za-z_][\w.-]*)\b[^>]*?(/?)>", body):
        is_close = bool(tok.group(1))
        is_self_close = bool(tok.group(3))
        name = tok.group(2)
        if is_close:
            depth -= 1
        else:
            if depth == 0:
                names.append(name)
            if not is_self_close:
                depth += 1
    return names


def _detect_device(text: str) -> Optional[DeviceConfig]:
    """Find which registered device tag is present in an ALS.

    Iterates the known DEVICE_CONFIG entries and returns the first whose
    ``tag_name`` appears as a properly bounded XML opening tag. Boundary
    is ``[\\s/>]`` so list-suffixed children (e.g. ``<Operator.0>``)
    don't masquerade as the device tag for ``Operator``.
    """
    for cfg in DEVICE_CONFIG.values():
        if re.search(rf"<{cfg.tag_name}(?=[\s/>])", text):
            return cfg
    return None


def audit_als(path: Path) -> Tuple[bool, Dict]:
    """Audit a single ALS file. Returns (passed, detail dict)."""
    detail: Dict = {"path": str(path)}
    try:
        raw = gzip.decompress(path.read_bytes())
    except Exception as exc:
        detail["error"] = f"gunzip failed: {exc}"
        return False, detail

    text = raw.decode("utf-8", errors="replace")
    config = _detect_device(text)
    if config is None:
        known = sorted(c.tag_name for c in DEVICE_CONFIG.values())
        detail["error"] = (
            f"no known device tag found (expected one of {known})"
        )
        return False, detail

    detail["instrument"] = config.instrument
    detail["device_tag"] = config.tag_name

    tag = config.tag_name
    boundary = r"(?=[\s/>])"
    match = re.search(rf"<{tag}{boundary}[\s\S]*?</{tag}>", text)
    if not match:
        detail["error"] = f"no <{tag}> element found"
        return False, detail
    block = match.group(0)
    body_match = re.search(rf"<{tag}{boundary}([^>]*)>([\s\S]*)</{tag}>", block)
    if not body_match:
        detail["error"] = f"could not parse <{tag}> body"
        return False, detail

    opening_attrs = body_match.group(1)
    body = body_match.group(2)

    # Opening tag must carry Id (Devices list-member rule).
    has_opening_id = bool(re.search(r"\bId\s*=", opening_attrs))
    children = _count_direct_children(body)
    missing = [
        name
        for name in config.required_children
        if not re.search(rf"<{name}\b", body)
    ]
    # List-suffixed members (<Foo.0>, <Foo.1>, ...) must each carry an Id
    # attribute or Ableton rejects the ALS at parse time.
    list_missing: List[str] = []
    for m in re.finditer(r"<([A-Za-z_][\w]*\.\d+)\b([^>]*?)(/?)>", body):
        if not re.search(r"\bId\s*=", m.group(2)):
            list_missing.append(m.group(1))

    detail["block_bytes"] = len(block)
    detail["direct_children_count"] = len(children)
    detail["missing_required"] = missing
    detail["list_members_missing_id"] = sorted(set(list_missing))
    detail["opening_tag_has_id"] = has_opening_id

    passed = (
        has_opening_id
        and len(children) >= config.min_children
        and not missing
        and not list_missing
    )
    if not passed:
        if not has_opening_id:
            detail["error"] = (
                f"<{tag}> opening tag missing Id "
                "(required as <Devices> list member)"
            )
        elif len(children) < config.min_children:
            detail["error"] = (
                f"only {len(children)} direct children "
                f"(< {config.min_children})"
            )
        elif missing:
            detail["error"] = f"missing required children: {missing}"
        elif list_missing:
            detail["error"] = (
                f"{len(list_missing)} list members missing Id "
                f"(examples: {sorted(set(list_missing))[:5]})"
            )
    return passed, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--als-dir",
        type=Path,
        default=Path("preset_catalog_output/als"),
        help="directory containing generated *.als files",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="optional JSON output path (default: <als-dir>/../retrieval/als_param_audit.json)",
    )
    args = parser.parse_args()

    if not args.als_dir.is_dir():
        print(f"ERROR: not a directory: {args.als_dir}", file=sys.stderr)
        return 2

    files = sorted(args.als_dir.glob("*.als"))
    if not files:
        print(f"ERROR: no .als files in {args.als_dir}", file=sys.stderr)
        return 2

    results: List[Dict] = []
    n_pass = 0
    n_fail = 0
    children_dist: Counter = Counter()
    instrument_counts: Counter = Counter()

    for path in files:
        passed, detail = audit_als(path)
        detail["passed"] = passed
        results.append(detail)
        if passed:
            n_pass += 1
            children_dist[detail["direct_children_count"]] += 1
            instrument_counts[detail.get("instrument", "?")] += 1
        else:
            n_fail += 1
            print(
                f"  FAIL  {path.name}: {detail.get('error', 'unknown')}",
                file=sys.stderr,
            )

    # Summary
    print(f"Audited {len(files)} ALS files in {args.als_dir}")
    print(f"  PASS: {n_pass}")
    print(f"  FAIL: {n_fail}")
    if instrument_counts:
        by_inst = ", ".join(
            f"{k}:{v}" for k, v in sorted(instrument_counts.items())
        )
        print(f"  by instrument (passing): {by_inst}")
    if children_dist:
        top = ", ".join(f"{k}:{v}" for k, v in children_dist.most_common(5))
        print(f"  direct-children distribution (passing files): {top}")

    report_path = args.report or (
        args.als_dir.parent / "retrieval" / "als_param_audit.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "als_dir": str(args.als_dir),
                "total": len(files),
                "pass": n_pass,
                "fail": n_fail,
                # Audit thresholds are per-instrument; dump the whole
                # DEVICE_CONFIG so downstream reports record exactly
                # which schema each ALS was checked against.
                "device_configs": {
                    cfg.instrument: {
                        "tag_name": cfg.tag_name,
                        "min_children": cfg.min_children,
                        "required_children": list(cfg.required_children),
                    }
                    for cfg in DEVICE_CONFIG.values()
                },
                "instrument_counts": dict(instrument_counts),
                "results": results,
            },
            indent=2,
        )
    )
    print(f"  Report -> {report_path}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
