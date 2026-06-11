"""Provider-agnostic stem model for the session engine.

The local engine produces audio stems through a separator (today Demucs)
plus optional post-processing (pan-split, source-classification). Both
the API result and the Jam UI used to consume Demucs-shaped names
directly — "other", "guitar", "guitar_lead" — which couples downstream
code to one provider.

This module defines a stable session-engine view that downstream code
should consume instead:

    Stem
      .id              stable, provider-prefixed identifier
      .role            StemRole — DRUMS / BASS / VOCALS / LEAD / ...
      .display_name    human-readable label for UI
      .audio_url       playback URL
      .midi_url        optional, when a MIDI extractor ran
      .parent_id       set when this stem was derived from another
                       (e.g. pan-split children of "demucs.other")
      .provider        the producer name (e.g. "demucs")
      .confidence      [0, 1] — how sure are we of the role label

Switching providers (htdemucs_6s, source-specific masks, etc.) becomes
"emit a different list of Stem records" with no UI or session-engine
changes required.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional


class StemRole(str, Enum):
    """Musical role of a stem.

    Roles are deliberately coarse — they're routing categories for the
    session engine, not a full instrumentation taxonomy. The UI uses
    these to pick which slot a stem fills (drums slot, bass slot, the
    user's instrument slot, etc.).
    """

    DRUMS = "drums"
    BASS = "bass"
    VOCALS = "vocals"
    # Catch-all harmonic content from a multi-instrument bucket
    # (Demucs' "other" with no usable pan-split).
    HARMONIC = "harmonic"
    # Pan-split / source-classified harmonic content:
    LEAD = "lead"
    RHYTHM = "rhythm"
    TEXTURE = "texture"
    # Keys/piano when surfaced explicitly by a richer separator.
    KEYS = "keys"
    UNKNOWN = "unknown"


# Which roles count as "guitar-family" for the purposes of routing the
# user (when they're playing guitar). The session engine reads this
# rather than substring-matching stem names. Keys is intentionally out:
# a keyboardist plays the keys role.
GUITAR_FAMILY_ROLES = frozenset(
    {StemRole.LEAD, StemRole.RHYTHM, StemRole.TEXTURE, StemRole.HARMONIC}
)


@dataclass
class Stem:
    """Session-engine view of a single audio stem.

    Identity:
        ``id`` is stable across re-runs of the same provider on the
        same source — useful for caching and UI state retention. Use
        provider-prefixed dotted notation (``demucs.other``,
        ``demucs.other.sides``). Children of a split stem use a
        dotted suffix and point at the parent via ``parent_id``.
    """

    id: str
    role: StemRole
    display_name: str
    audio_url: str
    midi_url: Optional[str] = None
    parent_id: Optional[str] = None
    provider: str = "unknown"
    confidence: float = 1.0
    # Free-form metadata bag for provider-specific signals
    # (e.g. {"side_ratio": 0.45, "lr_correlation": 0.10}).
    # Kept out of the equality contract.
    meta: dict = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict:
        out = asdict(self)
        # Enum -> string for JSON
        out["role"] = self.role.value
        return out


# ---------------------------------------------------------------------------
# Display-name + role helpers
# ---------------------------------------------------------------------------

# Default display names per role. Providers can override per-stem by
# constructing the Stem with an explicit display_name.
_DEFAULT_DISPLAY_NAMES: dict = {
    StemRole.DRUMS: "Drums",
    StemRole.BASS: "Bass",
    StemRole.VOCALS: "Vocals",
    StemRole.HARMONIC: "Guitar",
    StemRole.LEAD: "Guitar — lead",
    StemRole.RHYTHM: "Guitar — rhythm",
    StemRole.TEXTURE: "Guitar — texture",
    StemRole.KEYS: "Keys",
    StemRole.UNKNOWN: "Other",
}


def default_display_name(role: StemRole) -> str:
    return _DEFAULT_DISPLAY_NAMES.get(role, role.value)


# Stable display order — drums first, vocals last, user-instrument
# candidates in the middle so they sit visually near the user slot.
_ROLE_DISPLAY_ORDER: List[StemRole] = [
    StemRole.DRUMS,
    StemRole.BASS,
    StemRole.KEYS,
    StemRole.HARMONIC,
    StemRole.LEAD,
    StemRole.RHYTHM,
    StemRole.TEXTURE,
    StemRole.UNKNOWN,
    StemRole.VOCALS,
]


def role_sort_key(role: StemRole) -> int:
    try:
        return _ROLE_DISPLAY_ORDER.index(role)
    except ValueError:
        return len(_ROLE_DISPLAY_ORDER)


def is_guitar_family(role: StemRole) -> bool:
    return role in GUITAR_FAMILY_ROLES
