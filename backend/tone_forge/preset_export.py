"""Preset export for various platforms.

Exports signal chain recommendations to loadable preset formats:
- Helix (.hlx) - Line 6 Helix/HX preset format
- JSON - Generic format for custom import tools
- Neural DSP - Quad Cortex compatible format
- Ableton Live (.als) - Native Ableton project files
"""
from __future__ import annotations

import base64
import gzip
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .descriptor import ToneDescriptor

logger = logging.getLogger(__name__)


@dataclass
class ExportedPreset:
    """An exported preset ready for download."""
    filename: str
    format: str
    content: str  # JSON string or binary depending on format
    content_type: str


def export_helix_preset(
    chain: list[dict],
    descriptor: ToneDescriptor,
    preset_name: str = "Tone Forge Export",
) -> ExportedPreset:
    """Export signal chain to Helix .hlx format.

    The .hlx format is JSON with a specific structure that Helix Native
    and hardware units can import.
    """
    # Map our block IDs to Helix model IDs
    # Helix uses specific model numbers internally

    dsp_blocks = []
    block_position = 0

    for pick in chain:
        if pick.get("slot") == "amp_alt":
            continue  # Skip alternates for export

        helix_block = _convert_to_helix_block(pick, block_position)
        if helix_block:
            dsp_blocks.append(helix_block)
            block_position += 1

    # Build the Helix preset structure
    preset = {
        "version": 6,
        "data": {
            "meta": {
                "name": preset_name,
                "application": "Tone Forge",
                "build_sha": "toneforge",
                "modifieddate": int(datetime.now().timestamp()),
                "giession": str(uuid.uuid4()),
            },
            "device": 2162689,  # Helix Floor device ID (0x210001)
            "tone": {
                "dsp0": {
                    "block0": dsp_blocks[0] if len(dsp_blocks) > 0 else _empty_block(),
                    "block1": dsp_blocks[1] if len(dsp_blocks) > 1 else _empty_block(),
                    "block2": dsp_blocks[2] if len(dsp_blocks) > 2 else _empty_block(),
                    "block3": dsp_blocks[3] if len(dsp_blocks) > 3 else _empty_block(),
                    "block4": dsp_blocks[4] if len(dsp_blocks) > 4 else _empty_block(),
                    "block5": dsp_blocks[5] if len(dsp_blocks) > 5 else _empty_block(),
                    "block6": dsp_blocks[6] if len(dsp_blocks) > 6 else _empty_block(),
                    "block7": dsp_blocks[7] if len(dsp_blocks) > 7 else _empty_block(),
                    "inputA": {"@input": 1, "@model": "HD2_AppDSPFlowInput", "noiseGate": False},
                    "outputA": {"@model": "HD2_AppDSPFlowOutput", "@output": 1, "gain": 0, "pan": 0.5},
                    "split": {"@model": "HD2_AppDSPFlowSplitY", "@position": 0},
                    "join": {"@model": "HD2_AppDSPFlowJoinY", "@position": 8},
                },
                "dsp1": {},
                "global": {
                    "@current_snapshot": 0,
                    "@cursor_dsp": 0,
                    "@cursor_path": 0,
                    "@cursor_position": 0,
                    "@cursor_group": "block0",
                    "@tempo": 120,
                    "@topology": 0,
                },
                "footswitch": {},
                "controller": {},
            },
            "snapshot0": _default_snapshot(),
            "snapshot1": _default_snapshot(),
            "snapshot2": _default_snapshot(),
            "snapshot3": _default_snapshot(),
        },
        "schema": "L6Preset",
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}.hlx"

    return ExportedPreset(
        filename=filename,
        format="hlx",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def export_hx_stomp_preset(
    chain: list[dict],
    descriptor: ToneDescriptor,
    preset_name: str = "Tone Forge Export",
) -> ExportedPreset:
    """Export signal chain to HX Stomp .hlx format.

    The HX Stomp has 6 blocks max (vs 32 on Helix Floor).
    Prioritizes: Amp > Cab > Drive > Delay > Reverb > Modulation
    """
    # Priority order for Stomp's limited blocks
    priority_slots = ['amp', 'cab', 'drive', 'delay', 'reverb', 'modulation']

    dsp_blocks = []
    block_position = 0

    # Sort chain by priority
    sorted_chain = []
    for slot in priority_slots:
        for pick in chain:
            if pick.get("slot") == slot and pick.get("slot") != "amp_alt":
                sorted_chain.append(pick)
                break  # Only one of each type

    # Take only first 6 blocks for Stomp
    for pick in sorted_chain[:6]:
        helix_block = _convert_to_helix_block(pick, block_position)
        if helix_block:
            dsp_blocks.append(helix_block)
            block_position += 1

    # Build the HX Stomp preset structure
    preset = {
        "version": 6,
        "data": {
            "meta": {
                "name": preset_name[:16],  # Stomp has shorter name limit
                "application": "Tone Forge",
                "build_sha": "toneforge",
                "modifieddate": int(datetime.now().timestamp()),
                "giession": str(uuid.uuid4()),
            },
            "device": 2162694,  # HX Stomp device ID (0x210006)
            "tone": {
                "dsp0": {
                    "block0": dsp_blocks[0] if len(dsp_blocks) > 0 else _empty_block(),
                    "block1": dsp_blocks[1] if len(dsp_blocks) > 1 else _empty_block(),
                    "block2": dsp_blocks[2] if len(dsp_blocks) > 2 else _empty_block(),
                    "block3": dsp_blocks[3] if len(dsp_blocks) > 3 else _empty_block(),
                    "block4": dsp_blocks[4] if len(dsp_blocks) > 4 else _empty_block(),
                    "block5": dsp_blocks[5] if len(dsp_blocks) > 5 else _empty_block(),
                    "inputA": {"@input": 1, "@model": "HD2_AppDSPFlowInput", "noiseGate": False},
                    "outputA": {"@model": "HD2_AppDSPFlowOutput", "@output": 1, "gain": 0, "pan": 0.5},
                },
                "global": {
                    "@current_snapshot": 0,
                    "@cursor_dsp": 0,
                    "@cursor_path": 0,
                    "@cursor_position": 0,
                    "@cursor_group": "block0",
                    "@tempo": 120,
                    "@topology": 0,
                },
                "footswitch": {},
                "controller": {},
            },
            "snapshot0": _default_snapshot(),
            "snapshot1": _default_snapshot(),
            "snapshot2": _default_snapshot(),
        },
        "schema": "L6Preset",
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}_stomp.hlx"

    return ExportedPreset(
        filename=filename,
        format="hlx_stomp",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def _convert_to_helix_block(pick: dict, position: int) -> dict:
    """Convert a BlockPick to Helix block format."""
    slot = pick.get("slot", "")
    block_id = pick.get("block_id", "")
    params = pick.get("params", {})

    # Map our block IDs to Helix model strings
    model_map = {
        # Amps
        "amp.us_double_nrm": "HD2_AmpUSDoubleNrm",
        "amp.us_deluxe_nrm": "HD2_AmpUSDeluxeNrm",
        "amp.essex_a30": "HD2_AmpEssexA30",
        "amp.brit_plexi_brt": "HD2_AmpBritPlexiBrt",
        "amp.brit_2204": "HD2_AmpBrit2204",
        "amp.cali_rectifire": "HD2_AmpCaliRectifire",
        "amp.pv_panama": "HD2_AmpPVPanama",
        "amp.solo_lead_od": "HD2_AmpSoloLeadOD",
        "amp.litigator": "HD2_AmpLitigator",
        "amp.tweed_blues_nrm": "HD2_AmpTweedBluesNrm",
        # Cabs
        "cab.1x12_us_deluxe": "HD2_Cab1x12USDeluxe",
        "cab.2x12_blue_bell": "HD2_Cab2x12BlueBell",
        "cab.4x12_greenback_25": "HD2_Cab4x12Greenback25",
        "cab.4x12_xxl_v30": "HD2_Cab4x12XXLV30",
        "cab.4x12_uber_v30": "HD2_Cab4x12UberV30",
        # Drives
        "drive.scream_808": "HD2_DistScream808",
        "drive.minotaur": "HD2_DistMinotaur",
        "drive.teemah": "HD2_DistTeemah",
        "drive.vermin_dist": "HD2_DistVerminDist",
        # Delays
        "delay.digital": "HD2_DelayDigital",
        "delay.vintage_digital": "HD2_DelayVintageDigital",
        "delay.transistor_tape": "HD2_DelayTransistorTape",
        "delay.elephant_man": "HD2_DelayElephantMan",
        # Reverbs
        "reverb.glitz": "HD2_ReverbGlitz",
        "reverb.ganymede": "HD2_ReverbGanymede",
        "reverb.searchlights": "HD2_ReverbSearchlights",
        "reverb.plateaux": "HD2_ReverbPlateaux",
        # Modulation
        "mod.optical_trem": "HD2_TremOpticalTrem",
        "mod.script_phase": "HD2_PhaserScriptPhase",
        "mod.gray_flanger": "HD2_FlangerGrayFlanger",
        "mod.trinity_chorus": "HD2_ChorusTrinityChorus",
    }

    # Get model or use a default
    model = model_map.get(block_id)
    if not model:
        # Try to construct a reasonable model name
        if slot == "amp":
            model = "HD2_AmpUSDeluxeNrm"  # Default amp
        elif slot == "cab":
            model = "HD2_Cab1x12USDeluxe"  # Default cab
        elif slot == "drive":
            model = "HD2_DistScream808"
        elif slot == "delay":
            model = "HD2_DelayDigital"
        elif slot == "reverb":
            model = "HD2_ReverbGlitz"
        elif slot == "modulation":
            model = "HD2_ChorusTrinityChorus"
        else:
            return None

    # Convert params to Helix format (0-1 range)
    helix_params = {}
    for k, v in params.items():
        # Most Helix params are 0-1, our params are often 0-10
        if isinstance(v, (int, float)):
            helix_params[k.title().replace("_", "")] = v / 10.0

    return {
        "@model": model,
        "@path": 0,
        "@position": position,
        "@type": 0 if slot in ["amp", "cab"] else 1,
        **helix_params,
    }


def _empty_block() -> dict:
    """Return an empty/bypassed block."""
    return {
        "@model": "HD2_AppDSPFlowBlock",
        "@path": 0,
        "@position": 0,
        "@type": 0,
    }


def _default_snapshot() -> dict:
    """Return a default snapshot structure."""
    return {
        "@name": "SNAPSHOT 1",
        "@tempo": 120,
        "controllers": {},
        "blocks": {},
    }


def export_json_preset(
    chain: list[dict],
    descriptor_dict: dict,
    preset_name: str = "Tone Forge Export",
) -> ExportedPreset:
    """Export to generic JSON format.

    This format can be used by custom import tools or as a reference
    for manual preset creation.
    """
    preset = {
        "name": preset_name,
        "version": "1.0",
        "created": datetime.now().isoformat(),
        "generator": "Tone Forge",
        "analysis": {
            "amp_family": descriptor_dict.get("amp", {}).get("family"),
            "gain": descriptor_dict.get("amp", {}).get("gain"),
            "confidence": descriptor_dict.get("confidence", {}),
        },
        "signal_chain": [
            {
                "slot": p.get("slot"),
                "block": p.get("display"),
                "block_id": p.get("block_id"),
                "parameters": p.get("params", {}),
                "rationale": p.get("rationale"),
            }
            for p in chain
            if p.get("slot") != "amp_alt"
        ],
        "full_descriptor": descriptor_dict,
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}.json"

    return ExportedPreset(
        filename=filename,
        format="json",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def export_neural_dsp_preset(
    chain: list[dict],
    descriptor_dict: dict,
    preset_name: str = "Tone Forge Export",
) -> ExportedPreset:
    """Export to Neural DSP Quad Cortex format.

    The Quad Cortex uses a JSON-based preset format.
    """
    # Map amp families to Neural DSP capture/model suggestions
    amp_family = descriptor_dict.get("amp", {}).get("family", "unknown")

    neural_amp_map = {
        "fender_clean": "US Double Nrm",
        "tweed": "Tweed B-Man",
        "vox_chime": "AC30 TB",
        "marshall_plexi": "Brit 800",
        "marshall_jcm": "Brit 2203",
        "mesa_rectifier": "Cali Rectifire",
        "5150_peavey": "PV 5150",
        "bogner": "German Ubersonic",
        "soldano": "Solo 100 Lead",
        "dumble": "D-Cell Smooth",
    }

    suggested_amp = neural_amp_map.get(amp_family, "US Double Nrm")

    preset = {
        "name": preset_name,
        "version": "1.0",
        "format": "neural_dsp_qc",
        "created": datetime.now().isoformat(),
        "generator": "Tone Forge",
        "suggested_models": {
            "amp": suggested_amp,
            "cab": "4x12 V30" if descriptor_dict.get("cab", {}).get("speaker_character") == "v30_like" else "1x12 Deluxe",
        },
        "parameters": {
            "gain": descriptor_dict.get("amp", {}).get("gain", 0.5),
            "bass": descriptor_dict.get("amp", {}).get("voicing", {}).get("bass", 0.5),
            "mid": descriptor_dict.get("amp", {}).get("voicing", {}).get("mid", 0.5),
            "treble": descriptor_dict.get("amp", {}).get("voicing", {}).get("treble", 0.5),
            "presence": descriptor_dict.get("amp", {}).get("voicing", {}).get("presence", 0.5),
        },
        "effects": [
            {
                "type": p.get("slot"),
                "suggested": p.get("display"),
                "parameters": p.get("params", {}),
            }
            for p in chain
            if p.get("slot") not in ["amp", "cab", "amp_alt"]
        ],
        "notes": f"Based on detected {amp_family} tone. Adjust capture/model to taste.",
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}_qc.json"

    return ExportedPreset(
        filename=filename,
        format="neural_dsp",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def export_synth_preset(
    synth_descriptor: dict,
    preset_name: str = "Tone Forge Synth",
    target: str = "serum",
) -> ExportedPreset:
    """Export synth analysis to a preset format.

    Targets:
    - serum: Xfer Serum compatible format
    - vital: Vital synth format
    - generic: Generic JSON for any synth
    """
    osc = synth_descriptor.get("oscillator", {})
    filt = synth_descriptor.get("filter", {})
    env = synth_descriptor.get("amp_envelope", {})
    lfo = synth_descriptor.get("lfo", {})

    # Map oscillator types
    osc_type_map = {
        "saw": "Sawtooth",
        "square": "Square",
        "sine": "Sine",
        "triangle": "Triangle",
        "noise": "Noise",
    }

    preset = {
        "name": preset_name,
        "version": "1.0",
        "format": f"synth_{target}",
        "created": datetime.now().isoformat(),
        "generator": "Tone Forge",
        "oscillator": {
            "type": osc_type_map.get(osc.get("type", "saw"), "Sawtooth"),
            "voices": osc.get("num_voices", 1),
            "detune_cents": osc.get("detune", 0),
            "sub_oscillator": osc.get("sub_osc", False),
        },
        "filter": {
            "type": filt.get("type", "lowpass"),
            "cutoff_hz": filt.get("cutoff_hz", 20000),
            "cutoff_normalized": filt.get("cutoff_normalized", 1.0),
            "resonance": filt.get("resonance", 0),
        },
        "amp_envelope": {
            "attack_ms": env.get("attack_ms", 10),
            "decay_ms": env.get("decay_ms", 100),
            "sustain": env.get("sustain", 0.8),
            "release_ms": env.get("release_ms", 200),
        },
        "modulation": {
            "lfo_rate_hz": lfo.get("rate_hz", 0) if lfo else 0,
            "lfo_depth": lfo.get("depth", 0) if lfo else 0,
            "lfo_target": lfo.get("target", "none") if lfo else "none",
        },
        "effects": {
            "chorus": synth_descriptor.get("has_chorus", False),
            "phaser": synth_descriptor.get("has_phaser", False),
            "reverb": synth_descriptor.get("has_reverb", False),
            "delay": synth_descriptor.get("has_delay", False),
        },
        "notes": "Import these values manually or use as reference for patch creation.",
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}_{target}.json"

    return ExportedPreset(
        filename=filename,
        format=f"synth_{target}",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def export_bass_preset(
    bass_descriptor: dict,
    recommendations: list[dict],
    preset_name: str = "Tone Forge Bass",
) -> ExportedPreset:
    """Export bass analysis to a preset format.

    Includes amp/pedal recommendations and parameter settings.
    """
    amp = bass_descriptor.get("amp", {})
    cab = bass_descriptor.get("cab", {})
    effects = bass_descriptor.get("effects", {})

    preset = {
        "name": preset_name,
        "version": "1.0",
        "format": "bass",
        "created": datetime.now().isoformat(),
        "generator": "Tone Forge",
        "analysis": {
            "amp_family": amp.get("family", "unknown"),
            "gain": amp.get("gain", 0.5),
            "technique": bass_descriptor.get("technique", "fingerstyle"),
        },
        "amp_settings": {
            "family": amp.get("family", "ampeg_svt"),
            "gain": amp.get("gain", 0.5),
            "bass": amp.get("voicing", {}).get("bass", 0.5),
            "low_mid": amp.get("voicing", {}).get("low_mid", 0.5),
            "high_mid": amp.get("voicing", {}).get("high_mid", 0.5),
            "treble": amp.get("voicing", {}).get("treble", 0.5),
        },
        "cabinet": {
            "config": cab.get("config", "4x10"),
            "character": cab.get("character", "modern"),
        },
        "effects": {
            "compression": effects.get("compression", 0),
            "overdrive": effects.get("overdrive", 0),
            "chorus": effects.get("chorus", 0),
            "octaver": effects.get("octaver", 0),
        },
        "recommendations": [
            {
                "slot": r.get("slot"),
                "gear": r.get("display"),
                "price": r.get("price_estimate"),
                "rationale": r.get("rationale"),
                "parameters": r.get("params", {}),
            }
            for r in recommendations
        ],
        "notes": "Bass gear recommendations based on spectral analysis.",
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}_bass.json"

    return ExportedPreset(
        filename=filename,
        format="bass",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def export_drums_preset(
    drums_descriptor: dict,
    machine_match: dict,
    preset_name: str = "Tone Forge Drums",
) -> ExportedPreset:
    """Export drum analysis to a preset format.

    Includes drum machine recommendations and parameter settings.
    """
    kick = drums_descriptor.get("kick", {})
    snare = drums_descriptor.get("snare", {})
    hihat = drums_descriptor.get("hihat", {})
    overall = drums_descriptor.get("overall", {})

    preset = {
        "name": preset_name,
        "version": "1.0",
        "format": "drums",
        "created": datetime.now().isoformat(),
        "generator": "Tone Forge",
        "analysis": {
            "tempo_bpm": overall.get("tempo_bpm", drums_descriptor.get("tempo_bpm", 120)),
            "style": overall.get("style", "electronic"),
            "swing": overall.get("swing", drums_descriptor.get("swing", 0)),
        },
        "kick": {
            "pitch_hz": kick.get("pitch_hz", 60),
            "decay_ms": kick.get("decay_ms", 200),
            "saturation": kick.get("saturation", 0.3),
            "sub_presence": kick.get("sub_presence", 0.5),
            "click": kick.get("click", 0.3),
        },
        "snare": {
            "pitch_hz": snare.get("pitch_hz", 200),
            "noise": snare.get("noise", 0.5),
            "snap": snare.get("snap", 0.5),
            "decay_ms": snare.get("decay_ms", 150),
            "body": snare.get("body", 0.5),
        },
        "hihat": {
            "open_closed_ratio": hihat.get("open_closed_ratio", hihat.get("open_ratio", 0.3)),
            "decay_ms": hihat.get("decay_ms", 50),
            "brightness": hihat.get("brightness", 0.5),
        },
        "matched_machine": {
            "name": machine_match.get("display") if machine_match else None,
            "description": machine_match.get("description") if machine_match else None,
            "price_estimate": machine_match.get("price_estimate") if machine_match else None,
            "match_score": machine_match.get("match_score") if machine_match else None,
        } if machine_match else None,
        "suggested_params": machine_match.get("suggested_params") if machine_match else None,
        "notes": "Drum machine settings based on spectral analysis. Adjust to taste.",
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}_drums.json"

    return ExportedPreset(
        filename=filename,
        format="drums",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def export_ableton_preset(
    descriptor: dict,
    chain: list[dict] = None,
    preset_name: str = "Tone Forge Ableton",
    instrument_type: str = "guitar",
) -> ExportedPreset:
    """Export to Ableton Live compatible format.

    Creates a detailed preset with:
    - Recommended Ableton devices (Amp, Cabinet, effects)
    - Recommended VST plugins (for amp sims)
    - Parameter settings mapped to Ableton ranges
    - Effect chain order

    Args:
        descriptor: Analysis descriptor dict
        chain: Signal chain recommendations
        preset_name: Name for the preset
        instrument_type: 'guitar', 'bass', 'synth', or 'drums'
    """
    chain = chain or []

    # Map amp families to Ableton Amp device settings
    amp_family = descriptor.get("amp", {}).get("family", "clean")
    gain = descriptor.get("amp", {}).get("gain", 0.5)

    # Ableton Amp has these amp types: Clean, Boost, Blues, Rock, Lead, Heavy, Bass
    ableton_amp_map = {
        "fender_clean": {"type": "Clean", "gain": 0.3, "bass": 0.5, "mid": 0.5, "treble": 0.6},
        "tweed": {"type": "Blues", "gain": 0.5, "bass": 0.6, "mid": 0.5, "treble": 0.5},
        "vox_chime": {"type": "Boost", "gain": 0.4, "bass": 0.4, "mid": 0.6, "treble": 0.7},
        "marshall_plexi": {"type": "Rock", "gain": 0.6, "bass": 0.5, "mid": 0.6, "treble": 0.6},
        "marshall_jcm": {"type": "Lead", "gain": 0.7, "bass": 0.5, "mid": 0.7, "treble": 0.6},
        "mesa_rectifier": {"type": "Heavy", "gain": 0.8, "bass": 0.6, "mid": 0.5, "treble": 0.6},
        "5150_peavey": {"type": "Heavy", "gain": 0.85, "bass": 0.5, "mid": 0.6, "treble": 0.7},
        "bogner": {"type": "Lead", "gain": 0.75, "bass": 0.5, "mid": 0.6, "treble": 0.6},
        "soldano": {"type": "Lead", "gain": 0.8, "bass": 0.5, "mid": 0.7, "treble": 0.65},
        "dumble": {"type": "Boost", "gain": 0.5, "bass": 0.5, "mid": 0.6, "treble": 0.55},
    }

    amp_settings = ableton_amp_map.get(amp_family, {"type": "Clean", "gain": gain})
    amp_settings["gain"] = max(amp_settings.get("gain", 0.5), gain)  # Use detected gain if higher

    # Ableton Cabinet settings
    cab_character = descriptor.get("cab", {}).get("speaker_character", "neutral")
    ableton_cab_map = {
        "v30_like": {"type": "4x12", "microphone": "Dynamic", "position": "Near"},
        "greenback_like": {"type": "4x12", "microphone": "Dynamic", "position": "Off-Axis"},
        "alnico_like": {"type": "2x12", "microphone": "Condenser", "position": "Near"},
        "jensen_like": {"type": "1x12", "microphone": "Condenser", "position": "Near"},
        "neutral": {"type": "2x12", "microphone": "Dynamic", "position": "Near"},
    }
    cab_settings = ableton_cab_map.get(cab_character, ableton_cab_map["neutral"])

    # Build effect chain for Ableton
    ableton_chain = []

    # Add Amp and Cabinet
    ableton_chain.append({
        "device": "Amp",
        "type": "Ableton Built-in",
        "settings": amp_settings,
    })
    ableton_chain.append({
        "device": "Cabinet",
        "type": "Ableton Built-in",
        "settings": cab_settings,
    })

    # Map effects from chain to Ableton devices
    for pick in chain:
        slot = pick.get("slot", "")
        params = pick.get("params", {})

        if slot == "drive":
            ableton_chain.append({
                "device": "Overdrive" if params.get("gain", 5) < 7 else "Saturator",
                "type": "Ableton Built-in",
                "settings": {
                    "drive": params.get("gain", 5) / 10,
                    "tone": params.get("tone", 5) / 10,
                },
                "alternative_vst": pick.get("display", "Overdrive pedal"),
            })
        elif slot == "delay":
            ableton_chain.append({
                "device": "Delay",
                "type": "Ableton Built-in",
                "settings": {
                    "time_ms": params.get("time_ms", 350),
                    "feedback": params.get("feedback", 0.3),
                    "dry_wet": params.get("mix", 0.3),
                },
            })
        elif slot == "reverb":
            ableton_chain.append({
                "device": "Reverb",
                "type": "Ableton Built-in",
                "settings": {
                    "decay_time": params.get("decay", 2.0),
                    "dry_wet": params.get("mix", 0.25),
                    "room_size": params.get("size", 0.5),
                },
            })
        elif slot == "modulation":
            mod_type = pick.get("block_id", "chorus")
            if "chorus" in mod_type:
                ableton_chain.append({
                    "device": "Chorus-Ensemble",
                    "type": "Ableton Built-in",
                    "settings": {
                        "rate": params.get("rate", 0.5),
                        "depth": params.get("depth", 0.5),
                    },
                })
            elif "flanger" in mod_type:
                ableton_chain.append({
                    "device": "Flanger",
                    "type": "Ableton Built-in",
                    "settings": {
                        "rate": params.get("rate", 0.3),
                        "depth": params.get("depth", 0.5),
                    },
                })
            elif "phaser" in mod_type:
                ableton_chain.append({
                    "device": "Phaser",
                    "type": "Ableton Built-in",
                    "settings": {
                        "rate": params.get("rate", 0.4),
                        "feedback": params.get("feedback", 0.5),
                    },
                })

    # Recommended VST alternatives for better amp simulation
    vst_recommendations = []
    if instrument_type in ("guitar", "bass"):
        vst_recommendations = [
            {"name": "Neural DSP Archetype", "type": "Amp Sim", "note": "High-quality amp modeling"},
            {"name": "Amplitube 5", "type": "Amp Sim", "note": "Wide variety of amp models"},
            {"name": "BIAS FX 2", "type": "Amp Sim", "note": "Good tone matching capabilities"},
            {"name": "Guitar Rig 6", "type": "Amp Sim", "note": "Flexible routing and effects"},
        ]

    preset = {
        "name": preset_name,
        "version": "1.0",
        "format": "ableton",
        "created": datetime.now().isoformat(),
        "generator": "Tone Forge",
        "target_daw": "Ableton Live 11+",
        "instrument_type": instrument_type,
        "analysis_summary": {
            "amp_family": amp_family,
            "gain_level": gain,
            "cab_character": cab_character,
        },
        "ableton_device_chain": ableton_chain,
        "vst_recommendations": vst_recommendations,
        "setup_instructions": [
            "1. Create a new Audio Track in Ableton",
            "2. Add devices in order from the 'ableton_device_chain' list",
            "3. Apply the settings shown for each device",
            "4. For better results, consider using a VST amp sim from 'vst_recommendations'",
            "5. Fine-tune to taste - these are starting points based on analysis",
        ],
        "notes": f"Tone analysis suggests a {amp_family.replace('_', ' ')} style amp with {gain*100:.0f}% gain.",
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}_ableton.json"

    return ExportedPreset(
        filename=filename,
        format="ableton",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def export_ableton_synth(
    synth_descriptor: dict,
    preset_name: str = "Tone Forge Synth",
) -> ExportedPreset:
    """Export synth analysis to Ableton-specific format.

    Maps to Ableton's built-in synths: Wavetable, Operator, Analog.
    """
    osc = synth_descriptor.get("oscillator", {})
    filt = synth_descriptor.get("filter", {})
    env = synth_descriptor.get("amp_envelope", {})
    lfo = synth_descriptor.get("lfo", {})

    # Determine best Ableton synth for this sound
    osc_type = osc.get("type", "saw")
    num_voices = osc.get("num_voices", 1)

    if osc_type in ("saw", "square", "pulse") and num_voices > 1:
        recommended_synth = "Wavetable"
        synth_settings = {
            "oscillator_1": {
                "waveform": osc_type.title(),
                "voices": min(num_voices, 8),
                "detune": osc.get("detune", 0),
            },
            "filter": {
                "type": filt.get("type", "lowpass"),
                "frequency": filt.get("cutoff_normalized", 1.0),
                "resonance": filt.get("resonance", 0),
            },
            "amp_envelope": {
                "attack": env.get("attack_ms", 10) / 1000,
                "decay": env.get("decay_ms", 100) / 1000,
                "sustain": env.get("sustain", 0.8),
                "release": env.get("release_ms", 200) / 1000,
            },
        }
    elif osc_type == "sine" or (filt.get("cutoff_normalized", 1) < 0.3):
        recommended_synth = "Operator"
        synth_settings = {
            "algorithm": "1 (Simple FM)",
            "oscillator_a": {
                "waveform": "Sine",
                "level": 1.0,
            },
            "filter": {
                "frequency": filt.get("cutoff_normalized", 1.0),
                "resonance": filt.get("resonance", 0),
            },
        }
    else:
        recommended_synth = "Analog"
        synth_settings = {
            "oscillator_1": {
                "waveform": osc_type.title() if osc_type in ("saw", "square", "sine") else "Saw",
            },
            "filter": {
                "type": "Lowpass 24dB",
                "frequency": filt.get("cutoff_normalized", 1.0),
                "resonance": filt.get("resonance", 0),
            },
        }

    preset = {
        "name": preset_name,
        "version": "1.0",
        "format": "ableton_synth",
        "created": datetime.now().isoformat(),
        "generator": "Tone Forge",
        "target_daw": "Ableton Live 11+",
        "recommended_synth": recommended_synth,
        "synth_settings": synth_settings,
        "modulation": {
            "lfo_rate": lfo.get("rate_hz", 0) if lfo else 0,
            "lfo_target": lfo.get("target", "none") if lfo else "none",
        },
        "effects": {
            "chorus": synth_descriptor.get("has_chorus", False),
            "reverb": synth_descriptor.get("has_reverb", False),
            "delay": synth_descriptor.get("has_delay", False),
        },
        "setup_instructions": [
            f"1. Add {recommended_synth} to a MIDI track",
            "2. Apply the oscillator and filter settings",
            "3. Set the amp envelope (ADSR)",
            "4. Add effects as indicated",
        ],
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}_ableton_synth.json"

    return ExportedPreset(
        filename=filename,
        format="ableton_synth",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def export_ableton_drums(
    drums_descriptor: dict,
    machine_match: dict = None,
    preset_name: str = "Tone Forge Drums",
) -> ExportedPreset:
    """Export drum analysis to Ableton Drum Rack format.

    Provides settings for Ableton's Drum Rack with recommended samples
    and processing.
    """
    kick = drums_descriptor.get("kick", {})
    snare = drums_descriptor.get("snare", {})
    hihat = drums_descriptor.get("hihat", {})
    tempo = drums_descriptor.get("tempo_bpm", drums_descriptor.get("overall", {}).get("tempo_bpm", 120))

    # Map to Ableton Drum Rack cells
    drum_rack = {
        "C1_kick": {
            "sample_type": "808" if kick.get("decay_ms", 200) > 300 else "Acoustic",
            "processing": {
                "pitch": kick.get("pitch_hz", 60),
                "decay": kick.get("decay_ms", 200),
                "saturator": kick.get("saturation", 0.3),
                "eq_sub": kick.get("sub_presence", 0.5),
                "eq_click": kick.get("click", 0.3),
            },
        },
        "D1_snare": {
            "sample_type": "Electronic" if snare.get("noise", 0.5) > 0.6 else "Acoustic",
            "processing": {
                "pitch": snare.get("pitch_hz", 200),
                "decay": snare.get("decay_ms", 150),
                "noise_amount": snare.get("noise", 0.5),
                "transient_snap": snare.get("snap", 0.5),
            },
        },
        "F#1_hihat_closed": {
            "decay": hihat.get("decay_ms", 50),
            "brightness": hihat.get("brightness", 0.5),
        },
        "A#1_hihat_open": {
            "decay": hihat.get("decay_ms", 50) * 3,
            "brightness": hihat.get("brightness", 0.5),
        },
    }

    # Recommend sample packs based on style
    style = drums_descriptor.get("overall", {}).get("style", "electronic")
    sample_pack_recommendations = {
        "electronic": ["Ableton 808 Core", "Samples From Mars", "Goldbaby"],
        "acoustic": ["Ableton Drum Booth", "Superior Drummer", "Addictive Drums"],
        "hybrid": ["Ableton Punch & Tilt", "XO by XLN", "Battery 4"],
    }

    preset = {
        "name": preset_name,
        "version": "1.0",
        "format": "ableton_drums",
        "created": datetime.now().isoformat(),
        "generator": "Tone Forge",
        "target_daw": "Ableton Live 11+",
        "tempo_bpm": tempo,
        "drum_rack_cells": drum_rack,
        "recommended_sample_packs": sample_pack_recommendations.get(style, sample_pack_recommendations["electronic"]),
        "matched_machine": machine_match.get("display") if machine_match else None,
        "processing_chain": [
            {"device": "Drum Buss", "settings": {"drive": 0.3, "crunch": 0.2, "boom": kick.get("sub_presence", 0.5)}},
            {"device": "Glue Compressor", "settings": {"threshold": -10, "ratio": 4, "attack": 0.1, "release": 0.2}},
        ],
        "setup_instructions": [
            "1. Create a new MIDI track with Drum Rack",
            "2. Load samples matching the 'sample_type' for each cell",
            "3. Apply the processing settings to shape each sound",
            "4. Add Drum Buss and Glue Compressor after the Drum Rack",
            f"5. Set project tempo to {tempo:.0f} BPM",
        ],
    }

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}_ableton_drums.json"

    return ExportedPreset(
        filename=filename,
        format="ableton_drums",
        content=json.dumps(preset, indent=2),
        content_type="application/json",
    )


def export_ableton_live_set(
    full_result: dict,
    preset_name: str = "Tone Forge Session",
) -> ExportedPreset:
    """Export a complete Ableton Live Set (.als) with MIDI tracks from analysis.

    Uses template-based mutation for reliability - starts with a valid ALS structure
    and adds MIDI tracks with extracted notes. This is much more reliable than
    generating XML from scratch.

    The exported .als includes:
    - Correct tempo from analysis
    - Key/scale information
    - MIDI tracks for each separated stem (drums, bass, guitar, keys, synth, vocals)
    - Locators for detected chord changes

    Args:
        full_result: The complete analysis result containing midi_stems and metadata
        preset_name: Name for the Live Set
    """
    from . import als_template

    # Extract tempo. Phase 7 hoisted a canonical session tempo onto
    # `full_result["tempo_bpm"]` (see unified_pipeline.AnalysisResult);
    # the per-instrument descriptor fields are kept as legacy fallbacks
    # for old bundles. The previous `midi_stems[*].tempo_bpm` fallback
    # was orphaned by commit 74e278f (the per-stem field is now named
    # `extraction_tempo_bpm` and is deliberately not the session tempo).
    tempo_bpm = 120.0  # Default
    if full_result.get("tempo_bpm"):
        tempo_bpm = float(full_result["tempo_bpm"])
    elif full_result.get("guitar", {}).get("descriptor", {}).get("source", {}).get("tempo_bpm"):
        tempo_bpm = full_result["guitar"]["descriptor"]["source"]["tempo_bpm"]
    elif full_result.get("synth", {}).get("descriptor", {}).get("tempo_bpm"):
        tempo_bpm = full_result["synth"]["descriptor"]["tempo_bpm"]
    elif full_result.get("drums", {}).get("descriptor", {}).get("tempo_bpm"):
        tempo_bpm = full_result["drums"]["descriptor"]["tempo_bpm"]

    # Extract key info
    key_root = 0  # C
    key_scale = "Major"
    if full_result.get("synth", {}).get("descriptor", {}).get("detected_key"):
        key_str = full_result["synth"]["descriptor"]["detected_key"]
        # Parse key string like "C major" or "A minor"
        key_parts = key_str.split()
        if len(key_parts) >= 1:
            note_map = {'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3,
                       'E': 4, 'F': 5, 'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8,
                       'Ab': 8, 'A': 9, 'A#': 10, 'Bb': 10, 'B': 11}
            key_root = note_map.get(key_parts[0], 0)
        if len(key_parts) >= 2:
            key_scale = "Minor" if "minor" in key_parts[1].lower() else "Major"

    # Get MIDI stems data
    midi_stems = full_result.get("midi_stems", {})
    logger.info(f"ALS export - midi_stems keys: {list(midi_stems.keys())}")

    # Get chords if available
    chords = full_result.get("chords", [])

    # Check if we have enough data for a meaningful ALS. Fail loudly
    # rather than silently returning a JSON masquerading as a Live Set
    # — see the 2026-06-21 regression where the dict-shape `notes`
    # field from the new ensemble extractor tripped a template-side
    # TypeError and the silent fallback emitted a `.json` file with
    # MIME `application/json` that the client could not download as
    # `.als`. The caller in tone_forge_api.export_preset wraps any
    # exception raised here in an HTTPException(500) with the message
    # verbatim, so the operator sees the actual failure.
    if not midi_stems:
        raise ValueError(
            "Ableton Live Set export requires midi_stems in the analysis "
            "result. Re-analyze with MIDI extraction enabled."
        )

    # Create the ALS using template mutation. Any exception in als_template
    # propagates with the original traceback intact — no silent JSON fallback.
    als_b64, filename = als_template.create_als_from_analysis_base64(
        name=preset_name,
        tempo_bpm=tempo_bpm,
        key_root=key_root,
        key_scale=key_scale,
        midi_stems=midi_stems,
        chords=chords,
    )

    return ExportedPreset(
        filename=filename,
        format="ableton_live_set",
        content=als_b64,
        content_type="application/x-ableton-live-set",
    )


def export_project_bundle(
    full_result: dict,
    preset_name: str = "Tone Forge Project",
) -> ExportedPreset:
    """Export a complete project bundle (ZIP) with all assets.

    This is the RECOMMENDED export format because it:
    - Works with ANY DAW (Ableton, Logic, FL Studio, etc.)
    - Contains reliable standard formats (.mid, .wav)
    - Includes clear import instructions
    - Doesn't depend on fragile DAW-specific XML

    The bundle includes:
    - Per-stem MIDI files
    - Analysis data (JSON)
    - README with import instructions
    - Production notes and detected settings

    Args:
        full_result: The complete analysis result
        preset_name: Name for the project

    Returns:
        ExportedPreset with base64-encoded ZIP content
    """
    from . import project_bundle

    zip_bytes, filename = project_bundle.create_project_bundle(
        name=preset_name,
        analysis_result=full_result,
        include_stems=True,
        include_midi=True,
        include_presets=True,
    )

    # Base64 encode for JSON transport
    zip_b64 = base64.b64encode(zip_bytes).decode('ascii')

    return ExportedPreset(
        filename=filename,
        format="project_bundle",
        content=zip_b64,
        content_type="application/zip",
    )


def export_text_analysis(
    full_result: dict,
    preset_name: str = "Tone Forge Analysis",
) -> ExportedPreset:
    """Export analysis as a readable text file.

    Creates a plain text file with all detected settings formatted for easy reading.
    """
    lines = [
        f"{'='*60}",
        f"  TONE FORGE ANALYSIS: {preset_name}",
        f"{'='*60}",
        "",
    ]

    detection = full_result.get("detection", {})

    # Detection summary
    detected_types = []
    if detection.get("is_guitar"):
        detected_types.append("Guitar")
    if detection.get("is_bass"):
        detected_types.append("Bass")
    if detection.get("is_synth"):
        detected_types.append("Synth")
    if detection.get("is_drums"):
        detected_types.append("Drums")

    if detected_types:
        lines.append(f"Detected Instruments: {', '.join(detected_types)}")
        lines.append("")

    # Guitar analysis
    if full_result.get("guitar"):
        guitar = full_result["guitar"]
        desc = guitar.get("descriptor", {})
        amp = desc.get("amp", {})
        cab = desc.get("cab", {})

        lines.append(f"{'-'*60}")
        lines.append("  GUITAR")
        lines.append(f"{'-'*60}")
        lines.append(f"Amp Family:      {amp.get('family', 'unknown').replace('_', ' ').title()}")
        lines.append(f"Gain Level:      {amp.get('gain', 0):.0%}")
        if cab:
            lines.append(f"Cabinet:         {cab.get('speaker_character', 'neutral').replace('_', ' ').title()}")

        voicing = amp.get("voicing", {})
        if voicing:
            lines.append("")
            lines.append("EQ Settings:")
            lines.append(f"  Bass:     {voicing.get('bass', 0.5):.0%}")
            lines.append(f"  Mid:      {voicing.get('mid', 0.5):.0%}")
            lines.append(f"  Treble:   {voicing.get('treble', 0.5):.0%}")
            if voicing.get("presence"):
                lines.append(f"  Presence: {voicing.get('presence', 0.5):.0%}")

        # Helix chain
        if guitar.get("platforms", {}).get("helix"):
            helix_chain = guitar["platforms"]["helix"]
            lines.append("")
            lines.append("Recommended Helix Signal Chain:")
            for pick in helix_chain:
                slot = pick.get("slot", "")
                if slot == "amp_alt":
                    continue
                display = pick.get("display", "")
                rationale = pick.get("rationale", "")
                lines.append(f"  [{slot.upper():12}] {display}")
                if rationale:
                    lines.append(f"                 → {rationale}")

        # Pedals
        if guitar.get("platforms", {}).get("pedals"):
            pedal_chain = guitar["platforms"]["pedals"]
            lines.append("")
            lines.append("Recommended Pedals:")
            for pick in pedal_chain:
                display = pick.get("display", "")
                price = pick.get("price_estimate", "")
                lines.append(f"  • {display}" + (f" (~{price})" if price else ""))

        lines.append("")

    # Bass analysis
    if full_result.get("bass"):
        bass = full_result["bass"]
        desc = bass.get("descriptor", {})
        amp = desc.get("amp", {})

        lines.append(f"{'-'*60}")
        lines.append("  BASS")
        lines.append(f"{'-'*60}")
        lines.append(f"Amp Family:      {amp.get('family', 'unknown').replace('_', ' ').title()}")
        lines.append(f"Gain Level:      {amp.get('gain', 0):.0%}")
        lines.append(f"Technique:       {desc.get('technique', 'fingerstyle').title()}")

        if bass.get("recommendations"):
            lines.append("")
            lines.append("Recommended Gear:")
            for rec in bass["recommendations"]:
                display = rec.get("display", "")
                category = rec.get("category", "")
                price = rec.get("price_estimate", "")
                lines.append(f"  [{category.upper():12}] {display}" + (f" (~{price})" if price else ""))

        lines.append("")

    # Synth analysis
    if full_result.get("synth"):
        synth = full_result["synth"]
        desc = synth.get("descriptor", {})
        osc = desc.get("oscillator", {})
        filt = desc.get("filter", {})
        env = desc.get("amp_envelope", {})

        lines.append(f"{'-'*60}")
        lines.append("  SYNTH")
        lines.append(f"{'-'*60}")
        lines.append(f"Oscillator:      {osc.get('type', 'unknown').upper()}")
        if osc.get("num_voices", 1) > 1:
            lines.append(f"Voices:          {osc.get('num_voices')}")
        if osc.get("detune"):
            lines.append(f"Detune:          {osc.get('detune'):.1f} cents")

        if filt:
            lines.append("")
            lines.append("Filter:")
            lines.append(f"  Type:      {filt.get('type', 'lowpass').title()}")
            lines.append(f"  Cutoff:    {filt.get('cutoff_hz', 20000):.0f} Hz")
            lines.append(f"  Resonance: {filt.get('resonance', 0):.0%}")

        if env:
            lines.append("")
            lines.append("Amp Envelope (ADSR):")
            lines.append(f"  Attack:  {env.get('attack_ms', 0):.0f} ms")
            lines.append(f"  Decay:   {env.get('decay_ms', 0):.0f} ms")
            lines.append(f"  Sustain: {env.get('sustain', 0):.0%}")
            lines.append(f"  Release: {env.get('release_ms', 0):.0f} ms")

        # Hardware matches
        if synth.get("hardware_matches"):
            lines.append("")
            lines.append("Matched Hardware Synths:")
            for hw in synth["hardware_matches"][:3]:
                lines.append(f"  • {hw.get('name', '')} ({hw.get('match_score', 0):.0%} match)")

        lines.append("")

    # Drums analysis
    if full_result.get("drums"):
        drums = full_result["drums"]
        desc = drums.get("descriptor", {})
        kick = desc.get("kick", {})
        snare = desc.get("snare", {})

        lines.append(f"{'-'*60}")
        lines.append("  DRUMS")
        lines.append(f"{'-'*60}")
        tempo = desc.get("tempo_bpm", desc.get("overall", {}).get("tempo_bpm", 0))
        lines.append(f"Tempo:           {tempo:.0f} BPM")
        if desc.get("swing"):
            lines.append(f"Swing:           {desc.get('swing'):.0%}")

        if kick:
            lines.append("")
            lines.append("Kick:")
            lines.append(f"  Pitch:     {kick.get('pitch_hz', 60):.0f} Hz")
            lines.append(f"  Decay:     {kick.get('decay_ms', 200):.0f} ms")

        if snare:
            lines.append("")
            lines.append("Snare:")
            lines.append(f"  Pitch:     {snare.get('pitch_hz', 200):.0f} Hz")
            lines.append(f"  Noise:     {snare.get('noise', 0.5):.0%}")

        if drums.get("machine_match"):
            mm = drums["machine_match"]
            lines.append("")
            lines.append(f"Matched Drum Machine: {mm.get('display', 'unknown')}")
            if mm.get("match_score"):
                lines.append(f"Match Score: {mm.get('match_score'):.0%}")

        lines.append("")

    lines.append(f"{'='*60}")
    lines.append("Generated by Tone Forge")
    lines.append(f"{'='*60}")

    text_content = "\n".join(lines)

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}_analysis.txt"

    return ExportedPreset(
        filename=filename,
        format="text",
        content=text_content,
        content_type="text/plain",
    )


def export_ableton_wavetable(
    synth_descriptor: dict,
    preset_name: str = "Tone Forge Synth",
) -> ExportedPreset:
    """Export synth analysis to Ableton Wavetable .adv format.

    Creates a native Ableton Wavetable preset file that can be loaded directly
    into Ableton Live. Maps Tone Forge synth analysis to Wavetable parameters.

    Uses a complete Wavetable preset as a template and modifies specific
    parameter values via regex substitution to ensure compatibility.

    Args:
        synth_descriptor: Synth analysis descriptor with oscillator, filter, envelope data
        preset_name: Name for the preset
    """
    import re
    from pathlib import Path

    osc = synth_descriptor.get("oscillator", {})
    filt = synth_descriptor.get("filter", {})
    env = synth_descriptor.get("amp_envelope", {})
    lfo = synth_descriptor.get("lfo", {})

    # Load the complete Wavetable template
    template_path = Path(__file__).parent.parent / "data" / "wavetable_template.xml"
    if not template_path.exists():
        raise FileNotFoundError(f"Wavetable template not found at {template_path}")

    with open(template_path, 'r', encoding='utf-8') as f:
        xml = f.read()

    # Convert our analysis to Wavetable parameters
    # Filter frequency: 20-20480 Hz
    filter_freq = filt.get("cutoff_hz", 8000)
    filter_freq = max(20, min(20480, filter_freq))

    # Filter resonance: 0-1.25 (we use 0-1, so scale up slightly for more extreme values)
    filter_res = filt.get("resonance", 0)
    filter_res = max(0, min(1.25, filter_res))

    # Envelope times: convert ms to seconds, clamp to 0-20 range
    attack_s = min(20, max(0, env.get("attack_ms", 10) / 1000))
    decay_s = min(20, max(0.0015, env.get("decay_ms", 100) / 1000))
    release_s = min(20, max(0.0015, env.get("release_ms", 200) / 1000))
    sustain = max(0, min(1, env.get("sustain", 0.8)))

    # Wave position: 0-1 (use detune to influence timbre variation)
    detune = osc.get("detune", 0)
    wave_pos = min(1, max(0, 0.3 + (abs(detune) / 100)))  # Base position with detune influence

    # Oscillator 2 settings (slightly detuned for richness)
    osc2_detune = osc.get("detune", 0) / 100  # Convert cents to semitones fraction
    osc2_wave_pos = min(1, max(0, wave_pos + 0.1))

    # LFO rate: convert Hz to Wavetable's internal format
    lfo_rate = lfo.get("rate_hz", 0) if lfo else 0

    # Unison settings
    unison_on = "true" if osc.get("num_voices", 1) > 1 else "false"
    unison_voices = min(8, max(2, osc.get("num_voices", 2)))
    unison_amount = min(1, abs(osc.get("detune", 0)) / 50)

    # Sub oscillator
    sub_osc_on = "true" if osc.get("sub_osc", False) else "false"

    # Helper function to replace a parameter value in XML
    def replace_param(xml_str: str, param_name: str, new_value) -> str:
        """Replace a parameter's Manual Value in the XML."""
        # Match pattern: <ParamName>\n...\n<Manual Value="..." />
        pattern = rf'(<{param_name}>[\s\S]*?<Manual Value=")[^"]*(")'
        return re.sub(pattern, rf'\g<1>{new_value}\g<2>', xml_str, count=1)

    # Modify preset name and annotation
    xml = re.sub(r'(<UserName Value=")[^"]*(")', rf'\g<1>{preset_name}\g<2>', xml, count=1)
    xml = re.sub(r'(<Annotation Value=")[^"]*(")', r'\g<1>Generated by Tone Forge\g<2>', xml, count=1)

    # Modify filter parameters
    xml = replace_param(xml, "Voice_Filter1_Frequency", filter_freq)
    xml = replace_param(xml, "Voice_Filter1_Resonance", filter_res)

    # Modify amp envelope
    xml = replace_param(xml, "Voice_Modulators_AmpEnvelope_Times_Attack", attack_s)
    xml = replace_param(xml, "Voice_Modulators_AmpEnvelope_Times_Decay", decay_s)
    xml = replace_param(xml, "Voice_Modulators_AmpEnvelope_Times_Release", release_s)
    xml = replace_param(xml, "Voice_Modulators_AmpEnvelope_Sustain", sustain)

    # Modify oscillator wave positions
    xml = replace_param(xml, "Voice_Oscillator1_Wavetables_WavePosition", wave_pos)
    xml = replace_param(xml, "Voice_Oscillator2_Wavetables_WavePosition", osc2_wave_pos)

    # Modify oscillator 2 detune
    xml = replace_param(xml, "Voice_Oscillator2_Pitch_Detune", osc2_detune)

    # Modify unison settings (these use different XML structure)
    # Voice_Unison_Mode: 0=Off, 1=Classic, 2=Shimmer, 3=Noise, 4=Phase Sync
    unison_mode = 2 if osc.get("num_voices", 1) > 1 else 0
    xml = re.sub(r'(<Voice_Unison_Mode Value=")[^"]*(")', rf'\g<1>{unison_mode}\g<2>', xml, count=1)
    xml = re.sub(r'(<Voice_Unison_VoiceCount Value=")[^"]*(")', rf'\g<1>{unison_voices}\g<2>', xml, count=1)
    xml = replace_param(xml, "Voice_Unison_Amount", unison_amount)

    # Modify sub oscillator
    xml = replace_param(xml, "Voice_SubOscillator_On", sub_osc_on)

    wavetable_xml = xml

    # Template-based approach replaces the old inline XML
    # Compress with gzip
    adv_bytes = gzip.compress(wavetable_xml.encode('utf-8'))

    # Base64 encode for JSON transport
    adv_b64 = base64.b64encode(adv_bytes).decode('ascii')

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}.adv"

    return ExportedPreset(
        filename=filename,
        format="ableton_wavetable",
        content=adv_b64,
        content_type="application/x-ableton-wavetable",
    )


def export_ableton_analog(
    synth_descriptor: dict,
    preset_name: str = "Tone Forge Synth",
) -> ExportedPreset:
    """Export synth analysis to Ableton Analog .adv format.

    Creates a native Ableton Analog preset file that can be loaded directly
    into Ableton Live Standard or Suite.

    Args:
        synth_descriptor: Synth analysis descriptor with oscillator, filter, envelope data
        preset_name: Name for the preset
    """
    import re
    from pathlib import Path

    filt = synth_descriptor.get("filter", {})
    env = synth_descriptor.get("amp_envelope", {})

    # Load the Analog template
    template_path = Path(__file__).parent.parent / "data" / "analog_template.xml"
    if not template_path.exists():
        raise FileNotFoundError(f"Analog template not found at {template_path}")

    with open(template_path, 'r', encoding='utf-8') as f:
        xml = f.read()

    # Helper to replace a parameter's Manual Value
    def replace_param(xml_str: str, param_name: str, new_value, occurrence: int = 1) -> str:
        """Replace a parameter's Manual Value in the XML."""
        pattern = rf'(<{param_name}>[\s\S]*?<Manual Value=")[^"]*(")'
        count = 0
        def replacer(m):
            nonlocal count
            count += 1
            if count == occurrence:
                return f'{m.group(1)}{new_value}{m.group(2)}'
            return m.group(0)
        return re.sub(pattern, replacer, xml_str)

    # Convert filter cutoff to 0-1 range (Analog uses normalized values)
    # Analog's filter range is roughly 20Hz to 18kHz, logarithmic
    # Cap at 6kHz max for a musical sound (high cutoff = harsh)
    cutoff_hz = filt.get("cutoff_hz", 3000)
    cutoff_hz = min(cutoff_hz, 6000)  # Cap for warmer sound
    import math
    cutoff_norm = (math.log10(max(20, min(18000, cutoff_hz))) - math.log10(20)) / (math.log10(18000) - math.log10(20))
    cutoff_norm = max(0.3, min(0.75, cutoff_norm))  # Keep in musical range (0.3-0.75)

    # Filter resonance (already 0-1 in our descriptor)
    # Add some default resonance for character if none detected
    resonance = filt.get("resonance", 0.3)
    if resonance < 0.15:
        resonance = 0.25  # Minimum resonance for some character
    resonance = max(0.1, min(0.7, resonance))  # Keep in musical range

    # Envelope times - Analog uses 0-1 normalized values
    # Attack: 0=instant, 1=max (~20s)
    # We'll map our ms values logarithmically
    def ms_to_analog_time(ms, max_ms=20000):
        if ms <= 0:
            return 0
        return min(1, (math.log10(max(1, ms)) / math.log10(max_ms)))

    # Use reasonable defaults for a musical synth sound
    # Clamp attack to reasonable range (1-100ms typical for synth leads/pads)
    attack_ms = env.get("attack_ms", 20)
    attack_ms = max(5, min(100, attack_ms))  # Keep snappy
    attack = ms_to_analog_time(attack_ms)

    decay_ms = env.get("decay_ms", 200)
    decay_ms = max(50, min(500, decay_ms))
    decay = ms_to_analog_time(decay_ms)

    release_ms = env.get("release_ms", 300)
    release_ms = max(100, min(800, release_ms))
    release = ms_to_analog_time(release_ms)

    sustain = env.get("sustain", 0.7)
    sustain = max(0.5, min(0.9, sustain))  # Keep sustain reasonably high

    # Modify preset name
    xml = re.sub(r'(<UserName Value=")[^"]*(")', rf'\g<1>{preset_name}\g<2>', xml, count=1)
    xml = re.sub(r'(<Annotation Value=")[^"]*(")', r'\g<1>Generated by Tone Forge\g<2>', xml, count=1)

    # Set filter type to LP24 (lowpass 24dB) - value 0
    # Filter types: 0=LP24, 1=LP12, 2=BP12, 3=BP24, 4=N2P, 5=HP12, 6=HP24
    xml = replace_param(xml, "FilterType", "0", occurrence=1)  # First oscillator
    xml = replace_param(xml, "FilterType", "0", occurrence=2)  # Second oscillator

    # Disable noise (correct param name is NoiseLevel)
    xml = replace_param(xml, "NoiseLevel", "0.0", occurrence=1)

    # Set oscillator levels (correct param name is OscillatorLevel)
    xml = replace_param(xml, "OscillatorLevel", "0.75", occurrence=1)  # Osc1
    xml = replace_param(xml, "OscillatorLevel", "0.5", occurrence=2)   # Osc2

    # Set amplifier levels
    xml = replace_param(xml, "AmplifierLevel", "0.7", occurrence=1)  # Amp1
    xml = replace_param(xml, "AmplifierLevel", "0.5", occurrence=2)  # Amp2

    # Set oscillator octave to 0 (normal pitch, not high)
    xml = replace_param(xml, "OscillatorOct", "0", occurrence=1)
    xml = replace_param(xml, "OscillatorOct", "0", occurrence=2)

    # Set waveform - 0=Sine, 1=Saw, 2=Rect, 3=Noise (Analog uses different mapping)
    # For a typical synth sound, use Saw (1)
    xml = replace_param(xml, "OscillatorWaveShape", "1", occurrence=1)  # Saw wave
    xml = replace_param(xml, "OscillatorWaveShape", "1", occurrence=2)

    # Modify filter cutoff
    xml = replace_param(xml, "FilterCutoffFrequency", f"{cutoff_norm:.6f}", occurrence=1)
    xml = replace_param(xml, "FilterCutoffFrequency", f"{cutoff_norm:.6f}", occurrence=2)
    xml = replace_param(xml, "FilterQFactor", f"{resonance:.6f}", occurrence=1)
    xml = replace_param(xml, "FilterQFactor", f"{resonance:.6f}", occurrence=2)

    # Modify amp envelope
    xml = replace_param(xml, "AttackTime", f"{attack:.6f}", occurrence=1)
    xml = replace_param(xml, "AttackTime", f"{attack:.6f}", occurrence=2)
    xml = replace_param(xml, "DecayTime", f"{decay:.6f}", occurrence=1)
    xml = replace_param(xml, "DecayTime", f"{decay:.6f}", occurrence=2)
    xml = replace_param(xml, "SustainLevel", f"{sustain:.6f}", occurrence=1)
    xml = replace_param(xml, "SustainLevel", f"{sustain:.6f}", occurrence=2)
    xml = replace_param(xml, "ReleaseTime", f"{release:.6f}", occurrence=1)
    xml = replace_param(xml, "ReleaseTime", f"{release:.6f}", occurrence=2)

    # Compress with gzip
    adv_bytes = gzip.compress(xml.encode('utf-8'))

    # Base64 encode for JSON transport
    adv_b64 = base64.b64encode(adv_bytes).decode('ascii')

    safe_name = "".join(c for c in preset_name if c.isalnum() or c in " -_").strip()
    filename = f"{safe_name}.adv"

    return ExportedPreset(
        filename=filename,
        format="ableton_analog",
        content=adv_b64,
        content_type="application/x-ableton-analog",
    )


# ---------------------------------------------------------------------------
# End-to-end reconstruction export (Phase 1: hardcoded preset)
# ---------------------------------------------------------------------------
#
# Audio → MIDI → (fixed Analog preset) → downloadable .als
#
# Phase 1 wires the full export path with a single pinned Analog preset so we
# can validate the workflow before depending on retrieval. Phase 2 replaces
# `_phase1_default_preset()` with a V2 retrieval call; nothing else in this
# module changes.
#
# The default preset is `Thick Chord Pad` from the Live 12 Standard core
# library. It is one of the 99 Analog presets in the V2 catalog and is known
# to render cleanly via the existing splice path
# (see preset_als_generator.create_preset_als).

_PHASE1_DEFAULT_ADV_PATH = (
    "/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/"
    "Core Library/Devices/Instruments/Analog/Synth Pad/Thick Chord Pad.adv"
)


def _phase1_default_preset():
    """Return the pinned Phase 1 PresetInfo (Thick Chord Pad)."""
    from pathlib import Path
    from .preset_catalog.preset_discovery import PresetInfo

    return PresetInfo(
        preset_id="analog_thick_chord_pad",
        name="Thick Chord Pad",
        instrument="Analog",
        category="Synth Pad",
        sound_type="pad",
        path=Path(_PHASE1_DEFAULT_ADV_PATH),
        source="core",
    )


def _fallback_notes(tempo: float):
    """Return a 4-note C-major sanity sequence if MIDI extraction is absent.

    Used so the reconstruction export path exits cleanly during early testing
    even when no analysis MIDI is available.
    """
    from .preset_catalog.test_sequence import TestNote

    # Four quarter notes: C4, E4, G4, C5
    return [
        TestNote(pitch=60, start_beats=0.0, duration_beats=1.0, velocity=100),
        TestNote(pitch=64, start_beats=1.0, duration_beats=1.0, velocity=100),
        TestNote(pitch=67, start_beats=2.0, duration_beats=1.0, velocity=100),
        TestNote(pitch=72, start_beats=3.0, duration_beats=1.0, velocity=100),
    ]


def _notes_from_full_result(full_result: dict, tempo: float):
    """Convert the analysis `midi_data` blob into a list of TestNote.

    The `midi_data.content` field is a base64-encoded standard MIDI file
    written by `tone_forge.midi_extractor`. We parse it with `pretty_midi`,
    flatten all instrument tracks, and convert each note's seconds-based
    timing into beats using the supplied tempo.

    Returns the fallback C-major sequence if MIDI is missing or unparseable.
    """
    import io
    from .preset_catalog.test_sequence import TestNote

    midi_data = full_result.get("midi_data")
    if not midi_data:
        # Fall back to midi_stems (multi-stem analyses store notes there)
        midi_stems = full_result.get("midi_stems") or {}
        # Prefer melodic stems in a stable order
        for key in ("bass", "vocals", "other", "drums"):
            if key in midi_stems and midi_stems[key].get("content"):
                midi_data = midi_stems[key]
                break

    if not midi_data or not midi_data.get("content"):
        logger.info("reconstruction: no midi_data, using fallback sequence")
        return _fallback_notes(tempo)

    try:
        import pretty_midi  # already a transitive dep via midi_extractor
        raw = base64.b64decode(midi_data["content"])
        pm = pretty_midi.PrettyMIDI(io.BytesIO(raw))
    except Exception as e:
        logger.warning(f"reconstruction: MIDI parse failed ({e}); using fallback")
        return _fallback_notes(tempo)

    notes: list = []
    beats_per_sec = tempo / 60.0
    for instrument in pm.instruments:
        if instrument.is_drum:
            continue
        for n in instrument.notes:
            duration = max(0.0, n.end - n.start)
            if duration <= 0:
                continue
            notes.append(TestNote(
                pitch=int(n.pitch),
                start_beats=float(n.start * beats_per_sec),
                duration_beats=float(duration * beats_per_sec),
                velocity=int(max(1, min(127, n.velocity))),
            ))

    if not notes:
        logger.info("reconstruction: MIDI parsed but contained no melodic notes; using fallback")
        return _fallback_notes(tempo)

    # Stable order by start time so the ALS clip view is predictable.
    notes.sort(key=lambda n: (n.start_beats, n.pitch))
    return notes


def _resolve_tempo(full_result: dict) -> float:
    """Pull a tempo from the analysis result, defaulting to 120 BPM."""
    for key in ("tempo_bpm", "tempo"):
        v = full_result.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    midi_data = full_result.get("midi_data") or {}
    v = midi_data.get("tempo_bpm")
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    return 120.0


def _resolve_key(full_result: dict) -> tuple[int, str]:
    """Pull (key_root, key_scale) from the analysis result.

    Mirrors the parsing done by `export_ableton_als` so reconstruction
    sets pick up the same C-major default when nothing is detected.
    """
    key_root = 0
    key_scale = "Major"
    synth = full_result.get("synth") or {}
    descriptor = synth.get("descriptor") or {}
    detected = (
        descriptor.get("detected_key")
        or full_result.get("detected_key")
    )
    if detected:
        parts = str(detected).split()
        if parts:
            note_map = {
                'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3,
                'E': 4, 'F': 5, 'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8,
                'Ab': 8, 'A': 9, 'A#': 10, 'Bb': 10, 'B': 11,
            }
            key_root = note_map.get(parts[0], 0)
        if len(parts) >= 2:
            key_scale = "Minor" if "minor" in parts[1].lower() else "Major"
    return key_root, key_scale


_RECONSTRUCTION_TOTAL_BUDGET_SEC = 120.0

# Hardcoded Drum Rack used for the drums stem (V2 catalog has no kits).
_RECONSTRUCTION_DRUM_RACK_PATH = (
    "/Users/mattharvey/Music/Ableton/Factory Packs/Singularities/Drums/709 Kit.adg"
)

# Stem → V2 catalog sound_type filter (mirrors
# `unified_pipeline.UnifiedPipeline._STEM_SOUND_TYPE_FILTER`).
_EXPORT_STEM_SOUND_TYPE_FILTER: dict[str, Optional[str]] = {
    "bass": "bass",
    "vocals": "lead",
    "guitar": None,
    "other": None,
    "piano": "keys",
}


def _resolve_stem_local_path(stem_url_or_path) -> Optional[str]:
    """Resolve a stem entry to a local filesystem path if possible.

    `stems_paths` may contain either a raw filesystem path (cloud/CPU
    pipeline) or a local-engine serve URL of the form
    `http://127.0.0.1:7777/api/serve-file?path=<fs path>` (GPU pipeline).
    Returns the local path or `None` if it can't be resolved.
    """
    from pathlib import Path
    from urllib.parse import urlparse, parse_qs

    if not stem_url_or_path:
        return None
    s = str(stem_url_or_path)
    if s.startswith("http://") or s.startswith("https://"):
        try:
            qs = parse_qs(urlparse(s).query)
            path = qs.get("path", [None])[0]
        except Exception:
            return None
        if path and Path(path).exists():
            return path
        return None
    if Path(s).exists():
        return s
    return None


def _retrieve_preset_matches_at_export(
    stems_paths: dict,
) -> dict:
    """Run V2 preset retrieval per stem at export time.

    Used as a fallback when the analysis result did not include
    `preset_matches` (e.g. when results come from the local GPU engine
    pipeline which bypasses `UnifiedPipeline._build_result`).

    Returns a dict of stem-name → match metadata, matching the shape used
    by `unified_pipeline.UnifiedPipeline._match_presets_per_stem`. Stems
    that fail or have no local file are simply omitted.
    """
    import time
    from pathlib import Path
    from .preset_catalog import preset_retrieval

    matches: dict = {}
    for stem_name, stem_entry in (stems_paths or {}).items():
        if stem_name == "drums":
            continue
        local_path = _resolve_stem_local_path(stem_entry)
        if not local_path:
            logger.info(
                "[reconstruction] preset retrieval: %s has no local file (%r); "
                "skipping", stem_name, stem_entry,
            )
            continue
        sound_type = _EXPORT_STEM_SOUND_TYPE_FILTER.get(stem_name)
        t0 = time.perf_counter()
        try:
            results = preset_retrieval.match_audio_file(
                Path(local_path),
                k=1,
                instrument="Analog",
                sound_type_filter=sound_type,
            )
        except Exception as e:
            logger.warning(
                "[reconstruction] preset retrieval failed for %s: %s",
                stem_name, e,
            )
            continue
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if not results:
            logger.info(
                "[reconstruction] preset retrieval: %s no match (%.0f ms)",
                stem_name, elapsed_ms,
            )
            continue
        top = results[0]
        matches[stem_name] = {
            "preset_id": top["preset_id"],
            "preset_name": top["preset_name"],
            "preset_path": top["preset_path"],
            "instrument": top["instrument"],
            "category": top["category"],
            "sound_type": top["sound_type"],
            "distance": top["distance"],
        }
        logger.info(
            "[reconstruction] preset retrieval: %s -> %s (distance=%.3f, %.0f ms)",
            stem_name, top["preset_name"], top["distance"], elapsed_ms,
        )
    return matches


def export_reconstruction_als(
    full_result: dict,
    preset_name: str = "Tone Forge Reconstruction",
) -> ExportedPreset:
    """Phase 2 multi-stem reconstruction export.

    Builds an Ableton Live `.als` with one MIDI track per stem:
      - drums → hardcoded Drum Rack (`_RECONSTRUCTION_DRUM_RACK_PATH`)
      - bass / vocals / guitar / piano / other → V2-retrieved preset from
        `full_result["preset_matches"]`, with Thick Chord Pad as fallback.

    Returns an `ExportedPreset` whose `content` is base64-encoded gzipped
    ALS bytes ready to download.

    Per-stage timings (resolve / build_als) are logged so we can confirm
    the export stays well under the 2-minute e2e budget.
    """
    import time
    from pathlib import Path
    from . import als_template

    t0 = time.perf_counter()

    default_preset_path = Path(_PHASE1_DEFAULT_ADV_PATH)
    if not default_preset_path.exists():
        raise FileNotFoundError(
            "Default reconstruction preset .adv not found at "
            f"{default_preset_path}. Install Ableton Live 12 Standard "
            "with the Core Library at the standard path, or update "
            "_PHASE1_DEFAULT_ADV_PATH in preset_export.py."
        )

    tempo = _resolve_tempo(full_result)
    key_root, key_scale = _resolve_key(full_result)
    midi_stems = full_result.get("midi_stems") or {}
    preset_matches = full_result.get("preset_matches") or {}
    chords = full_result.get("chords") or []

    if not midi_stems:
        raise ValueError(
            "Reconstruction export requires `midi_stems` in the analysis "
            "result; none were present."
        )

    # Fallback: when `preset_matches` is absent (local GPU engine path
    # bypasses `UnifiedPipeline._build_result`), run V2 retrieval here so
    # the export still gets per-stem matched devices instead of every
    # track defaulting to Thick Chord Pad.
    if not preset_matches:
        stems_paths = full_result.get("stems_paths") or {}
        if stems_paths:
            preset_matches = _retrieve_preset_matches_at_export(stems_paths)

    drum_rack_path = Path(_RECONSTRUCTION_DRUM_RACK_PATH)
    if not drum_rack_path.exists():
        logger.info(
            "[reconstruction] drum rack not found at %s; drums track will "
            "ship without a device chain.", drum_rack_path,
        )
        drum_rack_path = None

    t_resolve = time.perf_counter()

    als_bytes, filename = als_template.create_reconstruction_als(
        name=preset_name,
        tempo_bpm=tempo,
        key_root=key_root,
        key_scale=key_scale,
        midi_stems=midi_stems,
        preset_matches=preset_matches,
        chords=chords,
        drum_rack_path=drum_rack_path,
        default_preset_path=default_preset_path,
        default_preset_name="Thick Chord Pad",
        default_instrument="Analog",
    )
    als_b64 = base64.b64encode(als_bytes).decode("ascii")
    t_als = time.perf_counter()

    # Optionally absorb upstream analysis timings so the log line can defend
    # the 2-minute end-to-end claim in one place.
    analysis_elapsed = float(full_result.get("analysis_elapsed_sec") or 0.0)
    total_export_sec = t_als - t0
    total_e2e_sec = analysis_elapsed + total_export_sec

    matched_stems = sorted(
        k for k, v in preset_matches.items() if v and v.get("preset_path")
    )
    logger.info(
        "[reconstruction] resolve=%.3fs build_als=%.3fs export_total=%.3fs "
        "analysis=%.3fs e2e=%.3fs stems=%d matched=%s",
        t_resolve - t0,
        t_als - t_resolve,
        total_export_sec,
        analysis_elapsed,
        total_e2e_sec,
        len(midi_stems),
        matched_stems or "none",
    )
    if total_e2e_sec > _RECONSTRUCTION_TOTAL_BUDGET_SEC:
        logger.warning(
            "[reconstruction] e2e budget exceeded: %.1fs > %.0fs target",
            total_e2e_sec, _RECONSTRUCTION_TOTAL_BUDGET_SEC,
        )

    return ExportedPreset(
        filename=filename,
        format="reconstruction",
        content=als_b64,
        content_type="application/octet-stream",
    )


# Supported export formats
EXPORT_FORMATS = {
    "text": "Text Analysis (.txt)",
    "hlx": "Line 6 Helix (.hlx)",
    "hlx_stomp": "HX Stomp (.hlx)",
    "json": "Generic JSON",
    "neural_dsp": "Neural DSP Quad Cortex",
    "synth_serum": "Synth (Serum-style)",
    "synth_vital": "Synth (Vital-style)",
    "bass": "Bass Rig Preset",
    "drums": "Drum Machine Preset",
    "ableton": "Ableton Live (Guitar/Bass)",
    "ableton_synth": "Ableton Live (Synth)",
    "ableton_drums": "Ableton Live (Drums)",
    "ableton_live_set": "Ableton Live Set (Full Project)",
    "ableton_wavetable": "Ableton Wavetable (.adv) [Suite only]",
    "ableton_analog": "Ableton Analog (.adv)",
    "reconstruction": "Reconstruction (.als) — MIDI + matched preset",
}
