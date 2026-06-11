#!/usr/bin/env python3
"""Build master_template.als from a user-saved Live session.

For Stage 2 of the M4L automated render pipeline.

Background:
    `build_per_preset_als.py` needs a `master_template.als` whose
    <MasterTrack> already carries the tf_recorder MxAudioEffect device.
    Constructing that <MasterTrack> XML from scratch is risky (Live-specific
    UUIDs, device hashes, automation envelope IDs).

    Sidestep: have the operator save their current Live session (which
    already has tf_recorder on Main from the Stage 1 PoC) via File →
    Save As to any path. This script then:

    1. Reads the saved ALS (gzipped XML).
    2. Verifies <MasterTrack> contains an MxDeviceAudioEffect (the recorder).
    3. Replaces the <Tracks>...</Tracks> children with an empty block so
       only the Main track remains.
    4. Writes the result as master_template.als next to this script (or
       --out path).

What survives:
    - <MasterTrack> with the tf_recorder M4L device
    - <Tempo>, <Scene*>, transport state (Live re-derives most of this)
    - The empty <Tracks/> element (so Live still parses the project)

What is stripped:
    - All <MidiTrack>, <AudioTrack>, <GroupTrack> entries inside <Tracks>
    - <ReturnTracks> are also stripped (not needed; the PoC didn't use them)

Usage:
    python3 scripts/render_via_m4l/extract_master_template.py \\
        /tmp/poc_session.als

    # or with explicit output path
    python3 scripts/render_via_m4l/extract_master_template.py \\
        /tmp/poc_session.als \\
        --out scripts/render_via_m4l/master_template.als
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "master_template.als"

# Strip the inner contents of <Tracks>...</Tracks>. Keep the open/close
# tags themselves so Live still sees a valid (empty) tracks container.
TRACKS_BODY_RE = re.compile(
    r"(<Tracks\b[^>]*>)(.*?)(</Tracks>)",
    re.DOTALL,
)

# Same treatment for <ReturnTracks> if present.
RETURN_TRACKS_BODY_RE = re.compile(
    r"(<ReturnTracks\b[^>]*>)(.*?)(</ReturnTracks>)",
    re.DOTALL,
)

# Live 12 renamed <MasterTrack> to <MainTrack>. Accept either so the
# script keeps working across Live versions.
MASTER_TRACK_RE = re.compile(
    r"<(MainTrack|MasterTrack)\b[^>]*>.*?</\1>",
    re.DOTALL,
)

MAX_DEVICE_MARKER = "MxDeviceAudioEffect"


def _read_als_text(path: Path) -> str:
    raw = path.read_bytes()
    return gzip.decompress(raw).decode("utf-8", errors="replace")


def _write_als_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(text.encode("utf-8")))


def _verify_master_has_recorder(als_text: str, source_path: Path) -> None:
    m = MASTER_TRACK_RE.search(als_text)
    if not m:
        raise RuntimeError(
            f"source ALS at {source_path} has no <MainTrack>/<MasterTrack> element"
        )
    if MAX_DEVICE_MARKER not in m.group(0):
        raise RuntimeError(
            f"source ALS at {source_path} <{m.group(1)}> has no "
            f"{MAX_DEVICE_MARKER} device. Drop tf_recorder.amxd on Main "
            "in Live, re-save the session, and re-run."
        )


def _strip_tracks(als_text: str) -> tuple[str, int, int]:
    """Empty out <Tracks> and <ReturnTracks> bodies. Returns (text, n_tracks_stripped, n_returns_stripped)."""

    n_tracks = 0
    n_returns = 0

    def _empty_tracks(match: re.Match[str]) -> str:
        nonlocal n_tracks
        body = match.group(2)
        # Approximate count of stripped tracks by counting opening track tags.
        n_tracks += len(re.findall(r"<(?:MidiTrack|AudioTrack|GroupTrack)\b", body))
        return match.group(1) + match.group(3)

    def _empty_returns(match: re.Match[str]) -> str:
        nonlocal n_returns
        body = match.group(2)
        n_returns += len(re.findall(r"<ReturnTrack\b", body))
        return match.group(1) + match.group(3)

    new_text, n_tracks_sub = TRACKS_BODY_RE.subn(_empty_tracks, als_text, count=1)
    if n_tracks_sub != 1:
        raise RuntimeError(
            "source ALS did not contain exactly one <Tracks> element; "
            "cannot safely strip."
        )

    new_text, _ = RETURN_TRACKS_BODY_RE.subn(_empty_returns, new_text, count=1)
    # ReturnTracks may legitimately be absent in some Live setups; do not
    # treat that as an error.

    return new_text, n_tracks, n_returns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        help="Path to the saved Live session ALS (has tf_recorder on Main)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output path for master_template.als (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"ERROR: source ALS not found at {args.source}", file=sys.stderr)
        return 2

    try:
        als_text = _read_als_text(args.source)
    except Exception as e:  # noqa: BLE001
        print(
            f"ERROR: could not read {args.source} as a gzipped ALS: "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 2

    try:
        _verify_master_has_recorder(als_text, args.source)
        stripped_text, n_tracks, n_returns = _strip_tracks(als_text)
        # Re-verify after strip — paranoia, but ensures we did not blow
        # the MasterTrack away with an over-eager regex.
        _verify_master_has_recorder(stripped_text, args.source)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    _write_als_text(args.out, stripped_text)

    print(f"Wrote master_template.als -> {args.out}")
    print(f"  source ALS:        {args.source}")
    print(f"  tracks stripped:   {n_tracks}")
    print(f"  returns stripped:  {n_returns}")
    print(f"  MasterTrack:       preserved (contains {MAX_DEVICE_MARKER})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
