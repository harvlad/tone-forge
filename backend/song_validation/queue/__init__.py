"""File-based work queue: offline async ingestion + drain.

The directive's "async / offline / batch mode" plumbing. Connect
clients (or fetch jobs, or backfill scripts) drop analysis bundles
and tab-source payloads as JSON files in an ``inbox/`` directory; a
worker drains the queue without touching the runtime path.

The queue is intentionally **filesystem-only**: no message broker, no
in-process channel, no shared state with the runtime API. That keeps
the runtime fully unaware of validation and lets the worker pool live
on different hardware entirely.

Layout under ``queue_dir``::

    queue_dir/
        inbox/      one JSON envelope per pending item
        done/       successfully processed items
        failed/     items that failed validation/ingestion + a
                    ``<name>.error.json`` sidecar with the exception

Envelope schema::

    {
        "kind": "analysis_bundle" | "tab_source",
        "payload": { ... ingestion-module-shaped ... }
    }

Writes to ``inbox/`` are atomic: the file is written to
``<name>.json.tmp`` first, then renamed into place.

Public surface:

- :func:`enqueue_bundle` -- write an analysis-bundle envelope.
- :func:`enqueue_tab`    -- write a tab-source envelope.
- :func:`drain_queue`    -- one worker pass; ingests every inbox
  item, moves it to ``done/`` or ``failed/``, optionally fires the
  validation pipeline for songs that now have both sides present.
- :class:`QueueError`    -- raised for malformed envelopes / bad
  ``queue_dir`` arguments.
"""

from __future__ import annotations

from .file_queue import (
    QueueError,
    drain_queue,
    enqueue_bundle,
    enqueue_tab,
)

__all__ = [
    "QueueError",
    "drain_queue",
    "enqueue_bundle",
    "enqueue_tab",
]
