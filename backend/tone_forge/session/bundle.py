"""SessionBundle assembly from the legacy ``AnalysisResult`` dict shape.

Jam UI needs a single payload (``SessionBundle``) that carries every
field required to render the band room. Today the pipeline produces an
``AnalysisResult`` dataclass; ``/api/history/{id}`` persists its
``to_dict()`` output to disk.

This module is the translation layer between those two shapes. It runs
at the API edge (P5d): ``GET /api/session/:id`` fetches the history
entry, calls ``build()`` here, and serializes the result.

Boundary discipline:

* This subsystem may not import from ``unified_pipeline`` or any other
  subsystem's internals. The boundary test in
  ``tests/test_subsystem_boundaries.py`` enforces that. So ``build()``
  takes a ``Mapping[str, Any]`` (the persisted dict) rather than the
  ``AnalysisResult`` dataclass — the API layer is responsible for
  passing the right shape.
* The assembler is lenient. Anything missing or malformed produces a
  conservative default rather than raising — the goal is to deliver
  a renderable bundle even for partially-analyzed sources. Strictness
  would have to come later, once Priorities 4/6/7 fill in the gaps
  (chords, tone matching, device caps) that are currently empty.

Confidence-tier policy and chord detection are out of scope here.
Those layers (P6 ``tone/``, P4 chord pipeline wire-up) feed into the
same SessionBundle when ready; ``build()`` will pick up populated
fields automatically without code changes.
"""

from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from tone_forge.contracts import (
    AcquiredAudio,
    Chord,
    ConfidenceTier,
    DeviceCaps,
    DeviceClass,
    GuidanceTrack,
    InstrumentMIDI,
    Section,
    SessionBundle,
    SongUnderstanding,
    StemSet,
    ToneMatch,
    TransportState,
    UserRole,
)
from tone_forge.session.transport import initial_state
from tone_forge.stem_model import Stem, StemRole


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build(
    result: Mapping[str, Any],
    session_id: str,
    *,
    user_role: Optional[UserRole] = None,
    device_caps: Optional[DeviceCaps] = None,
    initial_transport: Optional[TransportState] = None,
    tone_match: Optional[ToneMatch] = None,
) -> SessionBundle:
    """Assemble a ``SessionBundle`` from a persisted ``AnalysisResult`` dict.

    ``result`` is the dict shape produced by ``AnalysisResult.to_dict()``
    and persisted in ``backend/data/history.json``. The keys we read
    are the long-standing ones; new keys (chords, device probe) are
    consulted when present and ignored otherwise.

    ``user_role`` defaults to GUITAR when neither the dict nor the
    explicit override picks. The Jam MVP is guitar-only by plan; this
    default is the safe choice that matches the §10 scope.

    ``device_caps`` defaults to an interface-only profile so the
    LOW-tier fallback chain path works before Priority 7 ships device
    discovery.

    ``initial_transport`` defaults to a stopped/muted state via
    ``session.transport.initial_state()``. Per §6, reload-restore of
    the last position is a P5d concern; the bundle just carries
    whatever state the dispatch layer hands in.

    ``tone_match`` is dependency-injected from the API edge — that is
    where ``tone.retrieve()`` runs, since the boundary test forbids
    the session/ subsystem from importing tone/. When ``None`` (no
    injection), the assembler falls back to a conservative UNKNOWN
    tier so legacy callers still get a renderable bundle.
    """

    resolved_role = user_role or _resolve_user_role(result)
    resolved_caps = device_caps or _default_device_caps()
    resolved_transport = initial_transport or initial_state()

    return SessionBundle(
        session_id=session_id,
        audio=_build_audio(result),
        stems=_build_stems(result),
        understanding=_build_understanding(result),
        user_role=resolved_role,
        user_midi=_build_user_midi(result, resolved_role),
        tone=tone_match if tone_match is not None else _build_tone(result, resolved_role),
        guidance=_build_guidance(result),
        device_caps=resolved_caps,
        initial_transport=resolved_transport,
    )


# ---------------------------------------------------------------------------
# Field builders
# ---------------------------------------------------------------------------

