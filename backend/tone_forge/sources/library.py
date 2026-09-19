"""LibrarySource — the user's own library as a unified Songs source.

The library row set is the UNION of two lists the backend already keeps
apart:

  * analyzed **history** (``_load_history()`` in ``tone_forge_api``) —
    finished analyses, every one a ``DONE`` row; and
  * in-flight **jobs** (the ``JobRegistry``) — queued / running / errored
    uploads that have no history row yet.

They are keyed so a completing job COLLAPSES into its history row: a job
already carries the ``history_id`` it produced, so once that history row
exists we drop the (now redundant) ``done`` job row and keep the richer
history row. That is what lets the Band Room stop being a destination and
become a status column.

Boundary: this module consumes plain dicts (history entries + job dicts)
handed in by the composition point, already scoped to the caller. It
imports only ``tone_forge.contracts`` + this package. It never touches
``tone_forge_api`` or the auth/job internals — the endpoint does the
scoping and injects the data via providers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from tone_forge.contracts import (
    FacetBucket,
    SearchPage,
    SourceId,
    SourceTrack,
    TrackStatus,
)
from tone_forge.sources.base import (
    SourceCaps,
    paginate,
    sort_tracks,
)

# Facet fields the library exposes as filter chips + honours as filters.
_FACET_FIELDS: Tuple[str, ...] = ("genre", "key", "mood", "status")

HistoryProvider = Callable[[], Sequence[dict]]
JobsProvider = Callable[[], Sequence[dict]]


def _parse_timestamp(value: Any) -> float:
    """History timestamps are naive-local isoformat strings; jobs use
    epoch floats. Normalise either to epoch seconds for ``recent``
    ordering. Unparseable → 0.0 (sorts oldest, never raises)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except (ValueError, OSError):
            return 0.0
    return 0.0


