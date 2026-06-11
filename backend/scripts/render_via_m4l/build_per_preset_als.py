#!/usr/bin/env python3
"""Generate per-preset ALS files with the tf_recorder M4L device on Main.

For Stage 2 of the M4L automated render pipeline.

Strategy: master-template clone.
- Operator saves master_template.als ONCE with the tf_recorder M4L device
  on the Main track (and nothing else).
- This script:
    1. Reads master_template.als, extracts its <MasterTrack> block (which
       carries the embedded tf_recorder MxAudioEffect device).
    2. Calls preset_als_generator.create_preset_als(preset, notes, tempo)
       to build a per-preset synth ALS the normal way (one MIDI track,
       one MIDI clip, embedded synth device).
    3. Replaces the synth ALS's MasterTrack with the master_template's
       MasterTrack, so the recorder is present on Main in every output
       file.
    4. Writes the spliced ALS to <out_dir>/<safe_filename>.als.

The output files are intended to be fed to scripts/render_via_m4l/batch_render.py.

Usage:
    python3 scripts/render_via_m4l/build_per_preset_als.py \
        --instruments Analog Drift \
        --out-dir preset_catalog_output/als_v2
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
from pathlib import Path
from typing import List

# Backend on import path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tone_forge.preset_catalog.preset_als_generator import create_preset_als  # noqa: E402
from tone_forge.preset_catalog.preset_discovery import (  # noqa: E402
    PresetInfo,
    discover_presets,
    safe_filename,
)
from tone_forge.preset_catalog.test_sequence import get_test_sequence_for_type  # noqa: E402


DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parent / "master_template.als"
)
# Live 12 renamed <MasterTrack> to <MainTrack>. Match either form so the
# splicer works whether the template was authored in Live 11 or 12, and
# whether the synth ALS uses the old or new schema.
MASTER_TRACK_RE = re.compile(
    r"<(MainTrack|MasterTrack)\b[^>]*>.*?</\1>",
    re.DOTALL,
)
# preset_als_generator emits a vestigial trio at LiveSet scope (used by
# Live 11's color-picker view config) AFTER its real <MasterTrack>:
#   <MainTrackEnabled .../><MainTrackColor .../><MainTrack>...</MainTrack>
# When we splice a Live 12 <MainTrack> in place of <MasterTrack>, the
# vestigial stub becomes a second <MainTrack> at LiveSet scope and
# Live 12 rejects the file ("Class LiveDocument already has member
# MainTrack"). Strip the trio defensively before splicing.
VESTIGIAL_MAIN_RE = re.compile(
    r"\s*<MainTrackEnabled\b[^/]*/>"
    r"\s*<MainTrackColor\b[^/]*/>"
    r"\s*<MainTrack\b[^>]*>.*?</MainTrack>",
    re.DOTALL,
)
MAX_DEVICE_MARKER = "MxDeviceAudioEffect"


def _read_als_text(path: Path) -> str:
    """Gunzip an ALS file and decode it as UTF-8 text."""
    raw = path.read_bytes()
    return gzip.decompress(raw).decode("utf-8", errors="replace")


def _write_als_text(path: Path, text: str) -> None:
    """Gzip a UTF-8 ALS text body and write it to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(text.encode("utf-8")))


def _extract_master_track(template_text: str, template_path: Path) -> str:
    """Pull the <MainTrack>/<MasterTrack> block out of master_template."""
    m = MASTER_TRACK_RE.search(template_text)
    if not m:
        raise RuntimeError(
            f"master_template at {template_path} does not contain a "
            "<MainTrack>/<MasterTrack> element. Save the template per the "
            "operator step in README_stage2.md."
        )
    block = m.group(0)
    if MAX_DEVICE_MARKER not in block:
        raise RuntimeError(
            f"master_template at {template_path} has a {m.group(1)} but no "
            f"{MAX_DEVICE_MARKER} device. Drop tf_recorder.amxd on the Main "
            "track, re-save, and re-run."
        )
    return block


_ID_RE = re.compile(r'\bId="(\d+)"')
_POINTEE_REF_RE = re.compile(r'(<PointeeId\s+Value=")(\d+)(")')
_NEXT_POINTEE_RE = re.compile(r'(<NextPointeeId\s+Value=")(\d+)("\s*/?>)')


def _offset_ids_in_block(block_xml: str, offset: int) -> str:
    """Add `offset` to every Id="N" declaration AND every <PointeeId
    Value="N"/> reference in `block_xml`.

    The master template extracted from a real Live save carries
    Pointee IDs Live assigned (e.g. up to 23232). The synth ALS uses
    its own IDs starting from 0 (up to ~5000). Offsetting the
    template-side IDs by max_synth_id+1 guarantees they cannot
    collide.

    Two passes are required because Live 12 expresses references in
    a different syntax than declarations:

      - Declarations use `Id="N"` (e.g. <AutomationTarget Id="10">,
        <AutomationEnvelope Id="1298">).
      - Cross-references use `<PointeeId Value="N"/>` inside
        <EnvelopeTarget> blocks.

    If only declarations are offset, the references inside
    EnvelopeTarget still point at the pre-offset IDs and Live 12
    crashes on load with:
        Invalid Pointee ID N in APtr .../EnvelopeTarget
        ASSERT 'mpObject': expression failed in APtr.cpp:171
    """
    block_xml = _ID_RE.sub(
        lambda m: f'Id="{int(m.group(1)) + offset}"',
        block_xml,
    )
    block_xml = _POINTEE_REF_RE.sub(
        lambda m: f"{m.group(1)}{int(m.group(2)) + offset}{m.group(3)}",
        block_xml,
    )
    return block_xml