def _build_audio(result: Mapping[str, Any]) -> AcquiredAudio:
    """Reconstruct AcquiredAudio from the persisted dict.

    ``content_hash`` defaults to an empty string when the legacy result
    didn't carry one — the contract type allows that (it's still a str),
    but downstream cache-keys built on the hash will skip if it's empty.
    """

    source_url = _str_or_none(result.get("source_url"))
    source_kind = "url" if source_url else "upload"

    wav_path = _str_or_none(result.get("wav_path")) or ""
    duration = _safe_float(result.get("duration_sec"), default=0.0)
    sample_rate = _safe_int(result.get("sample_rate"), default=44100)
    content_hash = _str_or_none(result.get("content_hash")) or ""
    source_title = _str_or_none(result.get("source_name"))

    return AcquiredAudio(
        wav_path=wav_path,
        sample_rate=sample_rate,
        duration_s=duration,
        content_hash=content_hash,
        source_kind=source_kind,
        source_uri=source_url,
        source_title=source_title,
    )


_FIXED_STEM_SLOTS: Mapping[str, StemRole] = {
    "drums": StemRole.DRUMS,
    "bass": StemRole.BASS,
    "vocals": StemRole.VOCALS,
    "other": StemRole.HARMONIC,
    "guitar_left": StemRole.LEAD,
    "guitar_right": StemRole.RHYTHM,
}


# Best-effort role guess for stems the pipeline emits beyond the fixed
# slots. Anything not matched here falls back to HARMONIC — the client
# still renders the stem; the slot just doesn't get a specific role hint.
_EXTRA_STEM_ROLES: Mapping[str, StemRole] = {
    "guitar": StemRole.HARMONIC,
    "guitar_lead": StemRole.LEAD,
    "guitar_rhythm": StemRole.RHYTHM,
    "guitar_texture": StemRole.TEXTURE,
    "piano": StemRole.KEYS,
    "keys": StemRole.KEYS,
}


def _role_for_extra_stem(name: str) -> StemRole:
    if name in _EXTRA_STEM_ROLES:
        return _EXTRA_STEM_ROLES[name]
    # Variants like ``guitar_texture_2`` — keep the texture-ish family.
    # Walk prefixes longest-first so ``guitar_texture_`` wins over
    # ``guitar_`` (which would otherwise demote texture variants to
    # the harmonic catch-all).
    for prefix in sorted(_EXTRA_STEM_ROLES, key=len, reverse=True):
        if name.startswith(prefix + "_"):
            return _EXTRA_STEM_ROLES[prefix]
    return StemRole.HARMONIC


def _build_stems(result: Mapping[str, Any]) -> StemSet:
    """Convert the legacy ``stems`` / ``stems_paths`` dict into a StemSet.

    The persisted dict shape is ``{"drums": "<url-or-path>", ...}``. We
    can't reconstruct the full provider-rich ``Stem`` from a path alone,
    so we synthesize minimal records with the role implied by the dict
    key. Provider is recorded as ``"legacy"`` so call sites can detect
    the conversion.

    Stems beyond the six fixed contract slots (``guitar_texture``,
    ``guitar_texture_2``, ``guitar_rhythm``, ``piano``, ...) are
    preserved on the ``extras`` field — dropping them would make
    deep-link refresh lose stems the analysis actually produced.
    """

    paths = result.get("stems") or result.get("stems_paths") or {}
    if not isinstance(paths, Mapping):
        return StemSet()

    fixed: dict[str, Optional[Stem]] = {}
    for name, role in _FIXED_STEM_SLOTS.items():
        fixed[name] = _stem_from_path(name, paths.get(name), role)

    extras: list[Stem] = []
    for name, path in paths.items():
        if name in _FIXED_STEM_SLOTS:
            continue
        stem = _stem_from_path(name, path, _role_for_extra_stem(name))
        if stem is not None:
            extras.append(stem)

    content_hash = _str_or_none(result.get("content_hash")) or ""

    return StemSet(
        drums=fixed["drums"],
        bass=fixed["bass"],
        vocals=fixed["vocals"],
        other=fixed["other"],
        guitar_left=fixed["guitar_left"],
        guitar_right=fixed["guitar_right"],
        extras=tuple(extras),
        content_hash=content_hash,
    )


