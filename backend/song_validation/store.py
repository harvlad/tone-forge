"""SQLite storage layer for the song-validation subsystem.

The schema mirrors the six tables called out in the architecture
directive:

- ``songs``               canonical song identity (artist + title +
                          duration).
- ``analysis_results``    one row per JAM analysis bundle uploaded
                          from a Connect client.
- ``tab_sources``         one row per ingested tab/chord-source
                          progression for a song.
- ``alignment_results``   one row per (analysis, tab) pair that the
                          aligner has processed.
- ``disagreements``       per-timestamp mismatches found during
                          alignment, with a classification label.
- ``engine_metrics``      aggregated per-engine-version scores
                          (agreement_rate, boundary_accuracy, ...).

Foreign keys are declared but enforcement is left to the caller via
``PRAGMA foreign_keys=ON`` on each connection (sqlite default is
off). The aim here is a foundation, not a fully constrained
production schema; future commits add indexes + cascading deletes as
real query patterns surface.

Connection lifetime follows the same contextmanager pattern as
``tone_forge.ml.retrieval.reference_library`` so it composes with
existing test fixtures.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence


DEFAULT_DB_PATH = Path.home() / ".toneforge" / "song_validation.db"


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS songs (
    song_id   TEXT PRIMARY KEY,
    artist    TEXT,
    title     TEXT,
    duration  REAL
);

CREATE TABLE IF NOT EXISTS analysis_results (
    analysis_id    TEXT PRIMARY KEY,
    song_id        TEXT NOT NULL REFERENCES songs(song_id),
    engine_version TEXT NOT NULL,
    chords         TEXT NOT NULL,  -- JSON array
    sections       TEXT NOT NULL,  -- JSON array
    tempo          REAL,
    key            TEXT,
    created_at     TEXT NOT NULL   -- ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_analysis_results_song
    ON analysis_results(song_id);
CREATE INDEX IF NOT EXISTS idx_analysis_results_engine
    ON analysis_results(engine_version);

CREATE TABLE IF NOT EXISTS tab_sources (
    tab_id            TEXT PRIMARY KEY,
    song_id           TEXT NOT NULL REFERENCES songs(song_id),
    source            TEXT NOT NULL,         -- e.g. "songsterr"
    source_confidence REAL,                  -- 0..1
    progression       TEXT NOT NULL,         -- JSON array
    raw_tab           TEXT                   -- optional raw payload
);

CREATE INDEX IF NOT EXISTS idx_tab_sources_song ON tab_sources(song_id);

CREATE TABLE IF NOT EXISTS alignment_results (
    alignment_id TEXT PRIMARY KEY,
    song_id      TEXT NOT NULL REFERENCES songs(song_id),
    analysis_id  TEXT NOT NULL REFERENCES analysis_results(analysis_id),
    tab_id       TEXT NOT NULL REFERENCES tab_sources(tab_id),
    score        REAL,                       -- 0..1 alignment confidence
    total_points INTEGER NOT NULL DEFAULT 0, -- number of grid points sampled
    created_at   TEXT NOT NULL,
    aligner_kind TEXT NOT NULL DEFAULT 'grid' -- 'grid' | 'dtw' | future
);

CREATE INDEX IF NOT EXISTS idx_alignment_results_song
    ON alignment_results(song_id);
CREATE INDEX IF NOT EXISTS idx_alignment_results_aligner
    ON alignment_results(aligner_kind);

CREATE TABLE IF NOT EXISTS disagreements (
    disagreement_id TEXT PRIMARY KEY,
    song_id         TEXT NOT NULL REFERENCES songs(song_id),
    alignment_id    TEXT REFERENCES alignment_results(alignment_id),
    timestamp       REAL NOT NULL,           -- seconds into song
    jam_chord       TEXT,
    tab_chord       TEXT,
    confidence      REAL,                    -- engine confidence
    classification  TEXT NOT NULL            -- DisagreementClass value
);

CREATE INDEX IF NOT EXISTS idx_disagreements_song
    ON disagreements(song_id);
CREATE INDEX IF NOT EXISTS idx_disagreements_class
    ON disagreements(classification);

CREATE TABLE IF NOT EXISTS engine_metrics (
    engine_version          TEXT PRIMARY KEY,
    agreement_rate          REAL,
    boundary_accuracy       REAL,
    slash_chord_accuracy    REAL,
    extension_accuracy      REAL,
    updated_at              TEXT NOT NULL
);
"""


