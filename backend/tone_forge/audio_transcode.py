"""ffmpeg-based audio transcoding for the mobile bundle path.

The mobile app is going to be on cellular half the time. A 4-minute
stereo WAV at 44.1 kHz is ~40 MB; the same content re-encoded to
AAC-LC at 256 kbps in an M4A container is ~8 MB. Four stems per song
means ~130 MB → ~30 MB per bundle — the difference between "download
before the flight starts boarding" and "download during the flight".

This module is a thin, best-effort wrapper around ``ffmpeg``:
  - ``is_ffmpeg_available()`` — cached ``shutil.which`` check.
  - ``transcode_to_m4a(wav_path)`` — synchronous, returns a Path to the
    transcoded file, or ``None`` if ffmpeg is missing or the encode
    fails. Callers fall back to uploading the original WAV.

Caching:
  ffmpeg output files are placed in a system tempdir under
  ``toneforge_m4a/`` with a filename that encodes the source path +
  mtime. So a re-run of the same analysis (same stem paths, same mtime)
  reuses the cached m4a. If the WAV is regenerated the mtime changes
  and we transcode fresh.

Choices we're not exposing as knobs:
  - Codec: AAC-LC. Widely supported by AVAudioEngine, well-tuned
    encoders, cheap to decode on-device.
  - Bitrate: 256 kbps. Overkill for casual stems, but the tone-forge
    workflow includes hearing artefacts in stem separation; going
    lower (192 kbps) started to muddy transient detail in bench
    tests.
  - Container: M4A. Same MP4 payload iOS treats as first-class.
  - Sample rate: preserve source. Downsampling for size is not worth
    the audible cost when the file's still going to be tens of MB.
"""

from __future__ import annotations

import functools
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


AAC_BITRATE = "256k"
_CACHE_SUBDIR = "toneforge_m4a"


@functools.lru_cache(maxsize=1)
def is_ffmpeg_available() -> bool:
    """True iff ``ffmpeg`` is on PATH.

    Cached because ``shutil.which`` walks PATH; the answer doesn't
    change while a process is running.
    """
    return shutil.which("ffmpeg") is not None


def _cache_dir() -> Path:
    p = Path(tempfile.gettempdir()) / _CACHE_SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cache_key(src: Path) -> str:
    """Deterministic hash of (absolute path, mtime_ns, size).

    Any of the three changing invalidates the cache. We hash rather
    than embedding the raw path in the filename so long analysis paths
    don't blow up on filesystems with tight name limits.
    """
    st = src.stat()
    payload = f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode()
    return hashlib.sha1(payload).hexdigest()[:16]


def cached_m4a_path(src: Path) -> Path:
    """Return the cache path we would use for ``src`` (may not exist)."""
    return _cache_dir() / f"{src.stem}_{_cache_key(src)}.m4a"


def transcode_to_m4a(src_path: str | Path) -> Optional[Path]:
    """Transcode ``src_path`` (any format ffmpeg reads) to AAC-LC M4A.

    Returns the cached output path on success; ``None`` on any failure
    (ffmpeg missing, source missing, non-zero exit). Idempotent: reuses
    the cached file when source path + mtime + size match a prior run.
    """
    if not is_ffmpeg_available():
        return None
    src = Path(src_path)
    if not src.is_file():
        return None

    dst = cached_m4a_path(src)
    if dst.is_file() and dst.stat().st_size > 0:
        return dst

    # Write to a temp file next to the final path, then atomic rename
    # so a crashed transcode doesn't leave a truncated cache entry
    # that later runs will happily reuse.
    tmp_dst = dst.with_suffix(".m4a.partial")
    cmd = [
        "ffmpeg",
        "-y",                  # overwrite tmp_dst if present
        "-loglevel", "error",
        "-nostdin",
        "-i", str(src),
        "-vn",                 # no video (belt-and-braces)
        "-c:a", "aac",
        "-b:a", AAC_BITRATE,
        "-movflags", "+faststart",  # moov atom at the front for HTTP range
        # We're writing to `.m4a.partial` so ffmpeg can't infer the muxer
        # from the extension. `-f ipod` is the ffmpeg name for the m4a
        # profile (MP4 container, AAC audio, no video) — matches what iOS
        # AVAudioEngine treats as first-class.
        "-f", "ipod",
        str(tmp_dst),
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            timeout=300,       # 5 min hard cap; a 10-min stem finishes in <30s
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Log the tail of stderr so ops can diagnose without a repro.
        stderr = getattr(exc, "stderr", b"") or b""
        tail = stderr.decode("utf-8", errors="replace")[-500:]
        print(f"[audio_transcode] ffmpeg failed for {src}: {tail.strip()}")
        try:
            tmp_dst.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    except FileNotFoundError:
        # ffmpeg vanished between the which() check and the exec — very
        # unlikely in practice, but a shutil.which cache means we could
        # get here after a PATH change. Treat as unavailable.
        return None

    try:
        tmp_dst.replace(dst)
    except OSError as exc:
        print(f"[audio_transcode] rename failed for {tmp_dst} → {dst}: {exc}")
        return None
    return dst
