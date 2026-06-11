#!/usr/bin/env python3
"""Stage 2 batch driver — unattended render of per-preset ALS files.

For each ALS in --als-dir:
    1. Open it in Live (macOS `open` command — Live adopts the open instance).
    2. Bring Live to foreground via AppleScript activate.
    3. Wait `--load-settle` seconds for Live to load the project and
       arm the M4L recorder (live.thisdevice → openmsg → sfrecord~).
    4. Reset the transport to bar 1 (no-op if already there).
    5. Send `space` keystroke to start playback.
    6. Wait `clip_duration + tail_buffer` seconds.
    7. Send `space` to stop playback. sfrecord~ flushes on receipt
       of the `0` message (chained from sel-matched-0).
    8. Poll the staging WAV for size stability (no growth for `stable_window`).
    9. Move staging WAV to <audio-dir>/<preset_id>.wav.
    10. Write a manifest row (adv sha, als sha, wav sha, duration).

The clip duration is read from the per-preset ALS itself (CurrentEnd
attribute on the embedded MidiClip), at the configured `--tempo`.

Usage:
    python3 scripts/render_via_m4l/batch_render.py \\
        --als-dir preset_catalog_output/als_v2 \\
        --audio-dir preset_catalog_output/audio_v2 \\
        [--pilot]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Live's macOS process name is just "Live" (not "Ableton Live 12 Standard"
# as shown in /Applications). AppleScript's `tell application` / `tell process`
# need this short name; using the long name silently no-ops and Live never
# gets foreground focus, so Play keystrokes go to whatever was previously
# foreground (typically Terminal). Verified via System Events on this box.
LIVE_APP_NAME = "Live"
STAGING_DEFAULT = Path("/tmp/tone_forge_render_poc/current.wav")
CLIP_END_RE = re.compile(r'<CurrentEnd\s+Value="([0-9.]+)"\s*/?>')


@dataclass
class ManifestRow:
    preset_id: str
    als_path: str
    als_sha1: str
    wav_path: str
    wav_sha1: str
    duration_seconds: float
    clip_beats: float
    tempo_bpm: float
    bytes_written: int
    status: str
    error: Optional[str] = None


@dataclass
class BatchConfig:
    als_dir: Path
    audio_dir: Path
    staging: Path
    manifest_path: Path
    tempo: float
    tail_buffer_s: float
    load_settle_s: float
    stable_window_s: float
    stable_poll_s: float
    timeout_s: float
    dry_run: bool
    failures: list = field(default_factory=list)


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _osascript(script: str) -> None:
    """Run an AppleScript snippet via osascript."""
    subprocess.run(["osascript", "-e", script], check=True)


def _activate_live() -> None:
    _osascript(f'tell application "{LIVE_APP_NAME}" to activate')


def _send_key_code(code: int) -> None:
    """Send a raw key code to the foreground process via System Events.

    Use this for non-character keys (Escape=53, Return=36, etc).
    `keystroke` is text-only and cannot send Escape.
    """
    _osascript(
        f'tell application "System Events" to key code {code}'
    )


def _send_keystroke(key: str, modifiers: Optional[list[str]] = None) -> None:
    """Send a keystroke to Live via System Events.

    `key`: a single character (e.g. " " for space) or a special key name.
    `modifiers`: list of "command down" / "shift down" / etc.
    """
    if modifiers:
        mod_str = ", ".join(modifiers)
        script = (
            f'tell application "System Events" to '
            f'tell process "{LIVE_APP_NAME}" to '
            f'keystroke "{key}" using {{{mod_str}}}'
        )
    else:
        script = (
            f'tell application "System Events" to '
            f'tell process "{LIVE_APP_NAME}" to '
            f'keystroke "{key}"'
        )
    _osascript(script)


def _open_als(als_path: Path) -> None:
    """Hand the ALS to Live via macOS LaunchServices."""
    subprocess.run(["open", str(als_path)], check=True)


def _read_clip_beats(als_path: Path) -> float:
    """Read the MIDI clip end (in beats) from the ALS body."""
    raw = als_path.read_bytes()
    text = gzip.decompress(raw).decode("utf-8", errors="replace")
    m = CLIP_END_RE.search(text)
    if not m:
        raise RuntimeError(f"could not find <CurrentEnd> in {als_path}")
    return float(m.group(1))


def _wait_for_size_stable(
    path: Path,
    window_s: float,
    poll_s: float,
    timeout_s: float,
) -> int:
    """Poll a growing file until its size is stable for `window_s`.

    Returns the final size in bytes. Raises TimeoutError if it never
    stabilises within `timeout_s`.
    """
    deadline = time.monotonic() + timeout_s
    last_size = -1
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        if not path.exists():
            if time.monotonic() - last_change > window_s:
                # File never appeared at all; recorder must have failed.
                raise RuntimeError(f"staging file {path} never appeared")
            time.sleep(poll_s)
            continue
        size = path.stat().st_size
        if size != last_size:
            last_size = size
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= window_s and size > 0:
            return size
        time.sleep(poll_s)
    raise TimeoutError(
        f"staging file {path} never reached stable size within {timeout_s:.1f}s "
        f"(last_size={last_size})"
    )


def _render_one(
    als_path: Path,
    preset_id: str,
    cfg: BatchConfig,
) -> ManifestRow:
    """Render a single per-preset ALS to WAV. Returns a manifest row."""
    out_wav = cfg.audio_dir / f"{preset_id}.wav"

    # Compute expected clip duration from the ALS body.
    clip_beats = _read_clip_beats(als_path)
    clip_duration_s = clip_beats * 60.0 / cfg.tempo
    record_duration_s = clip_duration_s + cfg.tail_buffer_s

    # Pre-clean staging path so we never confuse two takes.
    if cfg.staging.exists():
        cfg.staging.unlink()

    if cfg.dry_run:
        return ManifestRow(
            preset_id=preset_id,
            als_path=str(als_path),
            als_sha1=_sha1(als_path),
            wav_path=str(out_wav),
            wav_sha1="",
            duration_seconds=clip_duration_s,
            clip_beats=clip_beats,
            tempo_bpm=cfg.tempo,
            bytes_written=0,
            status="dry_run",
        )

    # 0. Pre-clear: dismiss any leftover dialog from a prior iteration.
    #    Live shows a Save-As dialog if you `open <new.als>` while the
    #    current set is dirty. Escape cancels the dialog and the open
    #    request, so we send Escape FIRST to clear any stale dialog,
    #    then re-issue the open.
    _activate_live()
    time.sleep(0.2)
    _send_key_code(53)  # Escape — cancels any open dialog
    time.sleep(0.3)

    # 1. Open ALS in Live.
    _open_als(als_path)

    # 1b. If Live prompts "Save changes to current Live Set?", click
    #     "Don't Save" (Cmd+D, the standard macOS shortcut). If no
    #     dialog appears, Cmd+D is a no-op in Live's main window.
    time.sleep(0.5)
    _send_keystroke("d", modifiers=["command down"])

    # 2. Let Live load the project + arm the recorder.
    time.sleep(cfg.load_settle_s)

    # 3. Bring Live to foreground (insurance against focus loss).
    _activate_live()
    time.sleep(0.3)

    # 4. Start transport.
    _send_keystroke(" ")

    # 5. Wait for the clip to play through, plus a tail buffer.
    time.sleep(record_duration_s)

    # 6. Stop transport. sfrecord~ flushes on 0 message.
    _send_keystroke(" ")

    # 7. Poll staging file until size stable.
    try:
        size = _wait_for_size_stable(
            cfg.staging,
            window_s=cfg.stable_window_s,
            poll_s=cfg.stable_poll_s,
            timeout_s=cfg.timeout_s,
        )
    except (TimeoutError, RuntimeError) as e:
        return ManifestRow(
            preset_id=preset_id,
            als_path=str(als_path),
            als_sha1=_sha1(als_path),
            wav_path="",
            wav_sha1="",
            duration_seconds=clip_duration_s,
            clip_beats=clip_beats,
            tempo_bpm=cfg.tempo,
            bytes_written=0,
            status="fail",
            error=f"{type(e).__name__}: {e}",
        )

    # 8. Move staging WAV to final per-preset path.
    cfg.audio_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(cfg.staging), str(out_wav))
    wav_sha = _sha1(out_wav)

    return ManifestRow(
        preset_id=preset_id,
        als_path=str(als_path),
        als_sha1=_sha1(als_path),
        wav_path=str(out_wav),
        wav_sha1=wav_sha,
        duration_seconds=clip_duration_s,
        clip_beats=clip_beats,
        tempo_bpm=cfg.tempo,
        bytes_written=size,
        status="ok",
    )


def _gather_als_files(als_dir: Path) -> list[Path]:
    # ALS files now live inside per-preset Project folders
    # (<safe_id> Project/<safe_id>.als). Search both layouts:
    # nested ALSs first, then any flat ALSs left over from older builds.
    nested = sorted(als_dir.glob("*/*.als"))
    flat = sorted(als_dir.glob("*.als"))
    # Dedup while preserving order: nested wins over a flat ALS with the
    # same stem (the older flat copy should not exist after a fresh build,
    # but be defensive).
    seen_stems: set[str] = set()
    out: list[Path] = []
    for p in nested + flat:
        if p.stem in seen_stems:
            continue
        seen_stems.add(p.stem)
        out.append(p)
    return out


def _pick_pilot(als_files: list[Path]) -> list[Path]:
    """Select one Analog + one Drift ALS for the pilot smoke test."""
    analog = next((p for p in als_files if p.name.startswith("analog_")), None)
    drift = next((p for p in als_files if "drift" in p.name.lower()), None)
    picks = [p for p in (analog, drift) if p is not None]
    if not picks:
        # Fall back: take the first 2.
        picks = als_files[:2]
    return picks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--als-dir", type=Path, required=True)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--staging", type=Path, default=STAGING_DEFAULT)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Output manifest JSON (default: <audio-dir>/render_manifest.json)",
    )
    parser.add_argument("--tempo", type=float, default=120.0)
    parser.add_argument(
        "--tail-buffer",
        type=float,
        default=1.5,
        help="Seconds of extra record time after clip end (release tail).",
    )
    parser.add_argument(
        "--load-settle",
        type=float,
        default=4.0,
        help="Seconds to wait for Live to load a new ALS before pressing play.",
    )
    parser.add_argument(
        "--stable-window",
        type=float,
        default=0.5,
        help="Seconds of no-growth on staging file required to consider it final.",
    )
    parser.add_argument("--stable-poll", type=float, default=0.1)
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Max seconds to wait per preset for the staging file to stabilise.",
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="Only render the first Analog + first Drift preset (smoke test).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional: only render the first N ALS files.",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Optional: substring filter on preset_id.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute durations and manifest plan without driving Live.",
    )
    args = parser.parse_args()

    cfg = BatchConfig(
        als_dir=args.als_dir,
        audio_dir=args.audio_dir,
        staging=args.staging,
        manifest_path=(
            args.manifest if args.manifest else args.audio_dir / "render_manifest.json"
        ),
        tempo=args.tempo,
        tail_buffer_s=args.tail_buffer,
        load_settle_s=args.load_settle,
        stable_window_s=args.stable_window,
        stable_poll_s=args.stable_poll,
        timeout_s=args.timeout,
        dry_run=args.dry_run,
    )

    if not cfg.als_dir.is_dir():
        print(f"ERROR: --als-dir does not exist: {cfg.als_dir}", file=sys.stderr)
        return 2

    als_files = _gather_als_files(cfg.als_dir)
    if not als_files:
        print(f"ERROR: no .als files in {cfg.als_dir}", file=sys.stderr)
        return 2

    if args.only:
        needle = args.only.lower()
        als_files = [p for p in als_files if needle in p.stem.lower()]
    if args.pilot:
        als_files = _pick_pilot(als_files)
    elif args.limit is not None:
        als_files = als_files[: args.limit]

    if not als_files:
        print("ERROR: no ALS files match the filter / pilot selection.",
              file=sys.stderr)
        return 1

    cfg.audio_dir.mkdir(parents=True, exist_ok=True)
    cfg.staging.parent.mkdir(parents=True, exist_ok=True)

    if not cfg.dry_run:
        # Open Live first so the user can see it come to foreground.
        # (open empty bring-to-front; ALS open happens per-loop.)
        _activate_live()
        time.sleep(1.0)

    rows: list[ManifestRow] = []
    n_ok = 0
    n_fail = 0
    t0 = time.monotonic()
    for i, als_path in enumerate(als_files, start=1):
        preset_id = als_path.stem
        print(f"[{i:3d}/{len(als_files)}] {preset_id}", flush=True)
        try:
            row = _render_one(als_path, preset_id, cfg)
        except Exception as e:  # noqa: BLE001 — surface every failure type
            row = ManifestRow(
                preset_id=preset_id,
                als_path=str(als_path),
                als_sha1="",
                wav_path="",
                wav_sha1="",
                duration_seconds=0.0,
                clip_beats=0.0,
                tempo_bpm=cfg.tempo,
                bytes_written=0,
                status="exception",
                error=f"{type(e).__name__}: {e}",
            )
        rows.append(row)
        if row.status in ("ok", "dry_run"):
            n_ok += 1
            print(f"    OK   {row.duration_seconds:5.2f}s clip, "
                  f"{row.bytes_written} bytes WAV")
        else:
            n_fail += 1
            print(f"    FAIL {row.status}  {row.error}", file=sys.stderr)

    elapsed = time.monotonic() - t0

    # Write manifest.
    manifest = {
        "config": {
            "als_dir": str(cfg.als_dir),
            "audio_dir": str(cfg.audio_dir),
            "staging": str(cfg.staging),
            "tempo_bpm": cfg.tempo,
            "tail_buffer_s": cfg.tail_buffer_s,
            "load_settle_s": cfg.load_settle_s,
        },
        "summary": {
            "n_total": len(rows),
            "n_ok": n_ok,
            "n_fail": n_fail,
            "elapsed_seconds": round(elapsed, 1),
        },
        "rows": [row.__dict__ for row in rows],
    }
    cfg.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.manifest_path.write_text(json.dumps(manifest, indent=2))

    print()
    print(f"Rendered {n_ok}/{len(rows)} OK, {n_fail} fail in {elapsed:.1f}s")
    print(f"Manifest -> {cfg.manifest_path}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
