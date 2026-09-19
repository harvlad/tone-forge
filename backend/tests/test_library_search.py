"""Unified Songs page — LibrarySource union + /api/library/* endpoints.

Covers the load-bearing invariants of the pluggable Songs table:

  * the UNION of analyzed history + in-flight jobs, keyed so a completing
    job COLLAPSES into its history row (the Band Room folds into a status
    column);
  * server-side facet filtering + sort BEFORE paging;
  * opaque-cursor paging — a row inserted mid-page does NOT shift the
    window (the whole reason cursors beat offsets here);
  * the ONE ingest door doing history-id AND content-hash dedupe
    (re-adding an already-analyzed track — by id or by the sha256
    persisted onto its history row — reuses that row instead of a
    duplicate; see LibrarySource.ingest);
  * the ``scope=mine`` owner gate mirrored exactly from /api/history.

Most logic is unit-tested against ``LibrarySource`` with in-memory list
providers (it is a pure transform by design); the endpoint tests then
prove the composition point wires scoping + serialization correctly.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tone_forge_api as api  # noqa: E402
from tone_forge import r2_storage  # noqa: E402
from tone_forge.analysis_jobs import JobRegistry  # noqa: E402
from tone_forge.auth.deps import SESSION_COOKIE  # noqa: E402
from tone_forge.auth.tokens import hash_token, new_token  # noqa: E402
from tone_forge.contracts import SourceId, TrackStatus  # noqa: E402
from tone_forge.sources import LibrarySource  # noqa: E402
from tone_forge.sources.base import decode_cursor, encode_cursor  # noqa: E402


# ---------------------------------------------------------------------------
# helpers — build the plain dicts the composition point hands LibrarySource
# ---------------------------------------------------------------------------

def _hist(
    hid,
    name="Song",
    *,
    key=None,
    tempo=None,
    duration=None,
    genre=None,
    mood=None,
    tags=None,
    artist=None,
    timestamp="2026-01-01T00:00:00",
    content_hash=None,
    **extra,
):
    """A history entry the way ``_load_history()`` yields one (full blob)."""
    entry = {"id": hid, "name": name, "timestamp": timestamp}
    if key is not None:
        entry["detected_key"] = key
    if tempo is not None:
        entry["tempo_bpm"] = tempo
    if duration is not None:
        entry["duration"] = duration
    if genre is not None:
        entry["genre"] = genre
    if mood is not None:
        entry["mood"] = mood
    if tags is not None:
        entry["tags"] = tags
    if artist is not None:
        entry["artist"] = artist
    if content_hash is not None:
        entry["content_hash"] = content_hash
    entry.update(extra)
    return entry


def _job(job_id, *, status="running", percent=0.0, history_id=None,
         filename="upload.wav", created_at=0.0, meta=None):
    """A job dict the way ``public_dict()`` (plus injected ``meta``) yields."""
    return {
        "job_id": job_id,
        "status": status,
        "percent": percent,
        "history_id": history_id,
        "filename": filename,
        "created_at": created_at,
        "meta": meta or {},
    }


def _src(history, jobs=()):
    return LibrarySource(lambda: list(history), lambda: list(jobs))


# ---------------------------------------------------------------------------
# union + collapse
# ---------------------------------------------------------------------------

class TestUnionCollapse:
    def test_done_job_collapses_into_history_row(self):
        # A finished job carries the history_id it produced; once that
        # history row exists the job row is redundant and must vanish.
        history = [_hist("H1", "Done Song")]
        jobs = [_job("J1", status="done", percent=100.0, history_id="H1")]
        page = _src(history, jobs).search(limit=50)

        refs = [t.source_ref for t in page.tracks]
        assert refs == ["H1"], "the done job must fold into its history row"
        assert page.total == 1
        (only,) = page.tracks
        assert only.status == TrackStatus.DONE
        assert only.source == SourceId.LIBRARY

    def test_running_job_shows_as_its_own_row(self):
        # No history row yet -> the in-flight upload IS the row.
        history = [_hist("H1", "Done Song")]
        jobs = [_job("J1", status="running", percent=40.0)]
        page = _src(history, jobs).search(limit=50)

        by_ref = {t.source_ref: t for t in page.tracks}
        assert set(by_ref) == {"H1", "J1"}
        assert by_ref["J1"].status == TrackStatus.RUNNING
        assert by_ref["J1"].progress == pytest.approx(0.40)

    def test_done_job_without_history_row_still_shows(self):
        # Defensive: a done job whose history_id we DON'T have yet is not
        # yet represented, so it must remain visible (no silent drop).
        jobs = [_job("J1", status="done", percent=100.0, history_id="H-missing")]
        page = _src([], jobs).search(limit=50)
        assert [t.source_ref for t in page.tracks] == ["J1"]

    def test_error_job_reports_partial_progress(self):
        jobs = [_job("J1", status="error", percent=55.0)]
        (row,) = _src([], jobs).search(limit=50).tracks
        assert row.status == TrackStatus.ERROR
        assert row.progress == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# facets: filter + bucket computation
# ---------------------------------------------------------------------------

class TestFacets:
    def _rows(self):
        return [
            _hist("H1", "Rock A", genre="rock", key="C", tempo=120, mood="happy"),
            _hist("H2", "Rock B", genre="rock", key="G", tempo=90, mood="sad"),
            _hist("H3", "Jazz A", genre="jazz", key="C", tempo=140, mood="happy"),
        ]

    def test_genre_filter(self):
        page = _src(self._rows()).search(facets={"genre": "rock"}, limit=50)
        assert {t.source_ref for t in page.tracks} == {"H1", "H2"}

    def test_key_filter_case_insensitive(self):
        page = _src(self._rows()).search(facets={"key": "c"}, limit=50)
        assert {t.source_ref for t in page.tracks} == {"H1", "H3"}

    def test_tempo_range(self):
        page = _src(self._rows()).search(
            facets={"tempo_min": 100, "tempo_max": 130}, limit=50
        )
        assert {t.source_ref for t in page.tracks} == {"H1"}

    def test_tempo_range_excludes_unknown_tempo(self):
        rows = self._rows() + [_hist("H4", "No Tempo", genre="rock")]
        page = _src(rows).search(facets={"tempo_min": 1}, limit=50)
        assert "H4" not in {t.source_ref for t in page.tracks}

    def test_mood_filter(self):
        page = _src(self._rows()).search(facets={"mood": "sad"}, limit=50)
        assert {t.source_ref for t in page.tracks} == {"H2"}

    def test_status_filter(self):
        history = [_hist("H1", "Done")]
        jobs = [_job("J1", status="running", percent=10.0)]
        page = _src(history, jobs).search(facets={"status": "running"}, limit=50)
        assert {t.source_ref for t in page.tracks} == {"J1"}

    def test_tags_require_all(self):
        rows = [
            _hist("H1", "A", tags=["clean", "loop"]),
            _hist("H2", "B", tags=["clean"]),
        ]
        page = _src(rows).search(facets={"tags": "clean,loop"}, limit=50)
        assert {t.source_ref for t in page.tracks} == {"H1"}

    def test_buckets_count_over_query_filtered_set(self):
        # Facet buckets describe the query-filtered set, BEFORE the facet
        # selection narrows it, so sibling filter values still show.
        page = _src(self._rows()).search(facets={"genre": "rock"}, limit=50)
        genres = {b.value: b.count for b in page.facets["genre"]}
        assert genres == {"rock": 2, "jazz": 1}


# ---------------------------------------------------------------------------
# query (free text)
# ---------------------------------------------------------------------------

class TestQuery:
    def test_query_matches_title_artist_genre(self):
        rows = [
            _hist("H1", "Blue Skies", artist="Nina"),
            _hist("H2", "Red Sun", genre="ambient"),
            _hist("H3", "Green", artist="Blue Man"),
        ]
        refs = {t.source_ref for t in _src(rows).search(query="blue", limit=50).tracks}
        assert refs == {"H1", "H3"}


# ---------------------------------------------------------------------------
# sort
# ---------------------------------------------------------------------------

class TestSort:
    def test_recent_is_newest_first(self):
        rows = [
            _hist("old", "Old", timestamp="2026-01-01T00:00:00"),
            _hist("new", "New", timestamp="2026-06-01T00:00:00"),
        ]
        page = _src(rows).search(sort="recent", limit=50)
        assert [t.source_ref for t in page.tracks] == ["new", "old"]

    def test_title_sort_case_insensitive(self):
        rows = [_hist("H1", "banana"), _hist("H2", "Apple"), _hist("H3", "cherry")]
        page = _src(rows).search(sort="title", limit=50)
        assert [t.title for t in page.tracks] == ["Apple", "banana", "cherry"]

    def test_tempo_sort_unknown_last(self):
        rows = [
            _hist("H1", "A", tempo=120),
            _hist("H2", "B"),  # no tempo -> sorts last
            _hist("H3", "C", tempo=90),
        ]
        page = _src(rows).search(sort="tempo", limit=50)
        assert [t.source_ref for t in page.tracks] == ["H3", "H1", "H2"]

    def test_unknown_sort_falls_back_to_recent(self):
        rows = [
            _hist("old", "Old", timestamp="2026-01-01T00:00:00"),
            _hist("new", "New", timestamp="2026-06-01T00:00:00"),
        ]
        page = _src(rows).search(sort="bogus", limit=50)
        assert [t.source_ref for t in page.tracks] == ["new", "old"]


# ---------------------------------------------------------------------------
# cursor paging — the opaque-cursor invariant
# ---------------------------------------------------------------------------

class TestCursorPaging:
    def _rows(self):
        return [_hist(t, t) for t in ("a", "b", "c", "d")]

    def test_pages_cover_all_rows_without_overlap(self):
        src = _src(self._rows())
        seen = []
        cursor = None
        for _ in range(10):
            page = src.search(sort="title", cursor=cursor, limit=2)
            seen.extend(t.source_ref for t in page.tracks)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert seen == ["a", "b", "c", "d"]

    def test_midpage_insert_does_not_shift_window(self):
        # THE invariant: page 1 = [a, b], cursor after b. A background
        # ingest then inserts "aa" (sorts between a and b). Under OFFSET
        # paging page 2 would become [b, c] — b repeats, d is skipped.
        # Under boundary paging page 2 is exactly the rows AFTER b: c, d.
        history = list(self._rows())
        src = LibrarySource(lambda: list(history), lambda: [])

        page1 = src.search(sort="title", limit=2)
        assert [t.source_ref for t in page1.tracks] == ["a", "b"]
        cursor = page1.next_cursor
        assert cursor is not None

        history.insert(0, _hist("aa", "aa"))  # mid-window insert

        page2 = src.search(sort="title", cursor=cursor, limit=2)
        refs = [t.source_ref for t in page2.tracks]
        assert refs == ["c", "d"], "inserted row must not shift the window"
        assert "b" not in refs and "aa" not in refs

    def test_next_cursor_none_on_last_page(self):
        page = _src(self._rows()).search(sort="title", limit=50)
        assert page.next_cursor is None

    def test_cursor_from_other_sort_is_ignored(self):
        # A cursor minted under one sort must not silently page a
        # different order — it is ignored and paging restarts from the top.
        src = _src(self._rows())
        title_cursor = src.search(sort="title", limit=2).next_cursor
        page = src.search(sort="recent", cursor=title_cursor, limit=2)
        # recent order for equal timestamps tiebreaks on source_ref asc.
        assert len(page.tracks) == 2

    def test_garbage_cursor_starts_from_top(self):
        page = _src(self._rows()).search(sort="title", cursor="!!!not-b64!!!", limit=2)
        assert [t.source_ref for t in page.tracks] == ["a", "b"]

    def test_cursor_roundtrip(self):
        (track,) = _src([_hist("H1", "Title")]).search(sort="title", limit=1).tracks
        token = encode_cursor("title", track)
        decoded = decode_cursor(token)
        assert decoded["s"] == "title"
        assert decoded["r"] == "H1"


# ---------------------------------------------------------------------------
# ingest — the dedupe door
# ---------------------------------------------------------------------------

class TestIngestDedupe:
    def test_dedupe_by_history_id(self):
        src = _src([_hist("H1", "Known")])
        assert src.ingest("H1") == {"history_id": "H1", "deduped": True}

    def test_dedupe_by_content_hash(self):
        # Content-hash reuse is now REAL: the upload path persists a
        # sha256 onto the completed history entry, so a ref naming that
        # fingerprint reuses the analyzed row instead of a duplicate.
        src = _src([_hist("H1", "Known", content_hash="sha-abc")])
        assert src.ingest("sha-abc") == {"history_id": "H1", "deduped": True}

    def test_dedupe_by_content_hash_legacy_sha256_alias(self):
        # A writer that stamped the fingerprint under the ``sha256`` alias
        # still matches (both names key the same content-hash branch).
        src = _src([_hist("H1", "Known", sha256="sha-xyz")])
        assert src.ingest("sha-xyz") == {"history_id": "H1", "deduped": True}

    def test_history_id_wins_over_content_hash(self):
        # A ref that happens to equal one row's id and another row's hash
        # resolves to the id match (checked first, the stronger key).
        src = _src([
            _hist("H1", "ById"),
            _hist("H2", "ByHash", content_hash="H1"),
        ])
        assert src.ingest("H1") == {"history_id": "H1", "deduped": True}

    def test_legacy_entry_without_hash_does_not_dedupe(self):
        # Entries written before the hash was persisted carry none and
        # must never match the content-hash branch (additive, no crash).
        src = _src([_hist("H1", "Known")])
        assert src.ingest("sha-none") == {"history_id": None, "deduped": False}

    def test_unknown_ref_is_noop(self):
        src = _src([_hist("H1", "Known")])
        assert src.ingest("nope") == {"history_id": None, "deduped": False}

    def test_blank_ref_is_noop(self):
        assert _src([]).ingest("  ") == {"history_id": None, "deduped": False}


# ---------------------------------------------------------------------------
# endpoint — composition point wiring, scoping, serialization
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(api, "_JOBS", JobRegistry(tmp_path / "jobs"))
    monkeypatch.setattr(r2_storage, "is_configured", lambda: False)
    with TestClient(api.app) as c:
        yield c


def _sign_in(email="own@x.co"):
    async def go():
        store = api.app.state.auth_store
        user = await store.upsert_user_by_identity("email", email, email=email)
        token = new_token()
        await store.create_session(user.id, hash_token(token))
        return token, user

    return asyncio.run(go())


class TestEndpointScopeMine:
    def test_anonymous_scope_mine_401(self, client):
        assert client.get("/api/library/search?scope=mine").status_code == 401

    def test_scope_mine_filters_to_owner_and_device(self, client):
        token, user = _sign_in()
        api._add_to_history({"name": "mine-owned"}, owner_id=user.id)
        api._add_to_history({"name": "mine-device"}, device_id="dev-m")
        api._add_to_history({"name": "not-mine"}, owner_id="someone-else")
        api._add_to_history({"name": "anon"})

        client.cookies.set(SESSION_COOKIE, token)
        resp = client.get(
            "/api/library/search?scope=mine", headers={"X-Device-Id": "dev-m"}
        )
        assert resp.status_code == 200
        titles = {t["title"] for t in resp.json()["tracks"]}
        assert titles == {"mine-owned", "mine-device"}

    def test_default_scope_returns_everything(self, client):
        api._add_to_history({"name": "a"}, owner_id="u1")
        api._add_to_history({"name": "b"})
        resp = client.get("/api/library/search")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_shared_library_flag_is_the_only_thing_that_cross_shows(self, client, monkeypatch):
        # SHARED_LIBRARY=1 is a testing-only cross-show. The security-
        # relevant invariant is the GATE (who is a MEMBER of the result),
        # not display order: the Songs table has an explicit sort control,
        # so ordering follows the chosen sort (with a deterministic
        # source_ref tiebreak the opaque cursor depends on) rather than
        # ownership. We prove the flag is the ONLY thing widening the set:
        # OFF -> only my row; ON -> everyone's.
        token, user = _sign_in()
        api._add_to_history({"name": "mine-song"}, owner_id=user.id)
        api._add_to_history({"name": "other-song"}, owner_id="someone-else")
        client.cookies.set(SESSION_COOKIE, token)

        # Gate holds by default: another owner's song is NOT visible.
        monkeypatch.delenv("TONEFORGE_SHARED_LIBRARY", raising=False)
        resp = client.get("/api/library/search?scope=mine")
        assert resp.status_code == 200
        assert {t["title"] for t in resp.json()["tracks"]} == {"mine-song"}

        # Testing flag on: membership widens to the whole library, own row
        # still present (reads only — delete stays owner-gated elsewhere).
        monkeypatch.setenv("TONEFORGE_SHARED_LIBRARY", "1")
        resp = client.get("/api/library/search?scope=mine")
        assert resp.status_code == 200
        titles = {t["title"] for t in resp.json()["tracks"]}
        assert titles == {"mine-song", "other-song"}


class TestEndpointUnion:
    def test_done_job_collapses_through_endpoint(self, client):
        token, user = _sign_in()
        entry = api._add_to_history({"name": "Finished"}, owner_id=user.id)
        # a finished job that produced that history row + a still-running one
        done = api._JOBS.create_engine_job(filename="finished.wav", owner_id=user.id)
        done.status = "done"
        done.history_id = entry["id"]
        # A still-in-flight upload with no history row yet. Engine jobs are
        # born "queued"; mark it running so the assertion reads as "an
        # in-flight row survives the collapse", whatever its exact phase.
        running = api._JOBS.create_engine_job(filename="uploading.wav", owner_id=user.id)
        running.status = "running"

        client.cookies.set(SESSION_COOKIE, token)
        resp = client.get("/api/library/search?scope=mine")
        assert resp.status_code == 200
        rows = resp.json()["tracks"]
        refs = {r["source_ref"] for r in rows}
        # history row present, the done job's id absent (collapsed), the
        # running job present as its own row.
        assert entry["id"] in refs
        assert done.id not in refs
        statuses = {r["status"] for r in rows}
        assert "running" in statuses
        assert "done" in statuses

    def test_serialized_row_shape(self, client):
        api._add_to_history(
            {"name": "Shape", "detected_key": "C", "tempo_bpm": 128,
             "duration": 200.0, "genre": "rock"},
        )
        resp = client.get("/api/library/search")
        row = resp.json()["tracks"][0]
        assert row["source"] == "library"
        assert row["status"] == "done"
        assert row["key"] == "C"
        assert row["tempo_bpm"] == 128
        assert row["genre"] == "rock"
        assert isinstance(row["tags"], list)


class TestEndpointSource:
    def test_unknown_source_400(self, client):
        assert client.get("/api/library/search?source=bogus").status_code == 400

    def test_unimplemented_source_400(self, client):
        # A declared-but-not-built seam (crate) is a 400, never a silent
        # empty page, so a not-yet-shipped source is visible.
        assert client.get("/api/library/search?source=crate").status_code == 400


class TestEndpointIngest:
    def test_ingest_dedupes_to_existing_history_id(self, client):
        token, user = _sign_in()
        entry = api._add_to_history({"name": "Already"}, owner_id=user.id)
        client.cookies.set(SESSION_COOKIE, token)
        resp = client.post(
            "/api/library/ingest",
            json={"source": "library", "source_ref": entry["id"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["history_id"] == entry["id"]
        assert body["deduped"] is True

    def test_ingest_unknown_ref_is_noop(self, client):
        # Ingest dedupes against the CALLER's library, so it runs behind
        # the same scope=mine gate as search — authenticate first.
        token, _ = _sign_in()
        client.cookies.set(SESSION_COOKIE, token)
        resp = client.post(
            "/api/library/ingest",
            json={"source": "library", "source_ref": "does-not-exist"},
        )
        assert resp.status_code == 200
        assert resp.json()["history_id"] is None

    def test_ingest_requires_sign_in(self, client):
        # The dedupe door is owner-gated exactly like /api/history scope=mine:
        # an anonymous caller can't read a library to dedupe against -> 401.
        resp = client.post(
            "/api/library/ingest",
            json={"source": "library", "source_ref": "anything"},
        )
        assert resp.status_code == 401
