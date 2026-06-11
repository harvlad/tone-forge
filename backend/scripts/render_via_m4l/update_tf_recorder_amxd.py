#!/usr/bin/env python3
"""Rebuild tf_recorder.amxd from the current tf_recorder.maxpat.

The Max for Live .amxd file format is a small chunked binary:

    [ampf][0x04000000][aaaa]              -- 12-byte magic
    [meta][len][meta_payload]             -- usually 8 bytes of meta payload
    [ptch][len_le32][patcher_json_bytes]  -- the .maxpat JSON as raw bytes

This script:
1. Reads tf_recorder.maxpat from disk (canonical patcher source).
2. Reads the existing tf_recorder.amxd from the User Library to preserve
   the header chunks Max writes (meta payload differs per Max version).
3. Replaces the `ptch` payload with the current maxpat JSON bytes.
4. Writes the rebuilt amxd in place.

Effect: any path / wiring change in tf_recorder.maxpat (e.g. open
message now points at /tmp/.../current.wav instead of poc_render.wav)
takes effect the next time you drop tf_recorder.amxd into a Live track.

Existing device instances already on tracks are NOT updated — they
embed their patcher copy at the time they were added. To pick up the
new path, the operator must delete the device from the track and
re-drop the updated .amxd.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
MAXPAT_PATH = HERE / "tf_recorder.maxpat"
DEFAULT_AMXD = Path(
    "/Users/mattharvey/Music/Ableton/User Library/"
    "Presets/Audio Effects/Max Audio Effect/tf_recorder.amxd"
)


def _read_chunks(data: bytes) -> list[tuple[str, bytes]]:
    """Parse the amxd as a sequence of (tag, payload) chunks.

    Header layout (observed on Max 9 amxd output):
        offset 0  : 'ampf' (4 bytes, no length following)
        offset 4  : 0x04 0x00 0x00 0x00  (4 bytes, version marker)
        offset 8  : 'aaaa' (4 bytes)
        offset 12 : 'meta' 'ptch' etc. chunks: 4-byte tag + 4-byte LE length + payload
    """
    out: list[tuple[str, bytes]] = []
    # First 12 bytes are the magic preamble.
    if data[:4] != b"ampf":
        raise ValueError("not an amxd: missing 'ampf' magic")
    pos = 12
    while pos + 8 <= len(data):
        tag = data[pos : pos + 4].decode("ascii", errors="replace")
        length = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        payload_start = pos + 8
        payload_end = payload_start + length
        if payload_end > len(data):
            raise ValueError(
                f"chunk '{tag}' at offset {pos} claims length {length} "
                f"but only {len(data) - payload_start} bytes remain"
            )
        out.append((tag, data[payload_start:payload_end]))
        pos = payload_end
    return out


def _rebuild_amxd(original: bytes, new_patcher: bytes) -> bytes:
    """Return an amxd byte string with the `ptch` chunk replaced."""
    chunks = _read_chunks(original)
    rebuilt = bytearray(original[:12])  # preserve 12-byte magic preamble
    found_ptch = False
    for tag, payload in chunks:
        if tag == "ptch":
            found_ptch = True
            payload = new_patcher
        rebuilt += tag.encode("ascii")
        rebuilt += struct.pack("<I", len(payload))
        rebuilt += payload
    if not found_ptch:
        raise RuntimeError("original amxd had no 'ptch' chunk — refusing to rewrite")
    return bytes(rebuilt)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--maxpat",
        type=Path,
        default=MAXPAT_PATH,
        help=f"Source .maxpat (default: {MAXPAT_PATH})",
    )
    parser.add_argument(
        "--amxd",
        type=Path,
        default=DEFAULT_AMXD,
        help=f"Target .amxd in User Library (default: {DEFAULT_AMXD})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional alternative output path (default: write back to --amxd in place)",
    )
    args = parser.parse_args()

    if not args.maxpat.exists():
        print(f"ERROR: maxpat not found at {args.maxpat}", file=sys.stderr)
        return 2
    if not args.amxd.exists():
        print(
            f"ERROR: original amxd not found at {args.amxd}\n"
            "Build tf_recorder.amxd once via Live (Stage 1 PoC) so we have a "
            "template chunk layout to preserve.",
            file=sys.stderr,
        )
        return 2

    maxpat_bytes = args.maxpat.read_bytes()
    amxd_bytes = args.amxd.read_bytes()

    rebuilt = _rebuild_amxd(amxd_bytes, maxpat_bytes)

    out_path = args.out if args.out else args.amxd
    out_path.write_bytes(rebuilt)

    print(f"Wrote {len(rebuilt)} bytes -> {out_path}")
    print(f"  source maxpat: {args.maxpat} ({len(maxpat_bytes)} bytes)")
    print(f"  source amxd:   {args.amxd} ({len(amxd_bytes)} bytes)")
    print()
    print("NOTE: a Live track that already hosts a tf_recorder device will NOT")
    print("pick up this change automatically. To activate the new patcher,")
    print("delete the device from the track and re-drop tf_recorder.amxd.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
