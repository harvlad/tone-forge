"""Command-line entry point.

Usage:
    python cli.py path/to/clip.wav --hardware helix [--json]

End-to-end exercise of the pipeline:
    audio file -> analyzer -> ToneDescriptor -> translator -> ChainCard -> stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from tone_forge import analyzer, helix_translator


def main() -> int:
    p = argparse.ArgumentParser(description="Tone Forge — recreate guitar tones on your hardware.")
    p.add_argument("audio_path", help="Path to a WAV/MP3/FLAC clip.")
    p.add_argument("--hardware", choices=["helix"], default="helix",
                   help="Target hardware (only Helix in V1).")
    p.add_argument("--source-kind", choices=["isolated_guitar", "stem_separated", "full_mix"],
                   default="isolated_guitar",
                   help="What kind of audio the file contains.")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a human-readable card.")
    args = p.parse_args()

    descriptor = analyzer.analyze(args.audio_path, source_kind=args.source_kind)

    if args.hardware == "helix":
        card = helix_translator.translate(descriptor)
    else:  # never reached today; placeholder for future translators
        print(f"Hardware not yet supported: {args.hardware}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "descriptor": descriptor.to_dict(),
            "chain": [asdict(pick) for pick in card.picks],
            "tweak_hints": card.tweak_hints,
        }, indent=2))
        return 0

    _print_card(descriptor, card)
    return 0


def _print_card(descriptor, card) -> None:
    print()
    print("┌─ Tone Forge — Helix chain ─────────────────────────────")
    print(f"│ source: {descriptor.source.filename}  ({descriptor.source.kind}, {descriptor.source.duration_sec:.1f}s)")
    print(f"│ amp family: {descriptor.amp.family}   gain: {descriptor.amp.gain:.2f}   "
          f"confidence: {descriptor.confidence.amp_family:.0%}")
    print("├─ Signal chain ─────────────────────────────────────────")
    n = 0
    for pick in card.picks:
        if pick.slot == "amp_alt":
            print(f"│    [A/B alt] {pick.display}")
            print(f"│            ↳ {pick.rationale}")
            continue
        n += 1
        print(f"│ {n}. [{pick.slot:>10}]  {pick.display}")
        for k, v in pick.params.items():
            print(f"│            {k}: {v}")
        print(f"│            ↳ {pick.rationale}")
    if card.tweak_hints:
        print("├─ Tweak hints ──────────────────────────────────────────")
        for h in card.tweak_hints:
            print(f"│ • {h}")
    print("└────────────────────────────────────────────────────────")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