def _splice_master_track(synth_als_text: str, master_track_xml: str) -> str:
    """Replace the synth ALS's <MasterTrack> with the template's block.

    Three corrections are applied in order:

    1. Strip the vestigial MainTrackEnabled/MainTrackColor/MainTrack
       trio that preset_als_generator emits at LiveSet scope (see
       VESTIGIAL_MAIN_RE comment), otherwise Live 12 rejects the file
       with 'Class LiveDocument already has member MainTrack'.

    2. Offset every Id="N" in the spliced template block by
       (max_synth_id + 1) so no Id collides with the synth ALS.
       Live 12 otherwise rejects the file with 'corrupt
       (non-unique Pointee IDs)'.

    3. Bump <NextPointeeId> above every Id in the resulting doc.
       Live 12 otherwise rejects with 'NextPointeeId is too low'.
    """
    # Compute the offset BEFORE splicing, using only the synth body's IDs.
    synth_ids = [int(m.group(1)) for m in _ID_RE.finditer(synth_als_text)]
    max_synth_id = max(synth_ids, default=0)
    offset = max_synth_id + 1
    offset_master_track_xml = _offset_ids_in_block(master_track_xml, offset)

    new_text, n_subs = MASTER_TRACK_RE.subn(
        # Use a function so backslashes / $ in the replacement are literal.
        lambda _m: offset_master_track_xml,
        synth_als_text,
        count=1,
    )
    if n_subs != 1:
        raise RuntimeError(
            "synth ALS produced by create_preset_als() did not contain "
            "exactly one <MasterTrack> element; cannot splice."
        )
    new_text, _n_vestigial = VESTIGIAL_MAIN_RE.subn("", new_text, count=1)
    new_text = _bump_next_pointee_id(new_text)
    return new_text


def _bump_next_pointee_id(als_text: str) -> str:
    """Ensure NextPointeeId is strictly greater than every Id="..." in the doc."""
    max_id = max((int(m.group(1)) for m in _ID_RE.finditer(als_text)), default=0)
    new_value = max_id + 1
    new_text, n = _NEXT_POINTEE_RE.subn(
        lambda m: f"{m.group(1)}{new_value}{m.group(3)}",
        als_text,
        count=1,
    )
    if n != 1:
        raise RuntimeError(
            "synth ALS did not contain a <NextPointeeId Value='...'/> element; "
            "cannot bump."
        )
    return new_text


def _build_one(
    preset: PresetInfo,
    master_track_xml: str,
    out_dir: Path,
    tempo: float,
) -> Path:
    """Build a single per-preset ALS inside its own Project folder.

    Live 12 refuses to keep an ALS open if it sits outside a Project
    folder once that ALS is dirty (it pops "Live must save the Set into
    a Project folder. Please choose a Project folder..."). The
    orchestrator's Cmd+D ('Don't Save') shortcut cannot dismiss that
    dialog (it only has Cancel + Save As).

    Sidestep by laying out each preset as:

        <out_dir>/<safe_id> Project/
            Ableton Project Info/   (empty dir; Live regenerates contents on load)
            <safe_id>.als
    """
    notes, _midi_bytes = get_test_sequence_for_type(preset.sound_type, tempo=tempo)
    synth_bytes = create_preset_als(preset, notes, tempo=tempo)
    synth_text = gzip.decompress(synth_bytes).decode("utf-8", errors="replace")
    spliced = _splice_master_track(synth_text, master_track_xml)

    safe_id = safe_filename(preset.preset_id)
    project_dir = out_dir / f"{safe_id} Project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "Ableton Project Info").mkdir(exist_ok=True)
    out_path = project_dir / f"{safe_id}.als"
    _write_als_text(out_path, spliced)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--instruments",
        nargs="+",
        default=["Analog", "Drift"],
        help="Instruments to render (default: Analog Drift)",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help=f"Master template ALS (default: {DEFAULT_TEMPLATE})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("preset_catalog_output/als_v2"),
        help="Output directory for generated per-preset ALS files",
    )
    parser.add_argument(
        "--tempo",
        type=float,
        default=120.0,
        help="Tempo (BPM) for the generated MIDI clip (default: 120)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional: only build the first N presets (for smoke tests)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Optional: substring match preset name/preset_id (for pilots)",
    )
    args = parser.parse_args()

    if not args.template.exists():
        print(
            f"ERROR: master_template not found at {args.template}\n"
            "Follow the operator step in README_stage2.md to create it.",
            file=sys.stderr,
        )
        return 2

    template_text = _read_als_text(args.template)
    master_track_xml = _extract_master_track(template_text, args.template)

    presets: List[PresetInfo] = discover_presets(args.instruments)
    if args.only:
        needle = args.only.lower()
        presets = [
            p for p in presets
            if needle in p.preset_id.lower() or needle in p.name.lower()
        ]
    if args.limit is not None:
        presets = presets[: args.limit]

    if not presets:
        print("ERROR: no presets matched.", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    n_fail = 0
    failures: list[tuple[str, str]] = []
    for preset in presets:
        try:
            out_path = _build_one(preset, master_track_xml, args.out_dir, args.tempo)
            print(f"  OK  {preset.preset_id:60s} -> {out_path.name}")
            n_ok += 1
        except Exception as e:  # noqa: BLE001 — surface every failure type
            print(f"  FAIL {preset.preset_id:60s}  {type(e).__name__}: {e}",
                  file=sys.stderr)
            failures.append((preset.preset_id, f"{type(e).__name__}: {e}"))
            n_fail += 1

    print()
    print(f"Built {n_ok} / {n_ok + n_fail} per-preset ALS files at {args.out_dir}")
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for preset_id, msg in failures[:20]:
            print(f"  - {preset_id}: {msg}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
