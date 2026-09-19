"""Faceted metadata search/browse over the crate catalog.

Two orthogonal entry points share the same ``CrateTrack`` set: ``match``
answers "what fits my session" (session-ranked); this answers "let me dig
the crate" (unranked catalog view, filtered + free-text). The picker composes
them — a ranked "For your session" strip over a faceted "Browse the crate"
grid — and the same facet filter can be applied PRE-ranking so the two models
stack rather than compete.

No embeddings: the retrieval-embedding path is frozen, and a lexical
substring/token match over title/artist/album/tags/genre/mood is the honest
tool for a curated catalog of this size.

All facets read straight off the manifest (``CrateTrack`` + ``CrateFeatures``)
so browsing never triggers an analysis re-run. Facets AND across categories,
OR within a multi-valued one.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from tone_forge.contracts import CrateTrack
from tone_forge.performance import borrow

# ---------------------------------------------------------------------------
# Camelot wheel — a pure mapping over borrow._parse_key. Major keys sit on the
# "B" ring, minor on the "A" ring; the number is the position on the wheel.
# Neighbourhood = same number (relative maj/minor) or ±1 number on the same
# ring (adjacent on the circle of fifths) — the standard harmonic-mixing set.
# ---------------------------------------------------------------------------
# tonic pitch-class → Camelot number for MAJOR keys (minor shares the number).
_CAMELOT_MAJOR = {
    0: 8,   # C
    7: 9,   # G
    2: 10,  # D
    9: 11,  # A
    4: 12,  # E
    11: 1,  # B
    6: 2,   # F#
    1: 3,   # C#/Db
    8: 4,   # G#/Ab
    3: 5,   # D#/Eb
    10: 6,  # A#/Bb
    5: 7,   # F
}


def key_to_camelot(key_str: Optional[str]) -> Optional[str]:
    """'C major' → '8B', 'A minor' → '8A'. None when unparseable.

    A minor key shares its Camelot NUMBER with its relative major (A minor ↔ C
    major = 8), so a minor tonic must be resolved through its relative major's
    pitch class (``(tonic + 3) % 12``) — NOT looked up as if it were a major
    tonic. Looking A (pc 9) up directly would wrongly yield 11 (A major)."""
    parsed = borrow._parse_key(key_str)
    if parsed is None:
        return None
    tonic, is_minor = parsed
    ref_pc = (tonic + 3) % 12 if is_minor else tonic  # relative major for minors
    num = _CAMELOT_MAJOR.get(ref_pc)
    if num is None:
        return None
    return f"{num}{'A' if is_minor else 'B'}"


def _camelot_neighbourhood(code: str) -> set:
    """The harmonic-mixing neighbourhood of a Camelot code: itself, its
    relative (same number, flipped letter), and ±1 on the same ring."""
    code = code.strip().upper()
    if len(code) < 2 or code[-1] not in ("A", "B"):
        return {code}
    try:
        num = int(code[:-1])
    except ValueError:
        return {code}
    letter = code[-1]
    lo = 12 if num == 1 else num - 1
    hi = 1 if num == 12 else num + 1
    other = "A" if letter == "B" else "B"
    return {f"{num}{letter}", f"{num}{other}", f"{lo}{letter}", f"{hi}{letter}"}


# ---------------------------------------------------------------------------
# Free-text
# ---------------------------------------------------------------------------


def _text_blob(t: CrateTrack) -> str:
    parts = [t.title, t.artist, t.album, t.genre, t.mood]
    parts += list(t.tags) + list(t.subgenres)
    return " ".join(p for p in parts if p).lower()


def _text_score(t: CrateTrack, tokens: Sequence[str]) -> float:
    """Relevance: token coverage, with title/artist hits weighted heavier so a
    query that names the track/artist floats it up."""
    if not tokens:
        return 0.0
    blob = _text_blob(t)
    strong = f"{t.title} {t.artist}".lower()
    score = 0.0
    for tok in tokens:
        if tok in strong:
            score += 2.0
        elif tok in blob:
            score += 1.0
    return score


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def _matches(t: CrateTrack, *, genre, tags, mood, tempo_min, tempo_max,
             key, camelot, stems, has_vocals, license_ids, clean_export,
             duration_min, duration_max, tokens) -> bool:
    if genre and t.genre.strip().lower() != genre.strip().lower():
        return False
    if mood and t.mood.strip().lower() != mood.strip().lower():
        return False
    if tags:
        want = {x.strip().lower() for x in tags if x.strip()}
        have = {x.lower() for x in t.tags} | {x.lower() for x in t.subgenres}
        if not (want & have):   # OR within the facet
            return False
    bpm = t.features.tempo_bpm
    if tempo_min is not None and bpm < tempo_min:
        return False
    if tempo_max is not None and bpm > tempo_max:
        return False
    if key:
        tk = (t.features.detected_key or "").strip().lower()
        if tk != key.strip().lower():
            return False
    if camelot:
        tc = key_to_camelot(t.features.detected_key)
        if tc is None or tc not in _camelot_neighbourhood(camelot):
            return False
    if stems:
        want = {x.strip().lower() for x in stems if x.strip()}
        have = {x.lower() for x in t.features.available_stems}
        if not want.issubset(have):   # must-include ALL requested stems
            return False
    if has_vocals is not None and t.features.has_vocals != has_vocals:
        return False
    if clean_export and t.license.export_encumbered:
        return False
    if license_ids:
        want = {x.strip().upper() for x in license_ids if x.strip()}
        if t.license.license_id.value.upper() not in want:
            return False
    dur = t.features.duration_s
    if duration_min is not None and dur < duration_min:
        return False
    if duration_max is not None and dur > duration_max:
        return False
    if tokens:
        blob = _text_blob(t)
        if not all(tok in blob for tok in tokens):
            return False
    return True


# ---------------------------------------------------------------------------
# Facet counts
# ---------------------------------------------------------------------------


def _facet_counts(tracks: List[CrateTrack]) -> Dict[str, Dict[str, int]]:
    """Per-facet available counts over the filtered set so the UI can render a
    faceted sidebar (genre→N, mood→N, key→N, camelot→N, license→N, stem→N)."""
    counts: Dict[str, Dict[str, int]] = {
        "genre": {}, "mood": {}, "key": {}, "camelot": {},
        "license": {}, "stem": {},
    }

    def bump(cat: str, val: str) -> None:
        if val:
            counts[cat][val] = counts[cat].get(val, 0) + 1

    for t in tracks:
        bump("genre", t.genre)
        bump("mood", t.mood)
        bump("key", t.features.detected_key or "")
        bump("camelot", key_to_camelot(t.features.detected_key) or "")
        bump("license", t.license.license_id.value)
        for s in t.features.available_stems:
            bump("stem", s)
    return counts


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_SORTS = ("relevance", "tempo", "recency", "title")


def search_crate(tracks: List[CrateTrack], *, q: Optional[str] = None,
                 genre: Optional[str] = None,
                 tags: Optional[Sequence[str]] = None,
                 mood: Optional[str] = None,
                 tempo_min: Optional[float] = None,
                 tempo_max: Optional[float] = None,
                 key: Optional[str] = None,
                 camelot: Optional[str] = None,
                 stems: Optional[Sequence[str]] = None,
                 has_vocals: Optional[bool] = None,
                 license_ids: Optional[Sequence[str]] = None,
                 clean_export: bool = False,
                 duration_min: Optional[float] = None,
                 duration_max: Optional[float] = None,
                 sort: str = "relevance",
                 limit: int = 50, offset: int = 0) -> Dict:
    """Faceted browse over the crate. Returns
    ``{tracks: [CrateTrack…], facetCounts: {…}, total: N}`` — ``tracks`` are
    the parsed CrateTrack objects (the caller serializes via
    ``registry.track_to_dict``, which carries the mandatory attribution).
    ``total`` and ``facetCounts`` are over the full filtered set (pre-
    pagination); ``tracks`` is the ``[offset:offset+limit]`` page.
    """
    tokens = [tok for tok in (q or "").lower().split() if tok]

    filtered = [t for t in tracks if _matches(
        t, genre=genre, tags=tags, mood=mood, tempo_min=tempo_min,
        tempo_max=tempo_max, key=key, camelot=camelot, stems=stems,
        has_vocals=has_vocals, license_ids=license_ids,
        clean_export=clean_export, duration_min=duration_min,
        duration_max=duration_max, tokens=tokens)]

    facet_counts = _facet_counts(filtered)
    total = len(filtered)

    sort = sort if sort in _SORTS else "relevance"
    if sort == "tempo":
        filtered.sort(key=lambda t: t.features.tempo_bpm)
    elif sort == "title":
        filtered.sort(key=lambda t: t.title.lower())
    elif sort == "recency":
        # newest acquisition first (ISO-8601 sorts lexically).
        filtered.sort(key=lambda t: t.license.acquired_at, reverse=True)
    elif tokens:  # relevance, only meaningful with a query
        filtered.sort(key=lambda t: _text_score(t, tokens), reverse=True)
    # else: default browse keeps the manifest's curated order.

    if offset > 0:
        filtered = filtered[offset:]
    if limit is not None and limit >= 0:
        filtered = filtered[:limit]

    return {"tracks": filtered, "facetCounts": facet_counts, "total": total}
