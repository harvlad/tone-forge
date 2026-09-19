"""Vinyl Crate — a shared, curated, read-only donor pool of legally-clean
tracks (CC0 / CC-BY from Jamendo, Free Music Archive, ccMixter).

The crate is the "crate-digging" surface: users SEARCH/BROWSE it by rich
metadata and Jamn MATCHES it to the current session. It reuses the existing
borrow engine (``tone_forge.performance.borrow``) — a crate track's stored
analysis is adapted into a borrow "entry" and fed to the SAME donor ranker
and render/mount path a user's own analyzed songs use. Nothing about the
loop rendering, caching, or client mounting is new; only the *pool* is new
(shared + curated + license-bearing, instead of owner-scoped uploads).

Modules:
    registry — load/serve the shared catalog + license/analysis sidecars,
               and the ``to_borrow_entry`` bridge to the borrow ranker.
    search   — faceted metadata search/browse over the catalog.
    match    — session-aware weighted match ranking (extends borrow's score).
    ingest   — the ingestion pipeline (download → license sidecar → analyze
               → blind-gate → merge → register). Code only; the operator runs
               the batch on the VPS.

Boundary discipline: this is an active (non-frozen) subsystem. It imports
``tone_forge.contracts`` for its DTOs and ``tone_forge.performance.borrow``
(also active) for the shared ranking/render primitives. It is composed only
through ``tone_forge_api``.
"""
from __future__ import annotations

__all__ = ["registry", "search", "match", "ingest"]
