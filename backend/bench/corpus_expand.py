"""Corpus-expansion runner — Phase 0D unblocker.

Bypass uvicorn watcher, force deep analysis, persist bundles, fail
loudly when stems are missing. The goal is a reproducible path for
Phase 0C corpus calibration work that does not go through the HTTP
endpoint (which is reload-prone in dev) and does not depend on a
client correctly negotiating ``fast_mode=False``.

Usage
-----
    # YouTube URL
    python -m bench.corpus_expand --url "https://www.youtube.com/watch?v=..." \\
        --label "stairway" --duration 60

    # Local audio file
    python -m bench.corpus_expand --file /path/to/song.wav --label "stairway"

Exit codes
----------
    0   success — stems_paths populated, history persisted, bundle id printed
    2   download / load failure
    3   analysis raised
    4   analysis returned but stems_paths is empty (the Phase 0D failure mode)

Design notes
------------
* Calls ``UnifiedPipeline.analyze`` directly. No FastAPI app instance,
  no uvicorn reload watcher, no ``fast_mode`` ladder.
* Forces ``PipelineConfig.deep()`` unconditionally. The whole point of
  this runner is to be the calibration-grade path.
* Writes history via the same ``history.json`` file the HTTP endpoint
  uses so the JAM/UI can load corpus bundles by id.
* Emits a JSON line on stdout at the end with ``bundle_id``,
  ``stems_paths``, ``section_count`` so a downstream driver can pipe
  it into ``song_form_phase0c`` work.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Make sure ``backend/`` is on sys.path so this script can be run
# both as ``python -m bench.corpus_expand`` from backend/ and as a
# direct ``python backend/bench/corpus_expand.py`` from the repo root.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tone_forge.unified_pipeline import (  # noqa: E402
    PipelineConfig,
    UnifiedPipeline,
)


# History file is shared with the HTTP endpoint so corpus bundles
# appear in the regular UI history list.
_HISTORY_FILE = _BACKEND / "data" / "history.json"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


logger = logging.getLogger("corpus_expand")


# ---------------------------------------------------------------------------
# History persistence (mirrors tone_forge_api._add_to_history)
# ---------------------------------------------------------------------------

def _load_history() -> list[dict]:
    if not _HISTORY_FILE.exists():
        return []
    try:
        with open(_HISTORY_FILE) as fh:
            return json.load(fh)
    except Exception:
        return []


def _save_history(history: list[dict]) -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Use the same encoder the HTTP path uses so numpy types round-trip.
    from tone_forge_api import NumpyJSONEncoder  # type: ignore
    with open(_HISTORY_FILE, "w") as fh:
        json.dump(history, fh, indent=2, cls=NumpyJSONEncoder)


def _add_to_history(entry: dict, full_result: dict) -> dict:
    history = _load_history()
    entry["id"] = str(uuid.uuid4())[:8]
    entry["timestamp"] = datetime.now().isoformat()
    entry["result"] = full_result
    history.insert(0, entry)
    history = history[:100]
    _save_history(history)
    return entry


# ---------------------------------------------------------------------------
# Source acquisition
# ---------------------------------------------------------------------------

def _download_youtube(url: str, output_dir: Path, duration: int) -> Tuple[Path, str]:
    """Download audio with yt-dlp. Returns (path, display_name).

    Mirrors the production helper but is dependency-light: no caching,
    no timestamp parsing — corpus expansion wants a clean repeatable
    pull, not a UX-tuned download.
    """
    output_template = str(output_dir / "%(title).50s.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--extract-audio",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "--no-playlist",
        "--download-sections", f"*0-{duration}",
        "--output", output_template,
        url,
    ]
    logger.info("Downloading %s (duration=%ss)", url, duration)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        # Try a fallback without section limit (some videos refuse it).
        logger.warning("yt-dlp section download failed; retrying without section limit")
        cmd_fallback = [c for c in cmd if not c.startswith("*")]
        # Remove --download-sections + its arg
        try:
            i = cmd_fallback.index("--download-sections")
            del cmd_fallback[i : i + 2]
        except ValueError:
            pass
        proc = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {proc.stderr.strip()}")

    wav_files = list(output_dir.glob("*.wav"))
    if not wav_files:
        raise RuntimeError("yt-dlp produced no .wav output")
    audio_path = wav_files[0]
    return audio_path, audio_path.stem


# ---------------------------------------------------------------------------
# Pipeline driver
# ---------------------------------------------------------------------------

async def _run_pipeline_deep(
    audio_path: Path, display_name: str
) -> Tuple[Dict[str, Any], "PipelineConfig"]:
    """Run UnifiedPipeline with PipelineConfig.deep(). No HTTP, no ladder."""
    config = PipelineConfig.deep()
    config.source_name = display_name

    pipeline = UnifiedPipeline()
    result = await pipeline.analyze(audio_path, config)
    if result is None:
        raise RuntimeError("UnifiedPipeline.analyze returned None")

    # Convert to the same dict shape the HTTP endpoint persists.
    payload = result.to_dict()
    payload["analysis_mode"] = config.mode.value
    return payload, config


# ---------------------------------------------------------------------------
# Stem verification
# ---------------------------------------------------------------------------

# Expected stems for any full-mix corpus song. htdemucs_6s emits
# {drums, bass, vocals, other, guitar, piano}; the 4-stem fallback
# emits {drums, bass, vocals, other}. We require the 4-stem floor
# because that's the minimum the Phase 0C signals (D = vocal RMS,
# F = drum density) consume.
_REQUIRED_STEMS = frozenset({"drums", "bass", "vocals", "other"})


def _verify_stems(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, str]], list[str]]:
    """Return (stems_paths, missing). missing is the names absent from disk."""
    # ``stems`` is the canonical field; ``stems_paths`` is the alias.
    stems_paths = payload.get("stems_paths") or payload.get("stems") or None
    if not stems_paths:
        return None, sorted(_REQUIRED_STEMS)

    missing: list[str] = []
    for name in _REQUIRED_STEMS:
        path_str = stems_paths.get(name)
        if not path_str:
            missing.append(name)
            continue
        # If the path is a stem-serve URL we can't stat, treat presence as OK.
        if path_str.startswith(("/api/", "http://", "https://")):
            continue
        if not Path(path_str).exists():
            missing.append(name)
    return stems_paths, missing


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="corpus_expand",
        description="Force-deep corpus-expansion runner (Phase 0D).",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="YouTube URL to download and analyze")
    src.add_argument("--file", help="Local audio file path to analyze")
    p.add_argument(
        "--label",
        default=None,
        help="Optional label for the history entry (defaults to source name)",
    )
    p.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Seconds to download for --url (default 60)",
    )
    p.add_argument(
        "--keep-temp",
        action="store_true",
        help="Don't delete the download tempdir on exit (debugging)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    _configure_logging(args.verbose)

    # Acquire source
    tmp_dir: Optional[Path] = None
    try:
        if args.url:
            tmp_dir = Path(tempfile.mkdtemp(prefix="toneforge_corpus_"))
            try:
                audio_path, display_name = _download_youtube(
                    args.url, tmp_dir, args.duration
                )
            except Exception as exc:
                logger.error("Download failed: %s", exc)
                return 2
        else:
            audio_path = Path(args.file).expanduser().resolve()
            if not audio_path.exists():
                logger.error("Audio file not found: %s", audio_path)
                return 2
            display_name = audio_path.stem

        label = args.label or display_name

        # Run pipeline (deep, no ladder)
        logger.info("Analyzing %s in DEEP mode (forced)", audio_path)
        try:
            payload, config = asyncio.run(_run_pipeline_deep(audio_path, display_name))
        except Exception as exc:
            logger.exception("Analysis raised: %s", exc)
            return 3

        # Verify stems before persisting — corpus expansion has no
        # business writing a bundle the calibration code can't consume.
        stems_paths, missing = _verify_stems(payload)
        section_count = len(payload.get("sections") or [])

        if missing:
            # Emit a structured failure record so the operator can see
            # exactly which stems were absent.
            failure = {
                "status": "stems_missing",
                "label": label,
                "audio_path": str(audio_path),
                "detected_type": payload.get("detected_type"),
                "section_count": section_count,
                "stems_paths": stems_paths,
                "missing_stems": missing,
                "mode": config.mode.value,
            }
            logger.error(
                "Stems incomplete after deep analysis: missing=%s", missing
            )
            print(json.dumps(failure, indent=2))
            return 4

        # Persist to history (same store the JAM UI loads from)
        entry = _add_to_history(
            {
                "name": label,
                "detected_type": payload.get("detected_type", "unknown"),
                "summary": (payload.get("detection") or {}).get("summary", ""),
                "duration": payload.get("duration_sec"),
                "source_url": args.url if args.url else None,
                "corpus_run": True,
            },
            full_result=payload,
        )

        success = {
            "status": "ok",
            "bundle_id": entry["id"],
            "label": label,
            "detected_type": payload.get("detected_type"),
            "section_count": section_count,
            "stems_paths": stems_paths,
            "mode": config.mode.value,
        }
        # The single source-of-truth output: one JSON object on stdout.
        print(json.dumps(success, indent=2))
        logger.info("Bundle persisted: id=%s sections=%d", entry["id"], section_count)
        return 0

    finally:
        if tmp_dir and not args.keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