class Store:
    """SQLite-backed store for the song-validation subsystem.

    The store is intentionally thin — it owns schema creation and the
    smallest set of write/read helpers needed by the ingestion
    module. The alignment / disagreement / metrics modules add their
    own helpers on top as they're implemented.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a sqlite connection with foreign keys enabled.

        Use as ``with store.connect() as conn: ...``. The connection
        commits on success and closes on exit; rollback is implicit
        on exception via sqlite3's contextmanager semantics.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _create_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA_DDL)
            self._apply_migrations(conn)

    @staticmethod
    def _apply_migrations(conn: sqlite3.Connection) -> None:
        """Forward-only migrations for stores predating a column.

        SQLite's ``CREATE TABLE IF NOT EXISTS`` won't add a column to
        a table that already exists, so columns introduced after the
        original schema have to be backfilled with ``ALTER TABLE``.
        Each migration checks the column list first and is a no-op
        on already-current DBs, so re-running is safe.
        """
        # Phase 20: alignment_results.aligner_kind (existing rows
        # default to 'grid' — only ``align_grid`` existed before).
        cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(alignment_results)"
            ).fetchall()
        }
        if "aligner_kind" not in cols:
            conn.execute(
                "ALTER TABLE alignment_results "
                "ADD COLUMN aligner_kind TEXT NOT NULL DEFAULT 'grid'"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_alignment_results_aligner "
                "ON alignment_results(aligner_kind)"
            )

    # ---------------------------------------------------------- songs
    def upsert_song(
        self,
        song_id: str,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> None:
        """Insert a song row if missing; update metadata if provided.

        Only non-None fields overwrite existing values, so a later
        bundle with richer metadata fills in gaps left by an earlier
        upload that only knew the song_id.
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT artist, title, duration FROM songs WHERE song_id = ?",
                (song_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO songs (song_id, artist, title, duration) "
                    "VALUES (?, ?, ?, ?)",
                    (song_id, artist, title, duration),
                )
                return
            cur_artist, cur_title, cur_duration = row
            conn.execute(
                "UPDATE songs SET artist = ?, title = ?, duration = ? "
                "WHERE song_id = ?",
                (
                    artist if artist is not None else cur_artist,
                    title if title is not None else cur_title,
                    duration if duration is not None else cur_duration,
                    song_id,
                ),
            )

    def get_song(self, song_id: str) -> Optional[Mapping[str, Any]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM songs WHERE song_id = ?", (song_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    # ----------------------------------------------- analysis_results
    def insert_analysis_result(
        self,
        analysis_id: str,
        song_id: str,
        engine_version: str,
        chords: Sequence[Any],
        sections: Sequence[Any],
        tempo: Optional[float],
        key: Optional[str],
        created_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO analysis_results "
                "(analysis_id, song_id, engine_version, chords, sections, "
                " tempo, key, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    analysis_id,
                    song_id,
                    engine_version,
                    json.dumps(list(chords)),
                    json.dumps(list(sections)),
                    tempo,
                    key,
                    created_at,
                ),
            )

    def get_analysis_result(
        self, analysis_id: str
    ) -> Optional[Mapping[str, Any]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM analysis_results WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
            if row is None:
                return None
            out = dict(row)
            out["chords"] = json.loads(out["chords"])
            out["sections"] = json.loads(out["sections"])
            return out

    # ---------------------------------------------------- tab_sources
    def insert_tab_source(
        self,
        tab_id: str,
        song_id: str,
        source: str,
        source_confidence: Optional[float],
        progression: Sequence[Any],
        raw_tab: Optional[str] = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO tab_sources "
                "(tab_id, song_id, source, source_confidence, progression, "
                " raw_tab) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    tab_id,
                    song_id,
                    source,
                    source_confidence,
                    json.dumps(list(progression)),
                    raw_tab,
                ),
            )

    def get_tab_source(self, tab_id: str) -> Optional[Mapping[str, Any]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tab_sources WHERE tab_id = ?", (tab_id,)
            ).fetchone()
            if row is None:
                return None
            out = dict(row)
            out["progression"] = json.loads(out["progression"])
            return out

    # --------------------------------------------- alignment_results
    def insert_alignment_result(
        self,
        alignment_id: str,
        song_id: str,
        analysis_id: str,
        tab_id: str,
        score: Optional[float],
        total_points: int,
        created_at: str,
        aligner_kind: str = "grid",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO alignment_results "
                "(alignment_id, song_id, analysis_id, tab_id, score, "
                " total_points, created_at, aligner_kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alignment_id,
                    song_id,
                    analysis_id,
                    tab_id,
                    score,
                    total_points,
                    created_at,
                    aligner_kind,
                ),
            )

    def get_alignment_result(
        self, alignment_id: str
    ) -> Optional[Mapping[str, Any]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM alignment_results WHERE alignment_id = ?",
                (alignment_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    # -------------------------------------------------- disagreements
    def insert_disagreement(
        self,
        disagreement_id: str,
        song_id: str,
        alignment_id: Optional[str],
        timestamp: float,
        jam_chord: Optional[str],
        tab_chord: Optional[str],
        confidence: Optional[float],
        classification: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO disagreements "
                "(disagreement_id, song_id, alignment_id, timestamp, "
                " jam_chord, tab_chord, confidence, classification) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    disagreement_id,
                    song_id,
                    alignment_id,
                    timestamp,
                    jam_chord,
                    tab_chord,
                    confidence,
                    classification,
                ),
            )

    def list_disagreements_for_alignment(
        self, alignment_id: str
    ) -> list[Mapping[str, Any]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM disagreements WHERE alignment_id = ? "
                "ORDER BY timestamp ASC",
                (alignment_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_disagreement_classification(
        self, disagreement_id: str, classification: str
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE disagreements SET classification = ? "
                "WHERE disagreement_id = ?",
                (classification, disagreement_id),
            )

    # ------------------------------------------------- engine_metrics
    def upsert_engine_metrics(
        self,
        engine_version: str,
        agreement_rate: Optional[float],
        boundary_accuracy: Optional[float],
        slash_chord_accuracy: Optional[float],
        extension_accuracy: Optional[float],
        updated_at: str,
    ) -> None:
        """Insert or replace the row for one engine_version."""
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO engine_metrics "
                "(engine_version, agreement_rate, boundary_accuracy, "
                " slash_chord_accuracy, extension_accuracy, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(engine_version) DO UPDATE SET "
                "  agreement_rate = excluded.agreement_rate, "
                "  boundary_accuracy = excluded.boundary_accuracy, "
                "  slash_chord_accuracy = excluded.slash_chord_accuracy, "
                "  extension_accuracy = excluded.extension_accuracy, "
                "  updated_at = excluded.updated_at",
                (
                    engine_version,
                    agreement_rate,
                    boundary_accuracy,
                    slash_chord_accuracy,
                    extension_accuracy,
                    updated_at,
                ),
            )

    def get_engine_metrics(
        self, engine_version: str
    ) -> Optional[Mapping[str, Any]]:
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM engine_metrics WHERE engine_version = ?",
                (engine_version,),
            ).fetchone()
            return dict(row) if row is not None else None

    def alignments_for_engine_version(
        self, engine_version: str
    ) -> list[Mapping[str, Any]]:
        """Return all alignment rows whose underlying analysis was
        produced by the given engine_version. The metrics roll-up
        joins through analysis_results to filter."""
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT al.* FROM alignment_results al "
                "JOIN analysis_results ar "
                "  ON ar.analysis_id = al.analysis_id "
                "WHERE ar.engine_version = ?",
                (engine_version,),
            ).fetchall()
            return [dict(r) for r in rows]

    def count_disagreements_by_class_for_engine_version(
        self, engine_version: str
    ) -> dict[str, int]:
        """Count disagreement rows grouped by classification for
        alignments whose underlying analysis used the given
        engine_version."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT d.classification, COUNT(*) "
                "FROM disagreements d "
                "JOIN alignment_results al "
                "  ON al.alignment_id = d.alignment_id "
                "JOIN analysis_results ar "
                "  ON ar.analysis_id = al.analysis_id "
                "WHERE ar.engine_version = ? "
                "GROUP BY d.classification",
                (engine_version,),
            ).fetchall()
            return {row[0]: int(row[1]) for row in rows}