def _stem_from_path(
    name: str, path: Any, role: StemRole
) -> Optional[Stem]:
    """Wrap a stem path into a minimal Stem record, or None if absent."""
    audio_url = _str_or_none(path)
    if not audio_url:
        return None
    return Stem(
        id=f"legacy.{name}",
        role=role,
        display_name=name.replace("_", " ").title(),
        audio_url=audio_url,
        provider="legacy",
        confidence=1.0,
    )


def _build_understanding(result: Mapping[str, Any]) -> SongUnderstanding:
    """Extract tempo/key/sections/chords with conservative defaults.

    Legacy AnalysisResult does not carry tempo / key at the top level —
    they live inside the per-instrument ``guitar`` / ``bass`` /
    ``descriptor`` blobs. We try the common locations and fall back to
    safe defaults rather than fabricating a confidence we don't have.
    """

    tempo, tempo_conf = _resolve_tempo(result)
    key, key_conf = _resolve_key(result)

    sections = tuple(_iter_sections(result.get("sections")))
    chords = tuple(_iter_chords(result.get("chords")))
    # Phase 6 hybrid grid: optional beat-snapped chord array. Absent /
    # null on the wire collapses to an empty tuple so the UI toggle
    # stays hidden on legacy sessions without beats.
    chords_beat_snapped = tuple(_iter_chords(result.get("chords_beat_snapped")))

    return SongUnderstanding(
        tempo_bpm=tempo,
        tempo_confidence=tempo_conf,
        key=key,
        key_confidence=key_conf,
        time_signature=(4, 4),
        beats_s=tuple(_iter_floats(result.get("beats_s"))),
        downbeats_s=tuple(_iter_floats(result.get("downbeats_s"))),
        sections=sections,
        chords=chords,
        chords_beat_snapped=chords_beat_snapped,
    )


def _build_user_midi(
    result: Mapping[str, Any], role: UserRole
) -> Optional[InstrumentMIDI]:
    """Wrap the existing MIDI extraction blob into an InstrumentMIDI.

    Reads ``midi`` first, then falls back to ``midi_stems[<role>]``.
    Returns None if neither is populated — Jam handles the absence
    gracefully (no note-highway today anyway; that's Phase 3).
    """

    midi = result.get("midi")
    if not isinstance(midi, Mapping) or not midi:
        stems = result.get("midi_stems")
        if isinstance(stems, Mapping):
            midi = stems.get(role.value) or stems.get("guitar")
    if not isinstance(midi, Mapping):
        return None

    notes = midi.get("notes")
    if not isinstance(notes, Iterable):
        return None
    notes_tuple = tuple(n for n in notes if isinstance(n, Mapping))
    if not notes_tuple:
        return None

    confidence = _safe_float(midi.get("overall_confidence"), default=0.0)
    return InstrumentMIDI(
        role=role,
        notes=notes_tuple,
        overall_confidence=confidence,
        raw={"source": "legacy_analysis_result"},
    )


def _build_tone(result: Mapping[str, Any], role: UserRole) -> ToneMatch:
    """Fallback used only when no ``tone_match`` is dependency-injected.

    The API edge calls ``tone.retrieve()`` and passes the result into
    ``build(..., tone_match=...)``. This helper covers the path where
    a caller (legacy test, ad-hoc usage) doesn't inject one — we emit a
    conservative UNKNOWN tier so the UI takes the curated-chain
    fallback path rather than rendering a stale top-1 dict.
    """

    matches = result.get("preset_matches")
    if not isinstance(matches, Mapping):
        return _unknown_tone()

    role_block = matches.get(role.value)
    if not isinstance(role_block, Mapping):
        return _unknown_tone()

    # Legacy preset_matches shape isn't tier-aware; carry the raw blob
    # into debug for telemetry but treat the match itself as UNKNOWN.
    return ToneMatch(
        tier=ConfidenceTier.UNKNOWN,
        chosen=None,
        alternates=(),
        fallback_chain_id=None,
        rationale="No tone_match injected; legacy preset_match ignored.",
        debug={"legacy_preset_match": dict(role_block)},
    )


def _build_guidance(result: Mapping[str, Any]) -> GuidanceTrack:
    return GuidanceTrack(
        sections=tuple(_iter_sections(result.get("sections"))),
        chord_lane=tuple(_iter_chords(result.get("chords"))),
    )


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------

