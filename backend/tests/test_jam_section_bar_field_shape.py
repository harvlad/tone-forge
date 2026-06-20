"""Regression: ``buildSectionBar`` must accept ``start_s`` / ``end_s``.

The JAM client has two section-loading paths and the two paths ship
different field names:

* **Streaming analyze** (``/api/analyze-url`` → ``onAnalysisComplete``)
  delivers ``ArrangementSection.to_dict()`` shape:
  ``{type, start_time, end_time, ...}``.
* **Deep-link reload** (``/jam/:id`` → ``assembleBundleFrame``) projects
  ``SongUnderstanding.sections``, whose ``Section`` contract dicts use
  ``{label, start_s, end_s, ...}`` — see ``backend/tone_forge/session/
  bundle.py:570``.

``buildSectionBar`` in ``backend/static/jam.js`` resolves both shapes via
a fallback chain. If ``start_s`` / ``end_s`` ever drops out of that
chain, every deep-link section chip silently renders ``0:00`` (because
``secondsOf(undefined)`` returns 0) while the labels keep working
(``label`` is in the label chain).

That is the exact failure pattern that motivated this test: session
``126c9515`` rendered 22 chips all stamped ``0:00`` after deep-link
load even though the persisted bundle held correct ``start_time``
values. The root cause was the JS fallback chain checking
``start_time → start → start_sec → startSec`` but **not** ``start_s``,
which is the field name the SessionBundle contract emits.

This file pins the JS fallback chain so a future refactor can't drop
``start_s`` / ``end_s`` and silently re-break deep-link sections.
"""
from __future__ import annotations

from pathlib import Path

JAM_JS = Path(__file__).resolve().parent.parent / "static" / "jam.js"


def _read_jam_js() -> str:
    return JAM_JS.read_text(encoding="utf-8")


def test_section_bar_start_chain_includes_start_s():
    src = _read_jam_js()
    # The chain lives inside buildSectionBar. We don't anchor on
    # specific whitespace/order — we just require that the substring
    # ``s.start_s`` appears alongside the streaming-path field
    # ``s.start_time`` near each other (within 200 chars) so we know
    # both shapes are accepted by the same expression.
    idx = src.find("s.start_time")
    assert idx != -1, "jam.js must read s.start_time for the streaming-path shape"
    window = src[idx : idx + 200]
    assert "s.start_s" in window, (
        "jam.js buildSectionBar must accept s.start_s in its fallback chain; "
        "without it, deep-link sessions render every chip at 0:00. See "
        "backend/tone_forge/session/bundle.py:570 for the contract that "
        "emits start_s."
    )


def test_section_bar_end_chain_includes_end_s():
    src = _read_jam_js()
    idx = src.find("s.end_time")
    assert idx != -1, "jam.js must read s.end_time for the streaming-path shape"
    window = src[idx : idx + 200]
    assert "s.end_s" in window, (
        "jam.js buildSectionBar must accept s.end_s in its fallback chain; "
        "without it, deep-link sessions render zero-length section loops."
    )


def test_section_bar_label_chain_includes_label():
    """Sanity guard: the label chain must continue to accept ``label``.

    The label path didn't regress in the start_s/end_s incident, but
    pinning all three together keeps the dual-shape contract
    explicit in one place.
    """
    src = _read_jam_js()
    # Label fallback resolves on the same section dict as start/end —
    # require both label-emitting field names to appear in the same
    # local scope as the start chain.
    idx = src.find("s.start_time")
    assert idx != -1
    # Look backward a bit too, since the label assignment sits just
    # above the start assignment in buildSectionBar.
    window = src[max(0, idx - 600) : idx + 200]
    assert "s.label" in window, (
        "jam.js buildSectionBar must accept s.label in its label fallback "
        "chain; SessionBundle Section emits label (see contracts.py:211)."
    )
