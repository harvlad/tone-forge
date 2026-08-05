"""DuckDB analysis layer over the Parquet caches.

Usage:
    from lab.db import connect
    con = connect()
    con.sql("SELECT inst_class_gm, count(*) FROM corpus GROUP BY 1").show()

Views (created on demand, all read-only over Parquet):
    corpus       — the stem manifest
    gt_notes     — all cached GT notes (joined to stem via midi_hash)
    predictions  — all cached predictions (joined to stem/model via sidecars)
    matches      — all cached match tables
"""
from __future__ import annotations

import json

import duckdb

from . import config
from .gt import GT_PARSER_VERSION


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()  # in-memory; data stays in Parquet
    if config.MANIFEST_PATH.exists():
        con.sql(f"CREATE VIEW corpus AS SELECT * FROM read_parquet('{config.MANIFEST_PATH}')")
    gt_glob = config.GT_DIR / GT_PARSER_VERSION / "*.parquet"
    if any((config.GT_DIR / GT_PARSER_VERSION).glob("*.parquet")) if (config.GT_DIR / GT_PARSER_VERSION).is_dir() else False:
        con.sql(f"""
            CREATE VIEW gt_notes AS
            SELECT regexp_extract(filename, '([0-9a-f]+)\\.parquet', 1) AS midi_hash, *
            FROM read_parquet('{gt_glob}', filename=true)
        """)
    _register_predictions(con)
    match_glob = config.MATCHES_DIR / "*.parquet"
    if config.MATCHES_DIR.is_dir() and any(config.MATCHES_DIR.glob("*.parquet")):
        con.sql(f"""
            CREATE VIEW matches AS
            SELECT regexp_extract(filename, '([0-9a-f]+)\\.parquet', 1) AS match_key, *
            FROM read_parquet('{match_glob}', filename=true)
        """)
    return con


def _register_predictions(con) -> None:
    """Predictions need their sidecar metadata (stem_id/model) joined in;
    build a small meta table from the JSON sidecars, then join by key."""
    rows = []
    if not config.PREDICTIONS_DIR.is_dir():
        return
    for meta_path in config.PREDICTIONS_DIR.glob("*/*.json"):
        try:
            m = json.loads(meta_path.read_text())
        except Exception:
            continue
        if m.get("status") != "ok":
            continue
        rows.append((m["key"], m["model_id"], m.get("model_version", ""),
                     m["stem_id"], m.get("provenance", ""), m.get("n_notes", 0)))
    if not rows:
        return
    con.sql("CREATE TABLE _pred_meta (key VARCHAR, model_id VARCHAR, model_version VARCHAR, stem_id VARCHAR, provenance VARCHAR, n_notes INT)")
    con.executemany("INSERT INTO _pred_meta VALUES (?, ?, ?, ?, ?, ?)", rows)
    pred_glob = config.PREDICTIONS_DIR / "*" / "*.parquet"
    con.sql(f"""
        CREATE VIEW predictions AS
        SELECT m.model_id, m.model_version, m.stem_id, m.provenance, p.*
        FROM read_parquet('{pred_glob}', filename=true) p
        JOIN _pred_meta m
          ON regexp_extract(p.filename, '([0-9a-f]+)\\.parquet', 1) = m.key
    """)
