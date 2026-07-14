"""Best-effort title/artist tag extraction from uploaded audio.

mutagen's easy mode covers MP3/ID3, MP4/M4A, FLAC and OGG. WAVs from
the iOS transcode carry no tags — the mobile client sends title/artist
as form fields instead (see ImportCoordinator). Attribution metadata is
strictly best-effort: nothing here may ever fail an analysis, so every
path degrades to an empty result instead of raising.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

#: Tag values feed shared /jam/{id} pages and mobile bundles — cap and
#: strip so hostile/garbage tags can't smuggle control chars or novels.
_MAX_TAG_LEN = 200
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_tag(value) -> str:
    """Trim, strip control characters, cap length. Non-strings -> ''."""
    if not isinstance(value, str):
        return ""
    return _CONTROL_RE.sub("", value).strip()[:_MAX_TAG_LEN].strip()


def read_tags(path: Union[str, Path]) -> dict:
    """Return ``{"title": ..., "artist": ...}`` for the tags present.

    Keys are only included when a non-empty sanitized value exists.
    Missing mutagen, unreadable files, tag-less formats and corrupt
    containers all return ``{}``.
    """
    try:
        import mutagen

        audio = mutagen.File(str(path), easy=True)
    except Exception:  # noqa: BLE001 — tag junk must never fail analysis
        logger.debug("tag read failed for %s", path, exc_info=True)
        return {}
    if audio is None or not getattr(audio, "tags", None):
        return {}
    out: dict = {}
    for key in ("title", "artist"):
        try:
            values = audio.tags.get(key) or []
        except Exception:  # noqa: BLE001
            values = []
        value = sanitize_tag(values[0] if values else "")
        if value:
            out[key] = value
    return out