def _resolve_user_role(result: Mapping[str, Any]) -> UserRole:
    """Pick the user role from the legacy ``detected_type`` / ``type`` field."""
    raw = result.get("detected_type") or result.get("type")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in (r.value for r in UserRole):
            return UserRole(normalized)
    return UserRole.GUITAR


def _resolve_tempo(result: Mapping[str, Any]) -> tuple[float, float]:
    """Walk the common legacy locations for a tempo value."""
    for path in (
        ("descriptor", "tempo"),
        ("descriptor", "tempo_bpm"),
        ("guitar", "tempo"),
        ("bass", "tempo"),
    ):
        value = _nested_get(result, path)
        bpm = _safe_float(value, default=None)
        if bpm is not None and bpm > 0:
            return bpm, 0.5  # conservative confidence; we don't track it
    return 0.0, 0.0


def _resolve_key(result: Mapping[str, Any]) -> tuple[Optional[str], float]:
    for path in (
        ("descriptor", "key"),
        ("guitar", "key"),
        ("bass", "key"),
    ):
        value = _nested_get(result, path)
        if isinstance(value, str) and value:
            return value, 0.5
    return None, 0.0


def _default_device_caps() -> DeviceCaps:
    """Interface-only profile. Replaced when Priority 7 ships."""
    return DeviceCaps(
        cls=DeviceClass.INTERFACE_ONLY,
        display_name="Audio interface",
        can_monitor=True,
        can_receive_preset=False,
    )


def _unknown_tone() -> ToneMatch:
    return ToneMatch(
        tier=ConfidenceTier.UNKNOWN,
        chosen=None,
        alternates=(),
        fallback_chain_id=None,
        rationale="No preset match available.",
        debug={},
    )


# ---------------------------------------------------------------------------
# Iter helpers
# ---------------------------------------------------------------------------

def _first_present(item: Mapping[str, Any], *keys: str) -> Any:
    """Return the first key's value that is present and non-None.

    Plain ``item.get(k1) or item.get(k2)`` is wrong here because the
    legitimate start-of-song value 0.0 is falsy and would be skipped.
    """
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _iter_sections(raw: Any) -> Iterable[Section]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        start = _safe_float(_first_present(item, "start_s", "start"), default=None)
        end = _safe_float(_first_present(item, "end_s", "end"), default=None)
        if start is None or end is None:
            continue
        label = _str_or_none(item.get("label")) or "section"
        conf = _safe_float(item.get("confidence"), default=0.5) or 0.5
        yield Section(start_s=start, end_s=end, label=label, confidence=conf)


def _iter_chords(raw: Any) -> Iterable[Chord]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        start = _safe_float(_first_present(item, "start_s", "start"), default=None)
        end = _safe_float(_first_present(item, "end_s", "end"), default=None)
        symbol = _str_or_none(_first_present(item, "symbol", "chord"))
        if start is None or end is None or not symbol:
            continue
        conf = _safe_float(item.get("confidence"), default=0.5) or 0.5
        yield Chord(start_s=start, end_s=end, symbol=symbol, confidence=conf)


def _iter_floats(raw: Any) -> Iterable[float]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return
    for item in raw:
        coerced = _safe_float(item, default=None)
        if coerced is not None:
            yield coerced


# ---------------------------------------------------------------------------
# Coercion utilities
# ---------------------------------------------------------------------------

def _safe_float(value: Any, *, default: Optional[float]) -> Optional[float]:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, *, default: int) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _str_or_none(value: Any) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    return None


def _nested_get(d: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def serialize(bundle: SessionBundle) -> dict:
    """Convert a SessionBundle to a JSON-ready dict.

    ``dataclasses.asdict`` handles the frozen dataclass tree, but it
    leaves ``Enum`` instances in place — even our str-Enum subclasses,
    because ``asdict`` does not unwrap. We walk the resulting tree and
    convert enums to their ``.value`` so the payload is round-trippable
    through any JSON encoder (FastAPI's included).
    """
    return _jsonify(asdict(bundle))


def _jsonify(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    return obj


__all__ = ["build", "serialize"]
