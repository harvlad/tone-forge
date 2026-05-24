"""Dataclasses for the Tone Descriptor.

Mirrors `schemas/tone_descriptor.schema.json`. Keep them in sync.
The schema is canonical for cross-language consumers; these classes
are for ergonomic use inside the Python pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Literal


AmpFamily = Literal[
    "fender_clean", "vox_chime", "marshall_plexi", "marshall_jcm",
    "mesa_rectifier", "5150_peavey", "bogner", "soldano", "ac30",
    "tweed", "dumble", "unknown",
]

CabConfig = Literal["1x12", "2x12", "4x10", "4x12", "unknown"]
SpeakerChar = Literal["v30_like", "g12h_like", "g12m_like", "alnico_blue_like", "jensen_like", "unknown"]


@dataclass
class Source:
    kind: Literal["isolated_guitar", "stem_separated", "full_mix"]
    duration_sec: float
    sample_rate: Optional[int] = None
    filename: Optional[str] = None


@dataclass
class Guitar:
    pickup_brightness: float = 0.5
    playing_style: str = "unknown"
    estimated_tuning: str = "unknown"


@dataclass
class Voicing:
    bass: float
    mid: float
    treble: float
    presence: float
    mid_scoop: float = 0.0


@dataclass
class Amp:
    family: AmpFamily
    gain: float
    voicing: Voicing
    alternates: list = field(default_factory=list)  # list of dicts: {family, score}


@dataclass
class Cab:
    configuration: CabConfig
    speaker_character: SpeakerChar
    mic_position: str = "unknown"


@dataclass
class OverdrivePedal:
    style: str = "unknown"
    drive: float = 0.0
    level: float = 0.5


@dataclass
class Compressor:
    amount: float = 0.0
    character: str = "unknown"


@dataclass
class Modulation:
    type: str = "none"
    rate: float = 0.0
    depth: float = 0.0


@dataclass
class Delay:
    type: str = "none"
    time_ms: float = 0.0
    feedback: float = 0.0
    mix: float = 0.0


@dataclass
class Reverb:
    type: str = "none"
    size: float = 0.0
    mix: float = 0.0


@dataclass
class Effects:
    overdrive_pedal: Optional[OverdrivePedal] = None
    compressor: Optional[Compressor] = None
    modulation: Optional[Modulation] = None
    delay: Optional[Delay] = None
    reverb: Optional[Reverb] = None


@dataclass
class Confidence:
    amp_family: float
    gain: float
    cab: float
    effects: float


@dataclass
class ToneDescriptor:
    source: Source
    guitar: Guitar
    amp: Amp
    cab: Cab
    effects: Effects
    confidence: Confidence
    version: str = "0.1.0"
    reasoning: Optional[Any] = field(default=None, repr=False)  # DescriptorReasoning when captured
    provenance: Optional[dict] = field(default=None, repr=False)  # Provenance tracking summary

    def to_dict(self) -> dict:
        # Temporarily clear complex objects to avoid asdict issues
        reasoning = self.reasoning
        provenance = self.provenance
        self.reasoning = None
        self.provenance = None
        d = asdict(self)
        self.reasoning = reasoning
        self.provenance = provenance

        # Strip None effect blocks for cleaner JSON.
        d["effects"] = {k: v for k, v in d["effects"].items() if v is not None}

        # Convert reasoning to dict if present
        if reasoning is not None:
            try:
                d["reasoning"] = reasoning.to_dict() if hasattr(reasoning, "to_dict") else None
            except Exception:
                d["reasoning"] = None
        else:
            d["reasoning"] = None

        # Include provenance if present
        d["provenance"] = provenance

        return d