def _clean_str(value: Any) -> Optional[str]:
    """Coerce to a trimmed non-empty string or None. Attribution fields
    use ``""`` for unknown; the table wants a real absence."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _project_song_meta(entry: dict) -> Tuple[Optional[str], Optional[str], Tuple[str, ...]]:
    """Best-effort ``(genre, mood, tags)`` for a history entry.

    These are already computed during analysis; where exactly they land
    in the result blob has drifted over time, so we read a handful of
    known locations and tolerate absence. Cheap: the result blob is
    already in memory on the loaded entry — we pull scalars, never the
    blob itself, so list rows stay lightweight.
    """
    result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
    understanding = (
        result.get("understanding") if isinstance(result.get("understanding"), dict) else {}
    )
    detection = (
        result.get("detection") if isinstance(result.get("detection"), dict) else {}
    )

    def _first(*candidates: Any) -> Optional[str]:
        for c in candidates:
            cleaned = _clean_str(c)
            if cleaned:
                return cleaned
        return None

    genre = _first(
        entry.get("genre"),
        result.get("genre"),
        understanding.get("genre"),
        detection.get("genre"),
    )
    mood = _first(
        entry.get("mood"),
        result.get("mood"),
        understanding.get("mood"),
    )

    raw_tags = (
        entry.get("tags")
        or result.get("tags")
        or understanding.get("tags")
        or ()
    )
    if isinstance(raw_tags, str):
        raw_tags = [t for t in (s.strip() for s in raw_tags.split(",")) if t]
    tags: Tuple[str, ...] = tuple(
        t for t in (_clean_str(x) for x in raw_tags) if t
    )
    return genre, mood, tags


def _project_key_tempo(entry: dict) -> Tuple[Optional[str], Optional[float]]:
    """Best-effort ``(key, tempo_bpm)`` for a history entry.

    THE bug this fixes: a deep-analysis history entry stores the detected
    key/tempo INSIDE its ``result`` blob (``result.detected_key`` /
    ``result.tempo_bpm``), NOT at the entry top level. The top-level
    ``detected_key``/``tempo_bpm`` names only ever exist on the slimmed
    ``/api/history`` LIST rows (see ``_HISTORY_LIST_FIELDS`` in
    ``tone_forge_api``) — never on the full entries the Songs-page
    composition point (``_library_scoped_history``) hands us. Reading only
    the top level meant EVERY analyzed row projected ``key=None`` /
    ``tempo_bpm=None``, so the Key/Tempo columns were blank and
    ``_compute_facets`` emitted no key bucket (facets collapsed to
    ``{"status": ...}`` alone — the exact prod symptom).

    Top level is still checked first so a future writer that surfaces the
    scalars onto the entry keeps working; then we dip into ``result``.
    ``result`` is already resident on the loaded entry, so this stays a
    scalar pull — no extra I/O, list rows stay light. ``key`` also honours
    the legacy ``key`` alias; ``tempo`` honours ``tempo``.
    """
    result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
    key = (
        _clean_str(entry.get("detected_key"))
        or _clean_str(entry.get("key"))
        or _clean_str(result.get("detected_key"))
        or _clean_str(result.get("key"))
    )
    # A 0.0 tempo is "unknown", not a real value — the ``or`` chain skips it
    # (falsy) and falls through, ending at None if nothing meaningful exists.
    tempo = (
        _coerce_float(entry.get("tempo_bpm"))
        or _coerce_float(entry.get("tempo"))
        or _coerce_float(result.get("tempo_bpm"))
        or _coerce_float(result.get("tempo"))
    )
    return key, tempo


class LibrarySource:
    """MusicSource over the caller's own history + jobs.

    Construct with two providers (callables) that return already-scoped
    plain dicts. Keeping them lazy means the endpoint owns scoping/auth
    and this class stays a pure transform, trivially unit-testable with
    in-memory lists.
    """

    id: SourceId = SourceId.LIBRARY
    caps: SourceCaps = SourceCaps(
        search=True,
        ingest=True,  # dedupe/no-op door — see ``ingest``
        facets=_FACET_FIELDS,
    )

    def __init__(self, history_provider: HistoryProvider, jobs_provider: JobsProvider):
        self._history_provider = history_provider
        self._jobs_provider = jobs_provider

    # -- row construction ------------------------------------------------

    def _track_from_history(self, entry: dict) -> SourceTrack:
        genre, mood, tags = _project_song_meta(entry)
        key, tempo_bpm = _project_key_tempo(entry)
        hid = _clean_str(entry.get("id"))
        return SourceTrack(
            source=SourceId.LIBRARY,
            source_ref=hid or "",
            title=_clean_str(entry.get("name")) or _clean_str(entry.get("filename")) or "Untitled",
            artist=_clean_str(entry.get("artist")),
            key=key,
            tempo_bpm=tempo_bpm,
            duration_s=_coerce_float(entry.get("duration")),
            genre=genre,
            mood=mood,
            tags=tags,
            license=_clean_str(entry.get("license")),
            license_url=_clean_str(entry.get("license_url")),
            attribution=_clean_str(entry.get("attribution")),
            source_url=_clean_str(entry.get("source_url")),
            status=TrackStatus.DONE,
            progress=1.0,
            history_id=hid,
            artwork_ref=_clean_str(entry.get("artwork_ref")),
            created_at_s=_parse_timestamp(entry.get("timestamp")),
        )

    def _track_from_job(self, job: dict) -> SourceTrack:
        meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
        status = _job_status(job.get("status"))
        percent = _coerce_float(job.get("percent")) or 0.0
        # percent is 0..100; progress is [0,1]. Errored rows report
        # whatever progress they reached, not a fake 0.
        progress = max(0.0, min(1.0, percent / 100.0))
        title = (
            _clean_str(meta.get("title"))
            or _clean_str(job.get("filename"))
            or "Processing…"
        )
        return SourceTrack(
            source=SourceId.LIBRARY,
            source_ref=_clean_str(job.get("job_id")) or "",
            title=title,
            artist=_clean_str(meta.get("artist")),
            key=None,
            tempo_bpm=None,
            duration_s=None,
            genre=None,
            mood=None,
            tags=(),
            license=_clean_str(meta.get("license")),
            license_url=_clean_str(meta.get("license_url")),
            attribution=_clean_str(meta.get("attribution")),
            source_url=_clean_str(meta.get("source_url")),
            status=status,
            progress=progress,
            history_id=_clean_str(job.get("history_id")),
            artwork_ref=None,
            created_at_s=_parse_timestamp(job.get("created_at")),
        )

    def _union(self) -> List[SourceTrack]:
        """History ∪ jobs, keyed so a done job folds into its history row.

        A history row is authoritative for its analysis. A job is shown
        only while it has no equivalent history row yet: once a job is
        ``done`` and its ``history_id`` names a row we already have, the
        job row is redundant and dropped (the collapse). Non-terminal
        jobs (queued/running/error) always show — they have no history
        row to collapse into.
        """
        history_entries = [
            e for e in self._history_provider() if _clean_str(e.get("id"))
        ]
        history_tracks = [self._track_from_history(e) for e in history_entries]
        known_history_ids = {t.history_id for t in history_tracks}

        job_tracks: List[SourceTrack] = []
        seen_job_refs: set[str] = set()
        for job in self._jobs_provider():
            ref = _clean_str(job.get("job_id"))
            if not ref or ref in seen_job_refs:
                continue
            seen_job_refs.add(ref)
            status = _job_status(job.get("status"))
            hid = _clean_str(job.get("history_id"))
            # COLLAPSE: a finished job whose history row exists is already
            # represented by that row — drop the duplicate.
            if status == TrackStatus.DONE and hid and hid in known_history_ids:
                continue
            job_tracks.append(self._track_from_job(job))

        return history_tracks + job_tracks

    # -- MusicSource protocol -------------------------------------------

    def search(
        self,
        query: Optional[str] = None,
        facets: Optional[Dict[str, Any]] = None,
        sort: str = "recent",
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> SearchPage:
        rows = self._union()
        rows = _apply_query(rows, query)
        # Facet buckets describe what's filterable over the query-filtered
        # set (before the specific facet selections narrow it), so the UI
        # can still show sibling filter values.
        facet_buckets = _compute_facets(rows)
        rows = _apply_facets(rows, facets or {})
        ordered = sort_tracks(rows, sort)
        page, next_cursor = paginate(ordered, sort, cursor, limit)
        return SearchPage(
            tracks=tuple(page),
            next_cursor=next_cursor,
            facets=facet_buckets,
            total=len(ordered),
        )

    def ingest(self, source_ref: str) -> Dict[str, Any]:
        """The ONE ingest door — dedupe / no-op for the library source.

        A track already in the user's library is reused, not re-analyzed:
        given a ``source_ref`` naming an existing history id, return that
        ``history_id`` so the caller opens the analyzed row instead of
        queuing a duplicate. Unknown ref stays a no-op
        (``history_id: None``) — other sources override ``ingest`` to
        actually queue a job.

        Two dedupe keys, tried in order:

          1. **History id** — the ref names a row directly.
          2. **Content hash** — the ref is the sha256 of an audio file
             and a completed analysis already carries that fingerprint.
             This is now real end-to-end: engine-job completion persists
             ``content_hash`` onto the history entry (see the upload path
             + ``engine_job_complete`` in ``tone_forge_api``), so a
             re-upload of identical bytes reuses the prior analysis
             instead of manufacturing a duplicate row.

        Legacy history entries written before the hash was persisted
        simply carry no ``content_hash`` and never match the second
        branch — dedupe is additive and never crashes on old data. The
        history the provider yields is already owner-scoped by the
        composition point, so neither branch can cross users.
        """
        ref = _clean_str(source_ref)
        if not ref:
            return {"history_id": None, "deduped": False}
        entries = list(self._history_provider())
        for entry in entries:
            eid = _clean_str(entry.get("id"))
            if ref == eid:
                return {"history_id": eid, "deduped": True}
        # Content-hash reuse. ``sha256`` is accepted as a legacy alias for
        # the same field so either name a writer used matches.
        for entry in entries:
            chash = _clean_str(entry.get("content_hash")) or _clean_str(entry.get("sha256"))
            if chash and ref == chash:
                eid = _clean_str(entry.get("id"))
                if eid:
                    return {"history_id": eid, "deduped": True}
        return {"history_id": None, "deduped": False}


# ---------------------------------------------------------------------------
# Filtering / faceting helpers (module-level, pure — easy to unit test)
# ---------------------------------------------------------------------------


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # NaN/Inf aren't valid JSON and aren't meaningful tempos/durations.
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _job_status(raw: Any) -> TrackStatus:
    text = (str(raw) if raw is not None else "").strip().lower()
    if text == "done":
        return TrackStatus.DONE
    if text == "running":
        return TrackStatus.RUNNING
    if text == "error":
        return TrackStatus.ERROR
    return TrackStatus.QUEUED


def _apply_query(rows: List[SourceTrack], query: Optional[str]) -> List[SourceTrack]:
    q = (query or "").strip().casefold()
    if not q:
        return rows
    out: List[SourceTrack] = []
    for t in rows:
        hay = " ".join(
            filter(
                None,
                [t.title, t.artist, t.genre, t.mood, t.key, " ".join(t.tags)],
            )
        ).casefold()
        if q in hay:
            out.append(t)
    return out


def _apply_facets(rows: List[SourceTrack], facets: Dict[str, Any]) -> List[SourceTrack]:
    if not facets:
        return rows
    genre = _clean_str(facets.get("genre"))
    key = _clean_str(facets.get("key"))
    mood = _clean_str(facets.get("mood"))
    status = _clean_str(facets.get("status"))
    tempo_min = _coerce_float(facets.get("tempo_min"))
    tempo_max = _coerce_float(facets.get("tempo_max"))
    raw_tags = facets.get("tags")
    if isinstance(raw_tags, str):
        want_tags = {t.strip().casefold() for t in raw_tags.split(",") if t.strip()}
    elif isinstance(raw_tags, (list, tuple, set)):
        want_tags = {str(t).strip().casefold() for t in raw_tags if str(t).strip()}
    else:
        want_tags = set()

    out: List[SourceTrack] = []
    for t in rows:
        if genre and (t.genre or "").casefold() != genre.casefold():
            continue
        if key and (t.key or "").casefold() != key.casefold():
            continue
        if mood and (t.mood or "").casefold() != mood.casefold():
            continue
        # NB: use the enum ``.value`` ("done"), not ``str(status)`` — a
        # ``str``-mixin Enum stringifies to "TrackStatus.DONE" via
        # Enum.__str__, which would never match the "done" the client sends.
        if status and t.status.value.casefold() != status.casefold():
            continue
        # A tempo range excludes rows with no tempo — an unknown tempo
        # can't be asserted to fall inside the band.
        if tempo_min is not None:
            if t.tempo_bpm is None or t.tempo_bpm < tempo_min:
                continue
        if tempo_max is not None:
            if t.tempo_bpm is None or t.tempo_bpm > tempo_max:
                continue
        if want_tags:
            have = {tag.casefold() for tag in t.tags}
            if not want_tags.issubset(have):
                continue
        out.append(t)
    return out


def _compute_facets(rows: List[SourceTrack]) -> Dict[str, Tuple[FacetBucket, ...]]:
    """Value → count buckets per facet field, sorted by count desc then
    value asc. Empty/None values don't get a bucket."""
    buckets: Dict[str, Dict[str, int]] = {f: {} for f in _FACET_FIELDS}
    for t in rows:
        values = {
            "genre": t.genre,
            "key": t.key,
            "mood": t.mood,
            "status": t.status.value,
        }
        for field_name, value in values.items():
            v = _clean_str(value)
            if not v:
                continue
            buckets[field_name][v] = buckets[field_name].get(v, 0) + 1

    out: Dict[str, Tuple[FacetBucket, ...]] = {}
    for field_name, counts in buckets.items():
        if not counts:
            continue
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        out[field_name] = tuple(FacetBucket(value=v, count=c) for v, c in ordered)
    return out
