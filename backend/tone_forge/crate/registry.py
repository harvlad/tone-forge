"""Crate registry — load and serve the shared, read-only donor catalog.

Storage layout (rooted at ``TONEFORGE_CRATE_DIR``, default
``backend/data/crate/``):

    registry.json            list of CrateTrack dicts — the SEARCHABLE
                             manifest, kept small (no chords/graph).
    licenses/<id>.json       one CrateLicenseRecord per track — the CC
                             provenance, auditable independently of the
                             manifest and the source of truth for the
                             "prove every track is clean" audit.
    analysis/<id>.json       the FULL stored analysis blob per track
                             (detected_key, chords, sections, downbeats_s,
                             stems_paths → R2, AND performance_graph). This
                             is what ``to_borrow_entry`` hands to the borrow
                             ranker / render path.

Why separate from a user's own songs (data/history.json): the crate is
GLOBAL (no owner_id), immutable/curated, and license-bearing, whereas user
songs are owner/device-scoped, uploaded/deletable, and license-empty. The
retention/delete path (which purges history entries) must never touch the
crate, and the crate is never owner-filtered — every user sees the same
pool. Hence a distinct file tree and a distinct (un-scoped, read-only)
loader. The manifest is cached in-process and invalidated by mtime, exactly
like the history cache but without the R2/ownership machinery.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tone_forge.contracts import (
    CrateFeatures,
    CrateLicense,
    CrateLicenseRecord,
    CrateTrack,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# ``__file__`` = backend/tone_forge/crate/registry.py → parents[2] = backend/.
_DEFAULT_CRATE_DIR = Path(__file__).resolve().parents[2] / "data" / "crate"


def crate_dir() -> Path:
    """The crate storage root. ``TONEFORGE_CRATE_DIR`` overrides the default
    so tests point at a tmp fixture tree and prod/dev share the committed
    seed under ``backend/data/crate``."""
    raw = os.environ.get("TONEFORGE_CRATE_DIR")
    return Path(raw) if raw else _DEFAULT_CRATE_DIR


def _registry_file() -> Path:
    return crate_dir() / "registry.json"


def _license_file(track_id: str) -> Path:
    return crate_dir() / "licenses" / f"{_safe_id(track_id)}.json"


def _analysis_file(track_id: str) -> Path:
    return crate_dir() / "analysis" / f"{_safe_id(track_id)}.json"


def _safe_id(track_id: str) -> str:
    """A crate id (``crate:jamendo:123456``) → a filesystem-safe stem. The
    colons that namespace the id are not path-safe on every OS, so sidecar
    filenames replace them; the manifest keeps the real id."""
    return track_id.replace(":", "_").replace("/", "_")


# ---------------------------------------------------------------------------
# License-preference / encumbrance helpers
# ---------------------------------------------------------------------------

# CC0 > CC-BY > (avoid) CC-BY-SA. A ranking helper only — never a field. The
# lower the rank, the more preferred (fewer obligations, no copyleft).
_LICENSE_RANK: Dict[CrateLicense, int] = {
    CrateLicense.CC0: 0,
    CrateLicense.CC_BY: 1,
    CrateLicense.CC_BY_SA: 2,
}


def license_rank(license_id: CrateLicense) -> int:
    """Preference rank (0 = most preferred). Used to break ties toward the
    least-encumbered track when two match equally well."""
    return _LICENSE_RANK.get(license_id, 99)


def encumbered_for(license_id: CrateLicense) -> bool:
    """Whether a license copyleft-contaminates a user's exported remix.

    TRUE only for CC-BY-SA (ShareAlike). This is the single derivation
    point; the DTO stores the resolved boolean so the export/UI path never
    re-derives from ``license_id``."""
    return license_id == CrateLicense.CC_BY_SA


# ---------------------------------------------------------------------------
# (De)serialization — dataclasses <-> plain dicts (JSON)
# ---------------------------------------------------------------------------


def license_from_dict(d: Dict) -> CrateLicenseRecord:
    """Parse a CrateLicenseRecord from a manifest/sidecar dict. The license
    id accepts either the enum value (``"CC-BY-4.0"``) or a loose alias
    (``"CC-BY"``, the catalog.json form) so the D-024 catalog rows and the
    crate manifest share a shape."""
    return CrateLicenseRecord(
        license_id=_parse_license(d.get("license_id") or d.get("license")),
        license_url=str(d.get("license_url") or ""),
        attribution=str(d.get("attribution") or ""),
        source=str(d.get("source") or ""),
        source_track_id=str(d.get("source_track_id") or ""),
        source_url=str(d.get("source_url") or ""),
        content_hash=str(d.get("content_hash") or ""),
        acquired_at=str(d.get("acquired_at") or ""),
        export_encumbered=bool(d.get("export_encumbered", False)),
    )


def _parse_license(raw) -> CrateLicense:
    """Resolve a license spelling to the coarse CrateLicense the crate models.

    The enum only distinguishes CC0 / CC-BY / CC-BY-SA because those are the
    axes that matter for admission + export encumbrance; the exact CC *version*
    (2.5 / 3.0 / 4.0) is not modelled — ccMixter is mostly CC-BY-3.0 (one
    CC-BY-2.5), Jamendo CC-BY-3.0, Kevin MacLeod CC-BY-4.0, and all three are
    plain attribution, so every "CC-BY-x.y" collapses to CC_BY. Matching is
    version-agnostic and, critically, tests ShareAlike BEFORE plain attribution
    so a "CC-BY-SA-3.0" is never misfiled as unencumbered CC-BY (that would drop
    the copyleft obligation and let it ship in a "clean export").
    """
    if isinstance(raw, CrateLicense):
        return raw
    s = str(raw or "").strip().upper().replace(" ", "")
    if s in ("CC0", "CC-0", "PUBLICDOMAIN") or s.startswith("CC0"):
        return CrateLicense.CC0
    # ShareAlike first — "CC-BY-SA-4.0" contains "CC-BY" and would otherwise
    # match the attribution branch below. Any x.y spelling counts.
    if "BY-SA" in s:
        return CrateLicense.CC_BY_SA
    # Any attribution-only spelling: CC-BY, CC-BY-2.5, CC-BY-3.0, CC-BY-4.0.
    # The bare/ambiguous "CC-BY" defaults here too (plain attribution).
    return CrateLicense.CC_BY


def _features_from_dict(d: Dict) -> CrateFeatures:
    d = d or {}
    ts = d.get("time_signature") or (4, 4)
    try:
        ts_t: Tuple[int, int] = (int(ts[0]), int(ts[1]))
    except (TypeError, ValueError, IndexError):
        ts_t = (4, 4)
    return CrateFeatures(
        tempo_bpm=float(d.get("tempo_bpm") or 0.0),
        tempo_confidence=float(d.get("tempo_confidence") or 0.0),
        detected_key=d.get("detected_key"),
        key_confidence=float(d.get("key_confidence") or 0.0),
        duration_s=float(d.get("duration_s") or 0.0),
        section_count=int(d.get("section_count") or 0),
        time_signature=ts_t,
        energy=float(d.get("energy") or 0.0),
        energy_profile=tuple(float(x) for x in (d.get("energy_profile") or ())),
        available_stems=tuple(str(x) for x in (d.get("available_stems") or ())),
        instrumentation=tuple(str(x) for x in (d.get("instrumentation") or ())),
        has_vocals=bool(d.get("has_vocals", False)),
        loudness_lufs=(None if d.get("loudness_lufs") is None
                       else float(d["loudness_lufs"])),
        spectral_centroid=(None if d.get("spectral_centroid") is None
                           else float(d["spectral_centroid"])),
        pc_histogram=tuple(float(x) for x in (d.get("pc_histogram") or ())),
        melody_register=(None if d.get("melody_register") is None
                         else int(d["melody_register"])),
        melody_confidence=float(d.get("melody_confidence") or 0.0),
    )


def track_from_dict(d: Dict) -> CrateTrack:
    """Parse a CrateTrack from a manifest row. Lenient — missing optional
    fields fall back to their DTO defaults so a partial analysis parses."""
    lic = d.get("license")
    return CrateTrack(
        id=str(d["id"]),
        title=str(d.get("title") or ""),
        artist=str(d.get("artist") or ""),
        license=license_from_dict(lic if isinstance(lic, dict) else {}),
        features=_features_from_dict(d.get("features") if isinstance(
            d.get("features"), dict) else {}),
        album=str(d.get("album") or ""),
        year=(None if d.get("year") is None else int(d["year"])),
        genre=str(d.get("genre") or ""),
        subgenres=tuple(str(x) for x in (d.get("subgenres") or ())),
        tags=tuple(str(x) for x in (d.get("tags") or ())),
        mood=str(d.get("mood") or ""),
        stem_asset_base=str(d.get("stem_asset_base") or ""),
        preview_url=d.get("preview_url"),
        graph_available=bool(d.get("graph_available", False)),
    )


def license_to_dict(rec: CrateLicenseRecord) -> Dict:
    return {
        "license_id": rec.license_id.value,
        "license_url": rec.license_url,
        "attribution": rec.attribution,
        "source": rec.source,
        "source_track_id": rec.source_track_id,
        "source_url": rec.source_url,
        "content_hash": rec.content_hash,
        "acquired_at": rec.acquired_at,
        "export_encumbered": rec.export_encumbered,
    }


def features_to_dict(f: CrateFeatures) -> Dict:
    return {
        "tempo_bpm": f.tempo_bpm,
        "tempo_confidence": f.tempo_confidence,
        "detected_key": f.detected_key,
        "key_confidence": f.key_confidence,
        "duration_s": f.duration_s,
        "section_count": f.section_count,
        "time_signature": list(f.time_signature),
        "energy": f.energy,
        "energy_profile": list(f.energy_profile),
        "available_stems": list(f.available_stems),
        "instrumentation": list(f.instrumentation),
        "has_vocals": f.has_vocals,
        "loudness_lufs": f.loudness_lufs,
        "spectral_centroid": f.spectral_centroid,
        "pc_histogram": list(f.pc_histogram),
        "melody_register": f.melody_register,
        "melody_confidence": f.melody_confidence,
    }


def track_to_dict(t: CrateTrack) -> Dict:
    """Serialize a CrateTrack to a manifest/API row. Also the summary shape
    the candidates/search endpoints return (attribution rides along for the
    mandatory CC-BY credit display)."""
    return {
        "id": t.id,
        "title": t.title,
        "artist": t.artist,
        "album": t.album,
        "year": t.year,
        "genre": t.genre,
        "subgenres": list(t.subgenres),
        "tags": list(t.tags),
        "mood": t.mood,
        "license": license_to_dict(t.license),
        "features": features_to_dict(t.features),
        "stem_asset_base": t.stem_asset_base,
        "preview_url": t.preview_url,
        "graph_available": t.graph_available,
        # Convenience mirrors for clients that only render a row: the
        # attribution string and the one encumbrance boolean, so a UI never
        # has to reach into the nested license record.
        "attribution": t.license.attribution,
        "licenseId": t.license.license_id.value,
        "exportEncumbered": t.license.export_encumbered,
    }


# ---------------------------------------------------------------------------
# Loading — cached, mtime-invalidated, read-only, un-scoped
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
# (mtime_ns, path_str) → parsed tracks. Keyed on the resolved registry path
# too so a test that flips TONEFORGE_CRATE_DIR mid-process isn't served a
# stale cache from the previous dir.
_cache: Optional[Tuple[float, str, List[CrateTrack]]] = None
_blob_cache: Dict[str, Tuple[float, Dict]] = {}


def load_crate() -> List[CrateTrack]:
    """The shared crate catalog as parsed CrateTracks, newest-manifest-first
    order preserved. Empty list when no registry exists (dev box without the
    seed). Cached in-process; the cache busts when registry.json's mtime or
    the resolved crate dir changes."""
    path = _registry_file()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return []
    key = str(path)
    global _cache
    with _LOCK:
        if _cache is not None and _cache[0] == mtime and _cache[1] == key:
            return _cache[2]
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(rows, list):
        rows = rows.get("tracks", []) if isinstance(rows, dict) else []
    tracks: List[CrateTrack] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            tracks.append(track_from_dict(row))
        except Exception:
            # One malformed row must not sink the whole crate — skip it, the
            # ingest-side validation is where completeness is enforced.
            continue
    with _LOCK:
        _cache = (mtime, key, tracks)
    return tracks


def get_track(track_id: str) -> Optional[CrateTrack]:
    for t in load_crate():
        if t.id == track_id:
            return t
    return None


def license_record(track_id: str) -> Optional[CrateLicenseRecord]:
    """The authoritative license sidecar for a track (the compliance
    artifact). Falls back to the manifest's embedded copy when the sidecar
    is absent."""
    path = _license_file(track_id)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return license_from_dict(d)
    except Exception:
        t = get_track(track_id)
        return t.license if t else None


def analysis_blob(track_id: str) -> Optional[Dict]:
    """The FULL stored analysis blob for a crate track (detected_key, chords,
    sections, downbeats_s, stems_paths, performance_graph, melody). This is
    the donor ``result`` the borrow ranker/render path consumes. Cached by
    mtime so repeated candidate scoring doesn't re-read the file."""
    path = _analysis_file(track_id)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = _blob_cache.get(track_id)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(blob, dict):
        return None
    _blob_cache[track_id] = (mtime, blob)
    return blob


def to_borrow_entry(track: CrateTrack,
                    result_blob: Optional[Dict] = None) -> Optional[Dict]:
    """Bridge a CrateTrack to the generic borrow donor "entry" shape
    ``{id, name, result}`` that ``borrow.borrow_candidates`` /
    ``borrow.kit_borrow_job`` consume unchanged. ``result_blob`` is the
    track's stored analysis (loaded from ``analysis/<id>.json`` when not
    passed). Returns None when the analysis blob is missing — a track with
    no stored graph/result is un-rankable and un-renderable on prod, and the
    caller drops it rather than surfacing an empty pad set."""
    blob = result_blob if result_blob is not None else analysis_blob(track.id)
    if not isinstance(blob, dict) or not blob:
        return None
    return {"id": track.id, "name": track.title, "result": blob}


def invalidate_cache() -> None:
    """Drop the in-process manifest + blob caches. For tests that rewrite the
    fixture tree in place (same mtime granularity) and for an operator who
    re-runs ingestion against a live process."""
    global _cache
    with _LOCK:
        _cache = None
    _blob_cache.clear()
