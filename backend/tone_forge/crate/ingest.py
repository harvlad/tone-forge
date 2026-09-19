"""Crate ingestion pipeline — download → license sidecar → analyze →
blind-gate → merge → register.

CODE ONLY. This build does NOT pull live tracks; the operator runs the batch
on the VPS (data-ops run on the VPS, never on the Mac). The heavy, machine-
specific pieces — the actual HTTP download and the real analysis engine
(``unified_pipeline``) — are INJECTED as callables so this module stays a pure
orchestrator over ``contracts`` + ``borrow`` and the subsystem boundary check
keeps holding. ``backend/scripts/ingest_crate.py`` wires the real download +
analyzer and calls ``ingest_track`` here.

Admission doctrine (mirrors the repo's blind-gate rule — promotion is on real
audio, never metadata):

  1. Source allowlist — Jamendo / FMA / ccMixter only. Cambridge-MT is HARD-
     excluded (education-only; not commercially usable) even though the repo
     holds its catalog.
  2. Preference CC0 > CC-BY > (avoid) CC-BY-SA; BY-SA is admitted only with
     ``export_encumbered=True``.
  3. License completeness — title + artist + source_url + license_url +
     attribution — validated BEFORE analysis; an incomplete attribution is
     rejected and never ships (the license sidecar is the compliance artifact).
  4. Blind gate — admit on a REAL analysis result: the stored analysis must
     carry a persisted ``performance_graph`` (graph_available; the GPU-less
     prod box can only render from it) AND yield ≥1 usable curated-kit pad.
     A track whose stems separate to hiss is dropped regardless of its tags.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from tone_forge.contracts import (
    CrateFeatures,
    CrateLicense,
    CrateLicenseRecord,
    CrateTrack,
)
from tone_forge.crate import registry as _reg
from tone_forge.performance import borrow

# Only these platforms carry the CC license that authorizes self-hosting +
# export. Cambridge-MT is deliberately absent. ``incompetech`` (Kevin MacLeod,
# incompetech.com / filmmusic.io) is a first-party CC-BY-4.0 catalog — the
# artist self-licenses every piece attribution-only — so it clears the same
# self-host + export bar as the other three. The allowlist stays the hard
# compliance gate: a track whose source is not here never spends a GPU-minute.
SOURCE_ALLOWLIST = ("jamendo", "fma", "ccmixter", "incompetech")

_LICENSE_URLS = {
    CrateLicense.CC0: "https://creativecommons.org/publicdomain/zero/1.0/",
    CrateLicense.CC_BY: "https://creativecommons.org/licenses/by/4.0/",
    CrateLicense.CC_BY_SA: "https://creativecommons.org/licenses/by-sa/4.0/",
}


class IngestError(ValueError):
    """A track was rejected at ingest (bad source, incomplete license, or a
    failed blind gate). Never a soft-ship — the batch skips it and logs why."""


# ---------------------------------------------------------------------------
# License
# ---------------------------------------------------------------------------

_REQUIRED_LICENSE_FIELDS = ("title", "artist", "source_url",
                            "license_url", "attribution")


def validate_admission(meta: Dict) -> None:
    """Raise IngestError unless the track may be admitted: source on the
    allowlist and every license-completeness field present. Runs BEFORE any
    download/analysis so we never spend a GPU-minute on a track we can't ship.
    """
    source = str(meta.get("source") or "").strip().lower()
    if source not in SOURCE_ALLOWLIST:
        raise IngestError(
            f"source {source!r} not on the crate allowlist {SOURCE_ALLOWLIST} "
            "(Cambridge-MT is education-only and hard-excluded)")
    missing = [f for f in _REQUIRED_LICENSE_FIELDS if not str(meta.get(f) or "").strip()]
    if missing:
        raise IngestError(
            f"incomplete license/attribution for {meta.get('id')!r}: missing "
            f"{missing} — a CC-BY track without complete attribution never ships")


def build_license_record(meta: Dict, content_hash: str = "",
                         acquired_at: Optional[str] = None) -> CrateLicenseRecord:
    """Assemble the CrateLicenseRecord from ingest metadata. ``export_encumbered``
    is derived ONCE here (True for CC-BY-SA) and stored — the export/UI path
    reads the stored boolean, never re-derives."""
    lic = _reg._parse_license(meta.get("license") or meta.get("license_id"))
    return CrateLicenseRecord(
        license_id=lic,
        license_url=str(meta.get("license_url") or _LICENSE_URLS.get(lic, "")),
        attribution=str(meta.get("attribution") or ""),
        source=str(meta.get("source") or "").strip().lower(),
        source_track_id=str(meta.get("source_track_id") or meta.get("id") or ""),
        source_url=str(meta.get("source_url") or ""),
        content_hash=content_hash,
        acquired_at=acquired_at or datetime.now(timezone.utc).isoformat(),
        export_encumbered=_reg.encumbered_for(lic),
    )


# ---------------------------------------------------------------------------
# Features from the analysis result
# ---------------------------------------------------------------------------


def _aggregate_energy(result: Dict) -> float:
    ec = result.get("energy_curve")
    if isinstance(ec, list) and ec:
        vals = [float(x) for x in ec if isinstance(x, (int, float))]
        if vals:
            return max(0.0, min(1.0, sum(vals) / len(vals)))
    g = result.get(borrow_serve_graph_key())
    if isinstance(g, dict) and isinstance(g.get("phrases"), list):
        es = [float(p["energy"]) for p in g["phrases"]
              if isinstance(p, dict) and isinstance(p.get("energy"), (int, float))]
        if es:
            return max(0.0, min(1.0, sum(es) / len(es)))
    e = result.get("energy")
    return max(0.0, min(1.0, float(e))) if isinstance(e, (int, float)) else 0.0


def borrow_serve_graph_key() -> str:
    # The performance_graph result key, without importing serve just for a
    # constant. Kept in sync with performance.serve.GRAPH_RESULT_KEY.
    return "performance_graph"


def _instrumentation(result: Dict, stems: List[str]) -> List[str]:
    out: List[str] = []
    det = result.get("detection") or {}
    if isinstance(det, dict):
        for flag, label in (("is_guitar", "guitar"), ("is_bass", "bass"),
                            ("is_drums", "drums"), ("is_synth", "synth")):
            if det.get(flag):
                out.append(label)
    for s in stems:
        logical = borrow._logical_stem(s)
        if logical not in out:
            out.append(logical)
    return out


def features_from_result(result: Dict) -> CrateFeatures:
    """Project a stored analysis ``result`` into the searchable CrateFeatures
    (the analyzed-metadata union member). pc_histogram is precomputed here via
    borrow's folder so search/rank never re-fold the ribbon."""
    stems = list((result.get("stems_paths") or result.get("stems") or {}).keys())
    sections = result.get("sections") or []
    pc = borrow._song_pc_histogram(result) or []

    mel = result.get("melody") if isinstance(result.get("melody"), dict) else {}
    mel_notes = mel.get("notes") if isinstance(mel.get("notes"), list) else []
    mel_register: Optional[int] = None
    if mel_notes:
        ps = sorted(int(n["pitch"]) for n in mel_notes
                    if isinstance(n, dict) and isinstance(n.get("pitch"), (int, float)))
        if ps:
            mel_register = int(ps[len(ps) // 2])

    energy_profile = tuple(
        float(s.get("energy")) for s in sections
        if isinstance(s, dict) and isinstance(s.get("energy"), (int, float)))

    ts = result.get("time_signature")
    if isinstance(ts, (list, tuple)) and len(ts) == 2:
        try:
            time_sig = (int(ts[0]), int(ts[1]))
        except (TypeError, ValueError):
            time_sig = (4, 4)
    else:
        time_sig = (4, 4)

    return CrateFeatures(
        tempo_bpm=float(result.get("tempo_bpm") or result.get("tempo") or 0.0),
        tempo_confidence=float(result.get("tempo_confidence") or 0.0),
        detected_key=result.get("detected_key") or result.get("key"),
        key_confidence=float(result.get("detected_key_strength") or 0.0),
        duration_s=float(result.get("duration_sec") or result.get("duration_s") or 0.0),
        section_count=len(sections),
        time_signature=time_sig,
        energy=_aggregate_energy(result),
        energy_profile=energy_profile,
        available_stems=tuple(stems),
        instrumentation=tuple(_instrumentation(result, stems)),
        has_vocals=any(borrow._logical_stem(s) == "vocals" for s in stems),
        loudness_lufs=(float(result["loudness_lufs"])
                       if isinstance(result.get("loudness_lufs"), (int, float)) else None),
        spectral_centroid=(float(result["spectral_centroid"])
                           if isinstance(result.get("spectral_centroid"), (int, float)) else None),
        pc_histogram=tuple(float(x) for x in pc),
        melody_register=mel_register,
        melody_confidence=float(mel.get("confidence") or 0.0),
    )


# ---------------------------------------------------------------------------
# Blind gate
# ---------------------------------------------------------------------------


def graph_is_present(result: Dict) -> bool:
    """The hard admission requirement: a persisted performance_graph with
    assets. Without it the GPU-less prod box renders 0 pads forever."""
    g = result.get(borrow_serve_graph_key())
    return isinstance(g, dict) and bool(g.get("assets"))


def default_blind_gate(track_id: str, result: Dict) -> bool:
    """Admit on a REAL result: performance_graph present AND the curated kit
    yields ≥1 pad. Serve's kit builder already vetoes bad-separation pads
    (Phrase.flatness/collapse_ratio/parent_overlap), so a track whose stems
    separate to wash produces an empty kit and is dropped here."""
    if not graph_is_present(result):
        return False
    try:
        from tone_forge.performance import serve as _serve
        kit = _serve.kit_payload(track_id, result, pads=borrow._KIT_PADS_PER_SOURCE)
    except Exception:
        return False
    return bool(kit.get("pads"))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

Downloader = Callable[[str, Path], Path]   # (url, staging_dir) -> local file
Analyzer = Callable[[Path], Dict]          # local file -> stored analysis dict


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_track(meta: Dict, *, analyzer: Analyzer, downloader: Downloader,
                 crate_dir: Optional[Path] = None,
                 blind_gate: Callable[[str, Dict], bool] = default_blind_gate,
                 staging_dir: Optional[Path] = None,
                 persist: bool = True) -> CrateTrack:
    """Run one track through the pipeline and (optionally) register it.

    ``analyzer`` MUST return a stored analysis dict that carries a persisted
    ``performance_graph`` and, ideally, ``result["melody"]`` (the melody lane
    is not persisted by the default ``to_dict`` — the crate analyzer must
    materialize it so the match's melody term is live). Order of operations
    matches the doctrine: allowlist + license validation → download + hash →
    license sidecar → analyze → blind-gate → merge → write.

    Returns the assembled CrateTrack. Raises IngestError on any rejection.
    """
    validate_admission(meta)
    root = Path(crate_dir) if crate_dir else _reg.crate_dir()
    stage = Path(staging_dir) if staging_dir else (root / "_staging")
    stage.mkdir(parents=True, exist_ok=True)

    # 1) DOWNLOAD + hash (dedupe + provenance). The audio lives at
    #    ``download_url``; ``source_url`` is the human track page kept for the
    #    license record's provenance (attribution links there, not the raw
    #    stream). Fall back to source_url so legacy manifests that only carry
    #    the one URL still work.
    src_url = str(meta.get("download_url") or meta.get("source_url") or "")
    local = downloader(src_url, stage)
    content_hash = _sha256_file(Path(local))

    # 2) LICENSE SIDECAR (written before analysis so provenance survives even
    #    if analysis fails on the box).
    lic = build_license_record(meta, content_hash=content_hash)
    track_id = str(meta["id"])
    if persist:
        _write_json(_reg._license_file(track_id), _reg.license_to_dict(lic))

    # 3) ANALYZE (real pipeline injected; must persist performance_graph +
    #    materialize the melody lane).
    result = analyzer(Path(local))
    if not isinstance(result, dict):
        raise IngestError(f"{track_id}: analyzer returned no result")

    # 4) BLIND-GATE ADMISSION — real audio, never metadata.
    if not blind_gate(track_id, result):
        raise IngestError(
            f"{track_id}: failed blind gate (no performance_graph or the "
            "curated kit is empty — stems likely separated to wash)")

    # 5) MERGE source metadata with analyzed features → CrateTrack.
    feats = features_from_result(result)
    track = CrateTrack(
        id=track_id,
        title=str(meta.get("title") or ""),
        artist=str(meta.get("artist") or ""),
        license=lic,
        features=feats,
        album=str(meta.get("album") or ""),
        year=(int(meta["year"]) if str(meta.get("year") or "").strip().isdigit() else None),
        genre=str(meta.get("genre") or ""),
        subgenres=tuple(str(x) for x in (meta.get("subgenres") or ())),
        tags=tuple(str(x) for x in (meta.get("tags") or ())),
        mood=str(meta.get("mood") or ""),
        stem_asset_base=str(meta.get("stem_asset_base") or ""),
        preview_url=meta.get("preview_url"),
        graph_available=graph_is_present(result),
    )

    if persist:
        _write_json(_reg._analysis_file(track_id), result)
        _append_registry(root, track)
        _reg.invalidate_cache()
    return track


def _write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_registry(root: Path, track: CrateTrack) -> None:
    """Append (or replace by id) a track row in the manifest. Newest first so
    default browse surfaces recent additions; a re-ingest of the same id
    overwrites its row rather than duplicating it."""
    path = root / "registry.json"
    rows: List[Dict] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                rows = loaded
            elif isinstance(loaded, dict):
                rows = loaded.get("tracks", []) or []
        except Exception:
            rows = []
    rows = [r for r in rows if isinstance(r, dict) and r.get("id") != track.id]
    rows.insert(0, _reg.track_to_dict(track))
    _write_json(path, rows)  # a bare list is the manifest form load_crate reads


def main(argv: Optional[List[str]] = None) -> int:
    """CLI stub. The REAL analyzer/downloader are wired in
    ``backend/scripts/ingest_crate.py`` (which may import unified_pipeline);
    this module refuses to run a live pull on its own so the boundary check
    stays clean and no accidental network happens in tests/CI."""
    raise SystemExit(
        "crate.ingest is a library: run backend/scripts/ingest_crate.py, which "
        "injects the real analyzer + downloader. This build performs no live "
        "pull (data-ops run on the VPS).")


if __name__ == "__main__":  # pragma: no cover
    main()
