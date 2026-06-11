"""ToneForge platform contracts.

This module is the *only* legitimate carrier of types across subsystem
boundaries. Subsystems may not import from each other directly; they
import from ``tone_forge.contracts`` and dispatch through the API
composition layer (``tone_forge_api``).

The DTOs below mirror the platform architecture described in
``/EXECUTION_PLAN.md``. They are deliberately small, frozen, and free of
behavior. Add a field here before you add a corresponding field to any
subsystem function signature.

Conventions:
    * Times in seconds (``*_s`` suffix) unless otherwise stated.
    * Confidences in ``[0, 1]``.
    * Enums are subclasses of ``str`` so they JSON-serialize cleanly.
    * Dataclasses use ``frozen=True`` to make accidental mutation hard.
    * The catch-all ``Dict[str, Any]`` fields are escape hatches for
      provider-specific metadata. They are *not* a license to grow
      a coupling surface — anything that becomes load-bearing across
      a subsystem boundary should be promoted to a typed field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# Stem is the long-standing session-engine view of an audio stem.
# It is owned by stem_model.py and reused as-is here — do not re-define.
from tone_forge.stem_model import Stem

__all__ = [
    # Enums
    "ContentType",
    "UserRole",
    "ConfidenceTier",
    "DeviceClass",
    "MonitorChainFamily",
    # Core platform DTOs
    "AcquiredAudio",
    "StemSet",
    "Chord",
    "Section",
    "Motif",
    "SongUnderstanding",
    "InstrumentMIDI",
    "ToneCandidate",
    "ToneMatch",
    "ToneRecommendation",
    "ToneRecMatch",
    "ToneRecFallback",
    "ToneRecAlternate",
    "ToneRecApply",
    "MonitorChain",
    "DeviceCaps",
    "AudioDeviceInfo",
    "DeviceProbe",
    "DevicePreferences",
    "TransportState",
    "GuidanceTrack",
    "SessionBundle",
    # Re-exports for convenience
    "Stem",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ContentType(str, Enum):
    """Top-level classification of an incoming audio source."""

    SONG_MIX = "song_mix"
    ISOLATED_STEM = "isolated_stem"


class UserRole(str, Enum):
    """Instrument the user is playing inside the session.

    MVP supports GUITAR. BASS and KEYS are scoped for Phase 2.
    VOCALS is intentionally out of scope.
    """

    GUITAR = "guitar"
    BASS = "bass"
    KEYS = "keys"
    VOCALS = "vocals"  # reserved; out of scope for the foreseeable


class ConfidenceTier(str, Enum):
    """Coarse retrieval confidence tier driving UX behavior.

    See ``EXECUTION_PLAN.md`` §7 for the policy that maps a calibrated
    confidence + margin onto a tier.
    """

    HIGH = "high"        # auto-apply, no interruption
    MEDIUM = "medium"    # suggest top + alternates
    LOW = "low"          # fall back to curated monitor chain
    UNKNOWN = "unknown"  # retrieval not attempted or failed


class DeviceClass(str, Enum):
    """What the user is playing through.

    Discovery answers this question; integration adapters consume it.
    MVP only branches on INTERFACE_ONLY vs. modeler-class devices.
    """

    INTERFACE_ONLY = "interface_only"
    HELIX = "helix"
    QUAD_CORTEX = "quad_cortex"
    KEMPER = "kemper"
    FRACTAL = "fractal"
    TONEX = "tonex"
    NEURAL_DSP = "neural_dsp"
    CONNECT_MONITOR = "connect_monitor"
    NO_HARDWARE = "no_hardware"
    OTHER = "other"


class MonitorChainFamily(str, Enum):
    """Tonal family of a curated monitor chain.

    The chain bank ships one chain per family at MVP. Variants within
    a family (pickup type, amp character) are a Phase-2 expansion.
    """

    CLEAN = "clean"
    EDGE_OF_BREAKUP = "edge_of_breakup"
    CLASSIC_ROCK = "classic_rock"
    MODERN_GAIN = "modern_gain"
    AMBIENT = "ambient"


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquiredAudio:
    """Result of pulling audio in from a URL or upload.

    ``content_hash`` is the cache key — re-pasting the same URL should
    produce the same hash and skip re-download/re-decode.
    """

    wav_path: str
    sample_rate: int
    duration_s: float
    content_hash: str
    source_kind: str  # "url" | "upload"
    source_uri: Optional[str] = None
    source_title: Optional[str] = None


# ---------------------------------------------------------------------------
# Stems
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StemSet:
    """Bundle of stems for one acquired source.

    Composes the existing ``Stem`` records from ``stem_model``. Slots
    may be ``None`` when separation didn't produce a usable stem for
    that role (e.g. pan-split guitar isn't always extractable).
    """

    drums: Optional[Stem] = None
    bass: Optional[Stem] = None
    vocals: Optional[Stem] = None
    other: Optional[Stem] = None
    guitar_left: Optional[Stem] = None
    guitar_right: Optional[Stem] = None
    # Extra stems the pipeline emits that don't fit the fixed slots
    # (e.g. ``guitar_texture``, ``guitar_texture_2``, ``guitar_rhythm``,
    # ``piano``). The session bundle preserves them verbatim so the
    # client can render every stem the analysis actually produced
    # rather than the six this contract enumerates.
    extras: Tuple[Stem, ...] = ()
    content_hash: str = ""  # provenance back to AcquiredAudio


# ---------------------------------------------------------------------------
# Song Understanding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chord:
    """A single chord region on the timeline."""

    start_s: float
    end_s: float
    symbol: str  # e.g. "Cmaj7", "F#m", "G"
    confidence: float = 1.0


@dataclass(frozen=True)
class Section:
    """An arrangement section (intro / verse / chorus / etc.)."""

    start_s: float
    end_s: float
    label: str
    confidence: float = 1.0


@dataclass(frozen=True)
class Motif:
    """A repeating phrase or riff fingerprint.

    Phase-3 Song Understanding artifact. Present here so consumers can
    render stubs without DTO changes later.
    """

    start_s: float
    end_s: float
    fingerprint: str
    occurrences_s: Tuple[float, ...] = ()
    confidence: float = 1.0


@dataclass(frozen=True)
class SongUnderstanding:
    """Everything we know about the song's musical structure.

    MVP populates: tempo, key, sections, chords. Phase-3 fields
    (tuning, capo, difficulty, motifs) are declared here so they can
    be filled in without DTO churn.
    """

    tempo_bpm: float
    tempo_confidence: float
    time_signature: Tuple[int, int]  # (numerator, denominator), e.g. (4, 4)
    beats_s: Tuple[float, ...]
    downbeats_s: Tuple[float, ...]
    sections: Tuple[Section, ...]
    chords: Tuple[Chord, ...]
    key: Optional[str] = None  # e.g. "C major", "A minor"
    key_confidence: float = 0.0
    # Phase 3 — none of these populated in MVP:
    tuning: Optional[str] = None  # "standard" | "drop_d" | ...
    capo_fret: Optional[int] = None
    difficulty: Optional[float] = None
    motifs: Tuple[Motif, ...] = ()


# ---------------------------------------------------------------------------
# MIDI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentMIDI:
    """MIDI extracted for one stem / one instrument role.

    Wraps the existing ``MIDIExtractionResult`` shape without
    surfacing its internals. Consumers should treat ``notes`` as
    opaque and consult ``raw`` only for debugging.
    """

    role: UserRole
    notes: Tuple[Dict[str, Any], ...]
    overall_confidence: float
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tone (retrieval + monitor chains)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToneCandidate:
    """One retrieval result with calibrated confidence."""

    preset_id: str
    preset_name: str
    instrument: str  # "Analog" | "Drift" | ...
    distance: float
    calibrated_confidence: float  # [0, 1] after calibration
    audio_preview_url: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToneMatch:
    """Retrieval outcome shaped for the auto / suggest / fallback policy.

    Exactly one of ``chosen`` (HIGH/MEDIUM) or ``fallback_chain_id``
    (LOW) is populated. UNKNOWN populates ``fallback_chain_id`` too.
    """

    tier: ConfidenceTier
    chosen: Optional[ToneCandidate] = None
    alternates: Tuple[ToneCandidate, ...] = ()
    fallback_chain_id: Optional[str] = None
    rationale: str = ""
    debug: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ToneRecommendation — wire-shape for the Jam UI Tone card.
#
# ToneMatch is the internal policy object. ToneRecommendation is the
# external contract a client consumes. Two reasons to keep them separate:
#
#   1. The internal object can evolve (calibrator interface, debug shape,
#      new tier enum values) without forcing a wire-format change.
#   2. The wire object enforces the XOR invariant on `match` / `fallback`
#      that the UI relies on. Internal ToneMatch tracks the two cases
#      with a tier enum + nullable fields, which is the right shape for
#      Python policy code but the wrong shape for a JSON consumer.
#
# Invariant (enforced in ToneRecommendation.to_dict()):
#     exactly one of `match` or `fallback` is non-None.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToneRecMatch:
    """Confident-or-suggested match attached to a ToneRecommendation."""

    chain_id: str
    display_name: str
    archetype: str
    distance: float
    confidence: float


@dataclass(frozen=True)
class ToneRecFallback:
    """Fallback chain attached when no match is confident enough."""

    chain_id: str
    display_name: str
    archetype: str
    reason: str  # "empty_candidates" | "low_confidence" | "tempo_default" | ...


@dataclass(frozen=True)
class ToneRecAlternate:
    """Runner-up shown when tier is MEDIUM."""

    chain_id: str
    display_name: str
    archetype: str
    distance: float


@dataclass(frozen=True)
class ToneRecApply:
    """Resolved Apply-button payload.

    Always populated. UI dispatches by `action` without conditional
    logic on tier, eliminating a class of "Apply button does nothing"
    bugs.
    """

    chain_id: str
    action: str = "connect.apply_chain"


@dataclass(frozen=True)
class ToneRecommendation:
    """UI-facing recommendation envelope (Jam Tone card)."""

    tier: ConfidenceTier
    rationale: str
    apply: ToneRecApply
    match: Optional[ToneRecMatch] = None
    fallback: Optional[ToneRecFallback] = None
    alternates: Tuple[ToneRecAlternate, ...] = ()
    preview_url: Optional[str] = None
    debug: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # XOR invariant. Asserted in __post_init__ rather than runtime
        # because emitting an invalid recommendation is a programmer
        # error in the API composition layer, not user input.
        if (self.match is None) == (self.fallback is None):
            raise ValueError(
                "ToneRecommendation requires exactly one of match/fallback "
                f"to be set (got match={self.match!r}, fallback={self.fallback!r})."
            )


@dataclass(frozen=True)
class MonitorChain:
    """Curated tone delivery chain executed by Connect.

    Chain specs live in ``backend/tone_forge/monitor/chains/``.
    Parameters are forwarded as-is to Connect, which constructs the
    AVAudioEngine graph deterministically.
    """

    id: str
    family: MonitorChainFamily
    display_name: str
    description: str
    parameters: Dict[str, Any]


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceCaps:
    """What capabilities the user's playback path supports.

    Discovery populates this. Retrieval consults it to decide whether
    to emit a preset (for adapter-capable devices) or a monitor chain
    (everyone else).
    """

    cls: DeviceClass
    display_name: str
    can_monitor: bool
    can_receive_preset: bool
    preferred_chain_family: Optional[MonitorChainFamily] = None
    vendor_hint: Optional[str] = None
    model_hint: Optional[str] = None


@dataclass(frozen=True)
class AudioDeviceInfo:
    """One CoreAudio device as reported by the ``connect devices`` probe.

    The Connect CLI is the source of truth for what the OS sees; the
    Python probe just parses its JSON output. ``input_channels`` and
    ``output_channels`` of zero are legal — many devices are one-way.
    """

    device_id: int
    name: str
    input_channels: int
    output_channels: int


@dataclass(frozen=True)
class DeviceProbe:
    """Result of the non-blocking discovery probe.

    The plan (EXECUTION_PLAN.md §7) describes this as a hint surface
    rather than a decision: the UI uses ``suggested_input`` to pre-fill
    the onboarding question, and ``vendor_hint`` to label the row.
    ``device_class`` stays ``None`` here — Discovery never *answers*
    the onboarding question, the user does. The probe just narrows the
    interface choices.

    Frozen so a probe can be cached and shared without callers
    accidentally mutating it (the UI tends to keep one in memory for
    the lifetime of the onboarding flow).
    """

    devices: Tuple[AudioDeviceInfo, ...]
    suggested_input: Optional[AudioDeviceInfo] = None
    vendor_hint: Optional[str] = None
    probe_succeeded: bool = True
    error_message: Optional[str] = None


@dataclass(frozen=True)
class DevicePreferences:
    """User's persisted answer to the Discovery onboarding question.

    Schema mirrors ``~/Library/Application Support/ToneForge/device.json``
    as defined in EXECUTION_PLAN.md §8. The plan calls for re-prompting
    only when ``device_class`` is unset or the user explicitly opens
    settings, so this dataclass is the single source of truth the UI
    consults to decide whether to show the onboarding screen.

    Frozen so the persistence layer can return cached instances safely.
    """

    device_class: DeviceClass
    audio_input_name: Optional[str] = None
    preferred_chain_family: Optional[MonitorChainFamily] = None
    first_seen_iso: Optional[str] = None
    last_used_iso: Optional[str] = None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportState:
    """Canonical transport state owned by the session engine.

    UI dispatches intents; the engine produces this state; Connect and
    the UI subscribe. There is no other source of truth.
    """

    playing: bool
    position_s: float
    tempo_pct: float  # 0.5 .. 1.0 (no >100% in MVP)
    loop_in_s: Optional[float] = None
    loop_out_s: Optional[float] = None
    user_mute: bool = True  # user's stem muted by default
    monitor_gain: float = 0.0  # 0..1, muted on initial pair


@dataclass(frozen=True)
class GuidanceTrack:
    """Information the Jam UI renders during playback.

    MVP renders ``sections`` and ``chord_lane``. The other fields
    are reserved for later phases.
    """

    sections: Tuple[Section, ...]
    chord_lane: Tuple[Chord, ...]
    upcoming_chord_lookahead_beats: int = 0  # Phase 2
    note_highway: Tuple[Dict[str, Any], ...] = ()  # Phase 3


@dataclass(frozen=True)
class SessionBundle:
    """Everything the Jam UI needs to start a session.

    This is the contract for the new ``/api/session/:id`` route. It
    intentionally does *not* match the legacy ``AnalysisResult.to_dict``
    surface used by Studio. Studio keeps its existing contract; Jam
    consumes this.
    """

    session_id: str
    audio: AcquiredAudio
    stems: StemSet
    understanding: SongUnderstanding
    user_role: UserRole
    user_midi: Optional[InstrumentMIDI]
    tone: ToneMatch
    guidance: GuidanceTrack
    device_caps: DeviceCaps
    initial_transport: TransportState
