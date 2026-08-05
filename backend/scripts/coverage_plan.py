#!/usr/bin/env python3
"""Run the Coverage Planner over the permanent Stem Pool index -> the 9 reports."""
import argparse, json, sys, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lab.factory import AssetCatalog, planner  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--catalog", required=True)
ap.add_argument("--prev-coverage", default="")
ap.add_argument("--out", default="")
ap.add_argument("--benchmark-failures", default="", help="json path: {dim:{value:rate}}")
a = ap.parse_args()

catalog = AssetCatalog(path=a.catalog)
prev = json.load(open(a.prev_coverage)) if a.prev_coverage and Path(a.prev_coverage).exists() else None
bench = json.load(open(a.benchmark_failures)) if a.benchmark_failures and Path(a.benchmark_failures).exists() else None
p = planner.plan(catalog, benchmark_failures=bench)
report = planner.full_report(p, catalog, prev)
if a.out:
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(report)
print(report)
