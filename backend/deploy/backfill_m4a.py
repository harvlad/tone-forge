"""One-time backfill: transcode existing R2 WAV stems to AAC-LC M4A.

Production songs analyzed before ffmpeg was live on the VPS were
uploaded to R2 as raw WAV. The bundle endpoint's lazy transcode
(`_maybe_upload_stems_to_r2`) skips already-remote URLs, so those songs
stay WAV forever and cold-load slowly (~160 MB/song vs ~30 MB as m4a).

This script walks history, and for every stem whose URL is a remote R2
WAV object:
  1. If the sibling `.m4a` object already exists, just rewrite the URL.
  2. Otherwise download the WAV from R2, transcode to m4a, upload the
     m4a object, and rewrite `stems_paths[role]` to the m4a URL.

History is persisted back via the same store the API uses (R2 +
local), so a service restart afterwards picks up the new URLs cleanly.

Idempotent: re-running only touches stems still pointing at WAV.
Run:
    sudo -u toneforge bash -c \
      'set -a; . /opt/toneforge/.env; set +a; \
       /opt/toneforge/venv/bin/python /opt/toneforge/backend/deploy/backfill_m4a.py'
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tone_forge import r2_storage, audio_transcode

_HISTORY_FILE = Path(__file__).resolve().parent.parent / "data" / "history.json"


def _load_history() -> list:
    if r2_storage.is_configured():
        h = r2_storage.load_history()
        if h is not None:
            return h
    if _HISTORY_FILE.exists():
        with open(_HISTORY_FILE) as f:
            return json.load(f)
    return []


def _save_history(history: list) -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, default=str)
    if r2_storage.is_configured():
        ok = r2_storage.save_history(history)
        print(f"[backfill] R2 save_history: {ok}")


def _download_wav(key: str, dest: str) -> bool:
    try:
        client = r2_storage._client()  # noqa: SLF001 (one-time tool)
        client.download_file(Bucket=r2_storage.bucket_name(), Key=key, Filename=dest)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[backfill] download failed for {key}: {exc}")
        return False


def main() -> None:
    if not r2_storage.is_configured():
        print("[backfill] R2 not configured; nothing to do.")
        return
    if not audio_transcode.is_ffmpeg_available():
        print("[backfill] ffmpeg unavailable; aborting.")
        return

    history = _load_history()
    print(f"[backfill] {len(history)} history entries")
    changed = False

    for entry in history:
        aid = entry.get("id")
        result = entry.get("result")
        if not aid or not isinstance(result, dict):
            continue
        stems = result.get("stems_paths")
        if not isinstance(stems, dict) or not stems:
            continue

        for role, url in list(stems.items()):
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            # Only remote WAV objects need backfilling.
            path_part = url.split("?", 1)[0]
            if not path_part.lower().endswith(".wav"):
                continue

            m4a_key = r2_storage.stem_key(aid, role, "m4a")
            wav_key = r2_storage.stem_key(aid, role, "wav")

            # Already transcoded on a prior run — just repoint.
            if r2_storage.object_exists(m4a_key):
                new_url = r2_storage.public_url_for(m4a_key)
                if new_url and new_url != url:
                    stems[role] = new_url
                    changed = True
                    print(f"[backfill] {aid}/{role}: repoint to existing m4a")
                continue

            tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
            try:
                if not _download_wav(wav_key, tmp_wav):
                    continue
                m4a = audio_transcode.transcode_to_m4a(tmp_wav)
                if not m4a:
                    print(f"[backfill] {aid}/{role}: transcode failed, kept WAV")
                    continue
                new_url = r2_storage.upload_stem(str(m4a), aid, role)
                if new_url:
                    stems[role] = new_url
                    changed = True
                    wav_mb = os.path.getsize(tmp_wav) / 1e6
                    m4a_mb = os.path.getsize(m4a) / 1e6
                    print(
                        f"[backfill] {aid}/{role}: "
                        f"{wav_mb:.1f}MB WAV -> {m4a_mb:.1f}MB m4a"
                    )
            finally:
                try:
                    os.unlink(tmp_wav)
                except OSError:
                    pass

    if changed:
        _save_history(history)
        print("[backfill] history updated.")
    else:
        print("[backfill] nothing to change.")


if __name__ == "__main__":
    main()
