#!/usr/bin/env python3
"""Dump the Beat Capture correction corpus to CSV for the CoreML trainer.

Reads every stored correction (R2 when configured, else the local
``backend/data/beat_corrections`` dir) and writes a CSV whose columns
match the trainer's ``--corrections`` contract: the canonical feature
names in order, then ``original`` and ``corrected`` role labels.

Usage:
    python scripts/export_beat_corrections.py --out corrections.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge import beat_corpus  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="beat_corrections.csv",
        help="output CSV path (default: beat_corrections.csv)",
    )
    args = parser.parse_args()

    rows = beat_corpus.read_all()
    header = list(beat_corpus.FEATURE_NAMES) + ["original", "corrected"]

    out_path = Path(args.out)
    written = 0
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for row in rows:
            features = row.get("features", {})
            try:
                feats = [features[name] for name in beat_corpus.FEATURE_NAMES]
            except KeyError:
                continue  # skip malformed rows
            writer.writerow(feats + [row.get("original"), row.get("corrected")])
            written += 1

    print(f"Wrote {written} correction(s) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
