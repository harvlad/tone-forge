"""Compose a single Ableton Live Set containing one MIDI track per
supported instrument, each carrying an embedded `.adv` parameter tree.

Each track is functionally equivalent to what
``preset_als_generator.create_preset_als`` produces in single-track mode,
but all 8 tracks live in one ALS so the operator can A/B them in a
single Live session.

Id collision handling
---------------------
The single-track template hardcodes a set of fixed XML Ids:

* AutomationTarget Ids ``100``–``108``
* MidiClip Id ``200``
* device opening tag Id (``500`` from the generator), and renumbered
  ``.adv``-internal Ids starting at ``1000``

If we naively concatenate N tracks all of these collide. Each track's
XML is post-processed by adding ``offset = (track_index + 1) * 10000``
to every ``Id="N"`` occurrence inside that track. Master-track Ids
(1–8) stay below the offset window so they don't collide either.
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tone_forge.preset_catalog.preset_discovery import (  # noqa: E402
    PresetInfo,
    SUPPORTED_INSTRUMENTS,
    discover_presets,
    safe_filename,
)
from tone_forge.preset_catalog.preset_als_generator import (  # noqa: E402
    DEVICE_CONFIG,
    _assert_device_nontrivial,
    _build_als_xml,
    _build_device_xml,
    _build_key_tracks_xml,
    _build_midi_track_xml,
    get_device_config,
    xml_escape,
)
from tone_forge.preset_catalog.test_sequence import (  # noqa: E402
    get_test_sequence_for_type,
)


def _offset_track_ids(track_xml: str, offset: int) -> str:
    """Add ``offset`` to every ``Id="N"`` attribute in a track's XML.

    Uses a regex with a function substitution so we increment all numeric
    Ids in one pass without touching other ``Id`` shapes.
    """
    def _sub(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        return f'Id="{n + offset}"'

    return re.sub(r'Id="(\d+)"', _sub, track_xml)


def _pick_preset_for_instrument(instrument: str) -> PresetInfo:
    """Pick a Bass-category preset if available, else the first one."""
    presets = discover_presets([instrument])
    if not presets:
        raise RuntimeError(f"no presets discovered for {instrument!r}")
    bass = [p for p in presets if p.sound_type == "bass"]
    return bass[0] if bass else presets[0]


def _pick_preset_by_name(instrument: str, name_substring: str) -> PresetInfo:
    presets = discover_presets([instrument])
    if not presets:
        raise RuntimeError(f"no presets discovered for {instrument!r}")
    matches = [p for p in presets if name_substring.lower() in p.name.lower()]
    if not matches:
        raise RuntimeError(
            f"no {instrument!r} preset matched name substring "
            f"{name_substring!r}; first available: "
            f"{[p.name for p in presets[:5]]!r}"
        )
    return matches[0]


def build_multi_instrument_als(
    presets: List[PresetInfo],
    tempo: float = 120.0,
) -> bytes:
    """Assemble a single ALS containing one track per preset.

    Args:
        presets: One ``PresetInfo`` per track. Order is preserved.
        tempo: Project tempo.

    Returns:
        Gzipped ALS bytes.
    """
    if not presets:
        raise ValueError("build_multi_instrument_als() needs >=1 preset")

    track_xmls: List[str] = []
    for track_index, preset in enumerate(presets):
        notes, _midi = get_test_sequence_for_type(preset.sound_type, tempo)
        if notes:
            max_end = max(n.start_beats + n.duration_beats for n in notes)
            clip_end = max(4, ((int(max_end) + 3) // 4) * 4)
        else:
            clip_end = 8

        config = get_device_config(preset.instrument)
        device_xml = _build_device_xml(preset, config)
        _assert_device_nontrivial(device_xml, config)

        key_tracks_xml = _build_key_tracks_xml(notes)

        # Reserve unique track ids starting at 3 (Live's first-user-track
        # convention in the existing template). Each track also gets an
        # internal-Id offset so AutomationTargets / clip / device Ids
        # don't collide with adjacent tracks.
        track_id = 3 + track_index
        track_xml = _build_midi_track_xml(
            track_id=track_id,
            name=xml_escape(f"{preset.instrument}: {preset.name}"),
            clip_end=clip_end,
            key_tracks_xml=key_tracks_xml,
            device_xml=device_xml,
        )

        offset = (track_index + 1) * 10000
        track_xmls.append(_offset_track_ids(track_xml, offset))

    als_xml = _build_als_xml(tracks="\n".join(track_xmls), tempo=tempo)

    # NextPointeeId must be strictly greater than every Id="N" in the
    # document. The template hardcodes 5000, which is fine for a single
    # track but breaks once track-offsetting pushes Ids past it. Recompute
    # the high-water mark from the assembled XML and patch the value.
    max_id = max(int(m) for m in re.findall(r'Id="(\d+)"', als_xml))
    als_xml = re.sub(
        r'<NextPointeeId Value="\d+"/>',
        f'<NextPointeeId Value="{max_id + 1000}"/>',
        als_xml,
        count=1,
    )

    return gzip.compress(als_xml.encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "preset_catalog_output/equivalence/"
            "multi_instrument_demo.als"
        ),
        help="Where to write the composed ALS.",
    )
    parser.add_argument(
        "--instruments",
        nargs="*",
        default=SUPPORTED_INSTRUMENTS,
        help=(
            "Which instruments to include, in order. Default: all "
            "SUPPORTED_INSTRUMENTS."
        ),
    )
    parser.add_argument(
        "--preset",
        action="append",
        default=[],
        metavar="INSTRUMENT=NAME_SUBSTRING",
        help=(
            "Override the auto-picked preset for an instrument, e.g. "
            "--preset Analog='Saw Filter Bass'. Repeatable."
        ),
    )
    parser.add_argument("--tempo", type=float, default=120.0)
    args = parser.parse_args()

    overrides = {}
    for raw in args.preset:
        if "=" not in raw:
            print(f"ERROR: --preset must be INSTRUMENT=NAME, got {raw!r}",
                  file=sys.stderr)
            return 2
        inst, name = raw.split("=", 1)
        overrides[inst.strip()] = name.strip()

    presets: List[PresetInfo] = []
    for inst in args.instruments:
        if inst not in DEVICE_CONFIG:
            print(f"ERROR: instrument {inst!r} has no DEVICE_CONFIG; "
                  f"known: {sorted(DEVICE_CONFIG)}", file=sys.stderr)
            return 2
        if inst in overrides:
            p = _pick_preset_by_name(inst, overrides[inst])
        else:
            p = _pick_preset_for_instrument(inst)
        presets.append(p)
        print(f"  {inst:10s}  -> {p.name!r:40s}  "
              f"category={p.category!r}, sound_type={p.sound_type!r}")

    data = build_multi_instrument_als(presets, tempo=args.tempo)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"\nWrote {args.output} ({len(data)} bytes, "
          f"{len(presets)} tracks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
