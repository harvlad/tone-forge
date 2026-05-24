"""Factory for creating stem-specific pipelines."""
from __future__ import annotations

from typing import Dict, Optional, Type

from .base import StemPipeline, PipelineConfig
from .lead import LeadPipeline
from .bass import BassPipeline
from .pad import PadPipeline
from .guitar import GuitarPipeline
from .arp import ArpPipeline


# Registry of pipeline classes by name
PIPELINE_REGISTRY: Dict[str, Type[StemPipeline]] = {
    "lead": LeadPipeline,
    "bass": BassPipeline,
    "pad": PadPipeline,
    "guitar": GuitarPipeline,
    "arp": ArpPipeline,
}

# Mapping from stem type hints to pipelines
STEM_TYPE_MAPPING: Dict[str, str] = {
    # Direct mappings
    "lead": "lead",
    "bass": "bass",
    "pad": "pad",
    "pads": "pad",
    "guitar": "guitar",
    "arp": "arp",
    "arps": "arp",
    "arpeggio": "arp",

    # Aliases
    "melody": "lead",
    "vocal": "lead",  # Treat vocals like leads
    "vocals": "lead",
    "synth": "lead",  # Default synth to lead
    "synth_lead": "lead",
    "synth_bass": "bass",
    "synth_pad": "pad",
    "keys": "pad",  # Default keys to pad
    "piano": "guitar",  # Piano is polyphonic like guitar
    "strings": "pad",
    "brass": "lead",
    "pluck": "arp",

    # Catchall
    "other": "lead",
    "unknown": "lead",
}


def get_pipeline_for_stem(
    stem_type: str,
    config: Optional[PipelineConfig] = None,
) -> StemPipeline:
    """Get appropriate pipeline for a stem type.

    Args:
        stem_type: Type of stem (e.g., "lead", "bass", "pad")
        config: Optional custom configuration

    Returns:
        Appropriate StemPipeline instance
    """
    stem_type_lower = stem_type.lower()

    # Look up in mapping
    pipeline_name = STEM_TYPE_MAPPING.get(stem_type_lower, "lead")

    # Get pipeline class
    pipeline_class = PIPELINE_REGISTRY.get(pipeline_name, LeadPipeline)

    return pipeline_class(config)


def get_pipeline_by_name(
    name: str,
    config: Optional[PipelineConfig] = None,
) -> StemPipeline:
    """Get pipeline by its name.

    Args:
        name: Pipeline name (e.g., "lead", "bass")
        config: Optional custom configuration

    Returns:
        StemPipeline instance

    Raises:
        ValueError: If pipeline name is not found
    """
    name_lower = name.lower()

    if name_lower not in PIPELINE_REGISTRY:
        available = list(PIPELINE_REGISTRY.keys())
        raise ValueError(f"Unknown pipeline '{name}'. Available: {available}")

    pipeline_class = PIPELINE_REGISTRY[name_lower]
    return pipeline_class(config)


def list_available_pipelines() -> Dict[str, str]:
    """List available pipelines with descriptions.

    Returns:
        Dict mapping pipeline name to description
    """
    return {
        "lead": "Melody and lead instruments - preserves staccato and expression",
        "bass": "Bass instruments - monophonic focus with octave correction",
        "pad": "Pads and sustained sounds - harmonic suppression and chord handling",
        "guitar": "Guitar and polyphonic instruments - strumming and chord support",
        "arp": "Arpeggios and sequences - strong pattern preservation and quantization",
    }
