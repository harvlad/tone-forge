"""Empirically discover Ableton instrument-device schemas from `.adv` files.

For each requested instrument, this script:

  1. Locates sample ``.adv`` files in the Ableton Core Library.
  2. gunzip-decompresses each and parses the XML.
  3. Identifies the single device element inside ``<Ableton>``.
  4. Aggregates direct-child element names + counts across the samples.
  5. Reports a suggested ``DeviceConfig`` entry suitable for pasting into
     ``tone_forge.preset_catalog.preset_als_generator.DEVICE_CONFIG``.

This exists because populating ``DEVICE_CONFIG`` by guessing tag names is
the same failure mode that produced the original catalog collapse: a
plausible-looking but wrong validator passes empty ALS files. The
solution is to derive the tag names + required-children list from the
actual ``.adv`` files the system will be embedding.

Usage:
    python scripts/discover_device_schema.py                # all known
    python scripts/discover_device_schema.py Wavetable      # one
    python scripts/discover_device_schema.py --json         # JSON only
    python scripts/discover_device_schema.py --samples 5    # 5 per inst.

Exit codes:
  0 — every requested instrument had a valid sample discovered.
  1 — at least one instrument could not be sampled (no .adv found, gzip
      failure, XML parse failure, or no device element inside <Ableton>).
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET

# Re-use the existing installation-discovery logic so we agree with
# preset_discovery.py on where Core Library lives.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tone_forge.preset_catalog.preset_discovery import PresetDiscovery  # noqa: E402

logger = logging.getLogger("discover_device_schema")

# Instruments we want first-class catalog support for. Drift / Meld /
# Analog / Operator / Wavetable were already in SUPPORTED_INSTRUMENTS;
# Electric / Tension / Collision are the additions Phase 2 introduces.
DEFAULT_INSTRUMENTS = [
    "Analog",
    "Wavetable",
    "Operator",
    "Drift",
    "Meld",
    "Electric",
    "Tension",
    "Collision",
]


@dataclass
class DiscoveryResult:
    instrument: str
    samples_examined: int = 0
    tag_name: Optional[str] = None
    # Per-sample direct-child counts. Used to derive a median for
    # min_children that won't fail on slightly stripped-down presets.
    child_counts: List[int] = field(default_factory=list)
    # Aggregate child-name frequencies across all samples. A child name
    # is a strong "required" candidate if it appears in every sample.
    child_frequencies: Counter = field(default_factory=Counter)
    sample_paths: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.samples_examined > 0

    def suggest_required_children(self, top_n: int = 5) -> List[str]:
        """Pick child elements that appear in *every* sample.

        We restrict to children that appear in 100% of samples (universal
        children), then return the top-N most frequent of those. This is
        the same conservative approach we already use for Analog.
        """
        if not self.child_frequencies:
            return []
        universal = [
            name
            for name, count in self.child_frequencies.items()
            if count >= self.samples_examined
        ]
        # Sort by frequency desc, then alphabetically for determinism.
        universal.sort(key=lambda n: (-self.child_frequencies[n], n))
        return universal[:top_n]

    def suggest_min_children(self) -> int:
        """Conservatively pick min_children = floor(median * 0.6).

        The original Analog config used 30 against ~60 empirical, i.e.
        roughly half. Using 0.6 of the median tracks that ratio for
        any device while still allowing for legitimate stripped presets.
        """
        if not self.child_counts:
            return 0
        sorted_counts = sorted(self.child_counts)
        mid = len(sorted_counts) // 2
        if len(sorted_counts) % 2:
            median = sorted_counts[mid]
        else:
            median = (sorted_counts[mid - 1] + sorted_counts[mid]) / 2
        return max(1, int(median * 0.6))


def _find_instrument_dir(core_library: Path, instrument: str) -> Optional[Path]:
    p = core_library / "Devices" / "Instruments" / instrument
    return p if p.exists() else None


def _pick_sample_paths(instrument_dir: Path, n: int) -> List[Path]:
    """Pick up to n .adv files, biased toward variety (different categories).

    Ableton's Core Library lays out presets as
    ``<Instrument>/<Category>/<Preset>.adv``. To avoid sampling N
    presets from the same Bass folder (which might share an aberrant
    template), we round-robin one preset per category until we have n.
    """
    by_category: Dict[str, List[Path]] = {}
    for path in instrument_dir.rglob("*.adv"):
        if "Ableton Folder Info" in str(path):
            continue
        category = path.parent.name
        by_category.setdefault(category, []).append(path)

    # Round-robin across categories.
    selected: List[Path] = []
    indices = {cat: 0 for cat in by_category}
    while len(selected) < n and any(
        indices[c] < len(by_category[c]) for c in by_category
    ):
        for cat in sorted(by_category):
            if indices[cat] < len(by_category[cat]):
                selected.append(by_category[cat][indices[cat]])
                indices[cat] += 1
                if len(selected) >= n:
                    break
    return selected


def _parse_adv(path: Path) -> ET.Element:
    """gunzip + parse XML, returning the <Ableton> root element."""
    with open(path, "rb") as f:
        compressed = f.read()
    raw = gzip.decompress(compressed)
    root = ET.fromstring(raw)
    if root.tag != "Ableton":
        raise ValueError(
            f"{path}: root element is <{root.tag}>, expected <Ableton>"
        )
    return root


def _find_device_element(root: ET.Element) -> Optional[ET.Element]:
    """Return the single device-instance child of <Ableton>.

    `.adv` files contain exactly one device element directly under
    ``<Ableton>``. There is also typically a ``<LomVersion ...>`` /
    similar metadata sibling; we skip any element whose name looks like
    metadata (lowercase first letter is rare in Ableton, but the only
    truly safe filter is "the one with substantial children").
    """
    candidates = [
        child for child in root
        # Exclude trivial metadata wrappers.
        if len(list(child)) > 0
    ]
    if not candidates:
        return None
    # Pick the candidate with the most direct children — that's the
    # actual device payload. (Metadata wrappers typically have <5.)
    candidates.sort(key=lambda c: len(list(c)), reverse=True)
    return candidates[0]


def discover_instrument(
    instrument: str,
    core_library: Path,
    samples: int,
) -> DiscoveryResult:
    result = DiscoveryResult(instrument=instrument)

    inst_dir = _find_instrument_dir(core_library, instrument)
    if inst_dir is None:
        result.error = f"No Core Library folder for {instrument!r}"
        return result

    sample_paths = _pick_sample_paths(inst_dir, samples)
    if not sample_paths:
        result.error = f"No .adv files found under {inst_dir}"
        return result

    tags_seen: Counter = Counter()
    for path in sample_paths:
        try:
            root = _parse_adv(path)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", path, exc)
            continue
        device = _find_device_element(root)
        if device is None:
            logger.warning("No device element in %s", path)
            continue
        tags_seen[device.tag] += 1
        children = list(device)
        result.child_counts.append(len(children))
        for child in children:
            result.child_frequencies[child.tag] += 1
        result.sample_paths.append(str(path))
        result.samples_examined += 1

    if not tags_seen:
        result.error = "No usable .adv samples (all parses failed)"
        return result

    # Pick the most common tag name. If there's disagreement, surface it.
    most_common_tag, most_common_count = tags_seen.most_common(1)[0]
    result.tag_name = most_common_tag
    if len(tags_seen) > 1:
        logger.warning(
            "Inconsistent device tag for %s: %s (using %r)",
            instrument,
            dict(tags_seen),
            most_common_tag,
        )
    return result


def format_config_suggestion(result: DiscoveryResult) -> str:
    """Render a DEVICE_CONFIG dict entry ready to paste."""
    if not result.ok:
        return f'    # {result.instrument}: SKIPPED — {result.error}'
    required = result.suggest_required_children(top_n=5)
    min_children = result.suggest_min_children()
    median_label = (
        f"median={sorted(result.child_counts)[len(result.child_counts) // 2]}"
        if result.child_counts
        else "median=?"
    )
    lines = [
        f'    "{result.instrument}": DeviceConfig(',
        f'        instrument="{result.instrument}",',
        f'        tag_name="{result.tag_name}",',
        f"        # Empirical: {median_label} direct children across "
        f"{result.samples_examined} sample(s); 0.6x rounded.",
        f"        min_children={min_children},",
        f"        # Universal direct children in every sampled .adv:",
        "        required_children=(",
    ]
    for child in required:
        freq = result.child_frequencies[child]
        lines.append(f'            "{child}",  # in {freq}/'
                     f'{result.samples_examined} samples')
    lines.append("        ),")
    lines.append("    ),")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "instruments",
        nargs="*",
        help="Instrument names to discover. Default: all known.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=3,
        help="How many .adv samples to inspect per instrument (default: 3)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a single JSON document instead of human-readable output.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    discovery = PresetDiscovery()
    if not discovery.find_ableton_installation():
        print("ERROR: No Ableton installation found.", file=sys.stderr)
        return 1
    core_library = discovery._core_library
    assert core_library is not None

    instruments = args.instruments or DEFAULT_INSTRUMENTS
    results = [
        discover_instrument(inst, core_library, args.samples)
        for inst in instruments
    ]

    if args.json:
        payload = {
            r.instrument: {
                "ok": r.ok,
                "error": r.error,
                "tag_name": r.tag_name,
                "samples_examined": r.samples_examined,
                "child_counts": r.child_counts,
                "suggested_min_children": r.suggest_min_children(),
                "suggested_required_children": r.suggest_required_children(),
                "sample_paths": r.sample_paths,
                "child_frequencies_top20": dict(
                    r.child_frequencies.most_common(20)
                ),
            }
            for r in results
        }
        print(json.dumps(payload, indent=2))
    else:
        for r in results:
            print(f"\n=== {r.instrument} ===")
            if not r.ok:
                print(f"  SKIPPED: {r.error}")
                continue
            print(f"  device tag:        {r.tag_name}")
            print(f"  samples examined:  {r.samples_examined}")
            print(f"  child counts:      {r.child_counts}")
            print(f"  top universal children: "
                  f"{r.suggest_required_children()}")
            print(f"  suggested min_children: {r.suggest_min_children()}")
            print("\n  suggested DEVICE_CONFIG entry:\n")
            print(format_config_suggestion(r))

    exit_code = 0 if all(r.ok for r in results) else 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
