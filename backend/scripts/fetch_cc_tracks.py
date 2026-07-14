#!/usr/bin/env python3
"""Add a curated CC0/CC-BY track to the demo-track catalog.

Copies a local audio file into ``static/cc-tracks/audio/`` and appends
a validated entry to ``static/cc-tracks/catalog.json``. Track sourcing
stays manual (download the file yourself, check the license page) —
this script is the gate that guarantees every shipped entry carries a
complete, legally sufficient attribution before it can appear in the
pickers:

  * license must be CC0 or CC-BY
  * CC-BY requires title + artist + sourceUrl + licenseUrl (stems are
    derivatives, so the full TASL credit is non-negotiable)
  * attribution defaults to the canonical "“{title}” by {artist}
    ({license}), {sourceUrl}" line when not supplied verbatim

Idempotent per id — re-adding an id overwrites its entry and audio.

Usage:
    python3 scripts/fetch_cc_tracks.py add \
        --file ~/Downloads/song.mp3 \
        --id night-drive --title "Night Drive" --artist "Some Artist" \
        --license CC-BY \
        --license-url https://creativecommons.org/licenses/by/4.0/ \
        --source-url https://example.org/song \
        [--attribution "verbatim credit line"] \
        [--description "moody synthwave, 96 BPM"]

    python3 scripts/fetch_cc_tracks.py list
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

CC_TRACKS_DIR = Path(__file__).resolve().parents[1] / "static" / "cc-tracks"
CATALOG_PATH = CC_TRACKS_DIR / "catalog.json"
AUDIO_DIR = CC_TRACKS_DIR / "audio"

ACCEPTED_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
LICENSES = {"CC0", "CC-BY"}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def _load_catalog() -> dict:
    if CATALOG_PATH.exists():
        return json.loads(CATALOG_PATH.read_text())
    return {"catalogVersion": 1, "tracks": []}


def _save_catalog(catalog: dict) -> None:
    CATALOG_PATH.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")


def _duration_sec(path: Path) -> float:
    import mutagen

    audio = mutagen.File(str(path))
    if audio is None or not getattr(audio, "info", None):
        raise SystemExit(f"could not read duration from {path}")
    return round(float(audio.info.length), 2)


def cmd_add(args: argparse.Namespace) -> None:
    src = Path(args.file).expanduser()
    if not src.is_file():
        raise SystemExit(f"no such file: {src}")
    if src.suffix.lower() not in ACCEPTED_SUFFIXES:
        raise SystemExit(f"unsupported suffix {src.suffix}; accepted: {sorted(ACCEPTED_SUFFIXES)}")
    if not _ID_RE.match(args.id):
        raise SystemExit("id must be lowercase slug (a-z, 0-9, hyphens)")
    if args.license not in LICENSES:
        raise SystemExit(f"license must be one of {sorted(LICENSES)}")

    # CC-BY attribution completeness (TASL): stems are derivatives, so
    # a partial credit is a license violation waiting to ship.
    if args.license == "CC-BY":
        missing = [
            name for name, value in (
                ("--title", args.title), ("--artist", args.artist),
                ("--source-url", args.source_url), ("--license-url", args.license_url),
            ) if not value
        ]
        if missing:
            raise SystemExit(f"CC-BY requires {', '.join(missing)}")
    if not args.title:
        raise SystemExit("--title is required")

    attribution = args.attribution or ""
    if not attribution and args.license == "CC-BY":
        attribution = f"“{args.title}” by {args.artist} (CC BY), {args.source_url}"

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    dest = AUDIO_DIR / f"{args.id}{src.suffix.lower()}"
    shutil.copyfile(src, dest)

    entry = {
        "id": args.id,
        "title": args.title,
        "artist": args.artist or "",
        "license": args.license,
        "licenseUrl": args.license_url or "",
        "sourceUrl": args.source_url or "",
        "attribution": attribution,
        "file": dest.name,
        "durationSec": _duration_sec(dest),
        "description": args.description or "",
    }
    catalog = _load_catalog()
    catalog["tracks"] = [t for t in catalog.get("tracks", []) if t.get("id") != args.id]
    catalog["tracks"].append(entry)
    _save_catalog(catalog)
    print(f"added {args.id}: {entry['title']} — {entry['artist']} [{entry['license']}]")


def cmd_list(_args: argparse.Namespace) -> None:
    for t in _load_catalog().get("tracks", []):
        print(f"{t['id']:24} {t.get('license', ''):6} {t.get('title', '')} — {t.get('artist', '')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="validate + copy + append a catalog entry")
    add.add_argument("--file", required=True)
    add.add_argument("--id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--artist", default="")
    add.add_argument("--license", required=True, choices=sorted(LICENSES))
    add.add_argument("--license-url", default="")
    add.add_argument("--source-url", default="")
    add.add_argument("--attribution", default="")
    add.add_argument("--description", default="")
    add.set_defaults(func=cmd_add)

    lst = sub.add_parser("list", help="print the current catalog")
    lst.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
