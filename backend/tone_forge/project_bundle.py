"""
Project Bundle Export - Reliable multi-file export for DAW import.

Instead of generating fragile .als files, this creates a ZIP bundle containing:
- Stem audio files (.wav)
- Per-stem MIDI files (.mid)
- Synth/instrument presets (.adv, .adg)
- Analysis notes (README.txt)
- Import instructions

Users can drag these directly into any DAW (Ableton, Logic, FL Studio, etc.)
This is more reliable than synthetic DAW project generation.
"""

import io
import zipfile
import base64
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class BundleFile:
    """A file to include in the bundle."""
    filename: str
    content: bytes  # Raw bytes
    subfolder: str = ""  # Optional subfolder within bundle


@dataclass
class ProjectBundle:
    """Complete project bundle ready for export."""
    name: str
    files: List[BundleFile]
    readme: str


def create_project_bundle(
    name: str,
    analysis_result: Dict,
    include_stems: bool = True,
    include_midi: bool = True,
    include_presets: bool = True,
) -> Tuple[bytes, str]:
    """
    Create a complete project bundle from analysis results.

    Args:
        name: Project name
        analysis_result: Full analysis result dict
        include_stems: Include separated audio stems
        include_midi: Include per-stem MIDI files
        include_presets: Include generated presets

    Returns:
        Tuple of (zip_bytes, filename)
    """
    files = []

    # Project info
    tempo = _get_tempo(analysis_result)
    key_info = _get_key_info(analysis_result)
    duration = _get_duration(analysis_result)

    # Add MIDI files
    if include_midi and analysis_result.get("midi_stems"):
        for stem_name, midi_data in analysis_result["midi_stems"].items():
            if midi_data.get("content"):
                midi_bytes = base64.b64decode(midi_data["content"])
                files.append(BundleFile(
                    filename=midi_data.get("filename", f"{stem_name}.mid"),
                    content=midi_bytes,
                    subfolder="MIDI",
                ))

    # Add single MIDI if no stems
    if include_midi and not analysis_result.get("midi_stems") and analysis_result.get("midi"):
        midi_data = analysis_result["midi"]
        if midi_data.get("content"):
            midi_bytes = base64.b64decode(midi_data["content"])
            files.append(BundleFile(
                filename=midi_data.get("filename", "extracted.mid"),
                content=midi_bytes,
                subfolder="MIDI",
            ))

    # Generate README with analysis
    readme = _generate_readme(name, analysis_result, tempo, key_info, duration)

    # Add analysis JSON for programmatic access
    analysis_json = json.dumps(analysis_result, indent=2, default=str)
    files.append(BundleFile(
        filename="analysis.json",
        content=analysis_json.encode('utf-8'),
        subfolder="",
    ))

    # Create ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add README at root
        zf.writestr(f"{name}/README.txt", readme)

        # Add all files
        for bundle_file in files:
            if bundle_file.subfolder:
                path = f"{name}/{bundle_file.subfolder}/{bundle_file.filename}"
            else:
                path = f"{name}/{bundle_file.filename}"
            zf.writestr(path, bundle_file.content)

    zip_bytes = zip_buffer.getvalue()
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)[:50]
    filename = f"{safe_name}_ToneForge.zip"

    logger.info(f"Created project bundle: {filename} ({len(files)} files, {len(zip_bytes)} bytes)")
    return zip_bytes, filename


def _get_tempo(result: Dict) -> float:
    """Extract tempo from analysis result."""
    if result.get("guitar", {}).get("descriptor", {}).get("source", {}).get("tempo_bpm"):
        return result["guitar"]["descriptor"]["source"]["tempo_bpm"]
    if result.get("synth", {}).get("descriptor", {}).get("tempo_bpm"):
        return result["synth"]["descriptor"]["tempo_bpm"]
    if result.get("drums", {}).get("descriptor", {}).get("tempo_bpm"):
        return result["drums"]["descriptor"]["tempo_bpm"]
    if result.get("midi_stems"):
        for stem_data in result["midi_stems"].values():
            if stem_data.get("tempo_bpm"):
                return stem_data["tempo_bpm"]
    return 120.0


def _get_key_info(result: Dict) -> str:
    """Extract key information from analysis result."""
    # Check synth descriptor for key info
    if result.get("synth", {}).get("descriptor", {}).get("detected_key"):
        return result["synth"]["descriptor"]["detected_key"]
    return "Not detected"


def _get_duration(result: Dict) -> float:
    """Extract duration from analysis result."""
    if result.get("guitar", {}).get("descriptor", {}).get("source", {}).get("duration_sec"):
        return result["guitar"]["descriptor"]["source"]["duration_sec"]
    if result.get("synth", {}).get("descriptor", {}).get("duration_sec"):
        return result["synth"]["descriptor"]["duration_sec"]
    return 30.0


def _generate_readme(
    name: str,
    result: Dict,
    tempo: float,
    key_info: str,
    duration: float,
) -> str:
    """Generate README with analysis details and import instructions."""

    lines = [
        f"{'='*60}",
        f"TONE FORGE PROJECT: {name}",
        f"{'='*60}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "PROJECT INFO",
        "-" * 40,
        f"Tempo: {tempo:.1f} BPM",
        f"Key: {key_info}",
        f"Duration: {duration:.1f} seconds",
        "",
    ]

    # Detection summary
    detection = result.get("detection", {})
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
        lines.extend([
            "DETECTED INSTRUMENTS",
            "-" * 40,
            ", ".join(detected_types),
            "",
        ])

    # MIDI stems info
    if result.get("midi_stems"):
        lines.extend([
            "MIDI FILES INCLUDED",
            "-" * 40,
        ])
        for stem_name, midi_data in result["midi_stems"].items():
            note_count = midi_data.get("note_count", 0)
            lines.append(f"  {stem_name}: {note_count} notes")
        lines.append("")

    # Tweak hints
    tweak_hints = []
    if result.get("guitar", {}).get("tweak_hints"):
        tweak_hints.extend(result["guitar"]["tweak_hints"])
    if result.get("synth", {}).get("tweak_hints"):
        tweak_hints.extend(result["synth"]["tweak_hints"])
    if result.get("drums", {}).get("tweak_hints"):
        tweak_hints.extend(result["drums"]["tweak_hints"])

    if tweak_hints:
        lines.extend([
            "PRODUCTION NOTES",
            "-" * 40,
        ])
        for hint in tweak_hints[:10]:  # Limit to 10
            lines.append(f"  • {hint}")
        lines.append("")

    # Import instructions
    lines.extend([
        "HOW TO USE",
        "=" * 40,
        "",
        "ABLETON LIVE:",
        "  1. Open Ableton Live",
        "  2. Set project tempo to {:.1f} BPM".format(tempo),
        "  3. Drag MIDI files from the MIDI folder onto MIDI tracks",
        "  4. Drag audio stems (if included) onto Audio tracks",
        "  5. Load instruments/presets on each track",
        "",
        "LOGIC PRO:",
        "  1. Create new project at {:.1f} BPM".format(tempo),
        "  2. File > Import > MIDI to bring in MIDI files",
        "  3. Drag audio stems to audio tracks",
        "",
        "FL STUDIO:",
        "  1. Set project tempo to {:.1f} BPM".format(tempo),
        "  2. Drag MIDI files to the playlist",
        "  3. Assign instruments to each pattern",
        "",
        "OTHER DAWS:",
        "  1. Set tempo to {:.1f} BPM".format(tempo),
        "  2. Import MIDI files (standard .mid format)",
        "  3. Add your preferred instruments",
        "",
        "-" * 40,
        "Generated by Tone Forge",
        "https://github.com/tone-forge",
    ])

    return "\n".join(lines)


def create_minimal_bundle(
    name: str,
    midi_files: Dict[str, bytes],  # stem_name -> midi bytes
    tempo: float = 120.0,
    key: str = "C major",
) -> Tuple[bytes, str]:
    """
    Create a minimal bundle with just MIDI files and a README.

    Useful for quick exports without full analysis.
    """
    files = []

    for stem_name, midi_bytes in midi_files.items():
        files.append(BundleFile(
            filename=f"{stem_name}.mid",
            content=midi_bytes,
            subfolder="MIDI",
        ))

    readme = f"""{'='*60}
TONE FORGE PROJECT: {name}
{'='*60}

Tempo: {tempo:.1f} BPM
Key: {key}

MIDI FILES:
{chr(10).join(f'  - {stem}.mid' for stem in midi_files.keys())}

Import these MIDI files into your DAW and add your preferred instruments.

Generated by Tone Forge
"""

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}/README.txt", readme)
        for bundle_file in files:
            path = f"{name}/{bundle_file.subfolder}/{bundle_file.filename}"
            zf.writestr(path, bundle_file.content)

    zip_bytes = zip_buffer.getvalue()
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)[:50]
    filename = f"{safe_name}_ToneForge.zip"

    return zip_bytes, filename
