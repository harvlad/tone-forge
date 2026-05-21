"""
Ableton Live Set (.als) Session Generator.

Creates complete Ableton Live projects with:
- Audio tracks for stems
- MIDI tracks with extracted notes
- Tempo and key markers
- Effect chains based on FX analysis
- Instrument racks with inferred patches
"""

import gzip
import xml.etree.ElementTree as ET
from xml.dom import minidom
import base64
import logging
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import uuid
import time

logger = logging.getLogger(__name__)


@dataclass
class SessionTrack:
    """Represents a track in the session."""
    name: str
    type: str  # 'audio', 'midi'
    color: int
    audio_file: Optional[str] = None  # Path or base64 content
    midi_notes: Optional[List[Tuple[int, float, float, int]]] = None  # (pitch, start, end, vel)
    effects: Optional[List[Dict]] = None
    volume_db: float = 0.0
    pan: float = 0.0  # -1 to 1
    muted: bool = False
    solo: bool = False


@dataclass
class SessionMarker:
    """Represents a marker/locator."""
    time: float  # In beats
    name: str
    color: int = 0


@dataclass
class AbletonSession:
    """Complete Ableton session definition."""
    name: str
    tempo_bpm: float
    time_signature: Tuple[int, int]  # (numerator, denominator)
    key_root: int  # 0-11
    key_scale: str  # 'major', 'minor'
    duration_beats: float
    tracks: List[SessionTrack]
    markers: List[SessionMarker]

    # Analysis metadata
    source_url: Optional[str] = None
    detected_type: Optional[str] = None
    analysis_summary: Optional[str] = None


# Ableton color palette (index -> color name for reference)
ABLETON_COLORS = {
    0: 'rose',
    1: 'red',
    2: 'orange',
    3: 'yellow',
    4: 'lime',
    5: 'green',
    6: 'mint',
    7: 'cyan',
    8: 'sky',
    9: 'blue',
    10: 'purple',
    11: 'violet',
    12: 'magenta',
    13: 'gray',
}

# Track type to color mapping
TRACK_COLORS = {
    'drums': 1,    # Red
    'bass': 3,     # Yellow
    'guitar': 2,   # Orange
    'keys': 9,     # Blue
    'synth': 10,   # Purple
    'vocals': 12,  # Magenta
    'other': 13,   # Gray
}


def create_ableton_session(
    name: str,
    tempo_bpm: float,
    key_root: int,
    key_scale: str,
    duration_sec: float,
    stems: Dict[str, str] = None,  # stem_name -> base64 audio or path
    midi_data: Dict[str, List] = None,  # stem_name -> list of notes
    fx_analysis: Dict = None,  # FX chain analysis per stem
    chords: List = None,  # Detected chord progression
    source_url: str = None,
) -> AbletonSession:
    """
    Create an Ableton session from analysis results.

    Args:
        name: Session/project name
        tempo_bpm: Detected tempo
        key_root: Key root (0-11, C=0)
        key_scale: 'major' or 'minor'
        duration_sec: Total duration in seconds
        stems: Dict of stem audio data
        midi_data: Dict of MIDI note lists per stem
        fx_analysis: FX chain analysis results
        chords: Detected chord progression
        source_url: Original source URL

    Returns:
        AbletonSession object ready for export
    """
    duration_beats = (duration_sec / 60.0) * tempo_bpm

    tracks = []

    # Create audio tracks for stems
    if stems:
        for stem_name, audio_data in stems.items():
            color = TRACK_COLORS.get(stem_name.lower(), 13)

            # Get FX for this stem if available
            effects = None
            if fx_analysis and stem_name in fx_analysis:
                effects = _convert_fx_to_devices(fx_analysis[stem_name])

            track = SessionTrack(
                name=f"{stem_name.title()} (Audio)",
                type='audio',
                color=color,
                audio_file=audio_data,
                effects=effects,
            )
            tracks.append(track)

    # Create MIDI tracks
    if midi_data:
        for stem_name, notes in midi_data.items():
            if not notes:
                continue

            color = TRACK_COLORS.get(stem_name.lower(), 13)

            track = SessionTrack(
                name=f"{stem_name.title()} (MIDI)",
                type='midi',
                color=color,
                midi_notes=notes,
            )
            tracks.append(track)

    # Create chord track if chords detected
    if chords:
        chord_notes = _chords_to_midi(chords)
        if chord_notes:
            track = SessionTrack(
                name="Chords",
                type='midi',
                color=9,  # Blue
                midi_notes=chord_notes,
            )
            tracks.append(track)

    # Create markers for chord changes
    markers = []
    if chords:
        for i, chord in enumerate(chords[:20]):  # Limit markers
            markers.append(SessionMarker(
                time=(chord.start_time / 60.0) * tempo_bpm,  # Convert to beats
                name=chord.name,
                color=9,
            ))

    return AbletonSession(
        name=name,
        tempo_bpm=tempo_bpm,
        time_signature=(4, 4),
        key_root=key_root,
        key_scale=key_scale,
        duration_beats=duration_beats,
        tracks=tracks,
        markers=markers,
        source_url=source_url,
    )


def export_als(session: AbletonSession, output_path: str = None) -> Tuple[bytes, str]:
    """
    Export session to Ableton Live Set format (.als).

    Args:
        session: AbletonSession to export
        output_path: Optional path to write file

    Returns:
        Tuple of (als_bytes, filename)
    """
    # Build XML structure
    root = _create_als_xml(session)

    # Convert to string with proper formatting
    xml_str = ET.tostring(root, encoding='unicode')

    # Ableton expects specific XML declaration
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    full_xml = xml_declaration + xml_str

    # Compress with gzip
    als_bytes = gzip.compress(full_xml.encode('utf-8'))

    # Generate filename
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in session.name)
    filename = f"{safe_name[:50]}.als"

    if output_path:
        with open(output_path, 'wb') as f:
            f.write(als_bytes)
        logger.info(f"Exported Ableton session to {output_path}")

    return als_bytes, filename


def export_als_base64(session: AbletonSession) -> Tuple[str, str]:
    """
    Export session to base64-encoded .als data.

    Returns:
        Tuple of (base64_content, filename)
    """
    als_bytes, filename = export_als(session)
    return base64.b64encode(als_bytes).decode('ascii'), filename


def _create_als_xml(session: AbletonSession) -> ET.Element:
    """Create the XML structure for an Ableton Live Set."""

    # Root element
    root = ET.Element('Ableton')
    root.set('MajorVersion', '5')
    root.set('MinorVersion', '11.0_11300')
    root.set('SchemaChangeCount', '3')
    root.set('Creator', 'Tone Forge')
    root.set('Revision', str(uuid.uuid4().hex[:16]))

    # LiveSet
    live_set = ET.SubElement(root, 'LiveSet')

    # NextPointeeId (required)
    next_id = ET.SubElement(live_set, 'NextPointeeId')
    next_id.set('Value', '1000')

    # OverwriteProtectionNumber
    protect = ET.SubElement(live_set, 'OverwriteProtectionNumber')
    protect.set('Value', '2817')

    # LomId
    lom = ET.SubElement(live_set, 'LomId')
    lom.set('Value', '0')

    # Tracks container
    tracks = ET.SubElement(live_set, 'Tracks')

    track_id = 10
    for i, track in enumerate(session.tracks):
        track_id += 1
        if track.type == 'audio':
            _add_audio_track(tracks, track, track_id, session.tempo_bpm)
        else:
            _add_midi_track(tracks, track, track_id, session.tempo_bpm)

    # Master track
    master = ET.SubElement(live_set, 'MasterTrack')
    _add_master_track_content(master)

    # Transport
    _add_transport(live_set, session)

    # Scene/arrangement info
    _add_scenes(live_set, session)

    # Locators (markers)
    locators = ET.SubElement(live_set, 'Locators')
    for i, marker in enumerate(session.markers):
        _add_locator(locators, marker, i)

    return root


def _add_audio_track(parent: ET.Element, track: SessionTrack, track_id: int, tempo: float):
    """Add an audio track to the XML."""
    audio_track = ET.SubElement(parent, 'AudioTrack')
    audio_track.set('Id', str(track_id))

    # LomId
    lom = ET.SubElement(audio_track, 'LomId')
    lom.set('Value', '0')

    # Name
    name_elem = ET.SubElement(audio_track, 'Name')
    eff_name = ET.SubElement(name_elem, 'EffectiveName')
    eff_name.set('Value', track.name)
    user_name = ET.SubElement(name_elem, 'UserName')
    user_name.set('Value', track.name)

    # Color
    color = ET.SubElement(audio_track, 'Color')
    color.set('Value', str(track.color))

    # DeviceChain
    device_chain = ET.SubElement(audio_track, 'DeviceChain')
    _add_device_chain_content(device_chain, track.effects)

    # Mixer
    mixer = ET.SubElement(device_chain, 'Mixer')
    _add_mixer_content(mixer, track.volume_db, track.pan)


def _add_midi_track(parent: ET.Element, track: SessionTrack, track_id: int, tempo: float):
    """Add a MIDI track to the XML."""
    midi_track = ET.SubElement(parent, 'MidiTrack')
    midi_track.set('Id', str(track_id))

    # LomId
    lom = ET.SubElement(midi_track, 'LomId')
    lom.set('Value', '0')

    # Name
    name_elem = ET.SubElement(midi_track, 'Name')
    eff_name = ET.SubElement(name_elem, 'EffectiveName')
    eff_name.set('Value', track.name)
    user_name = ET.SubElement(name_elem, 'UserName')
    user_name.set('Value', track.name)

    # Color
    color = ET.SubElement(midi_track, 'Color')
    color.set('Value', str(track.color))

    # DeviceChain with MIDI content
    device_chain = ET.SubElement(midi_track, 'DeviceChain')

    # Add MIDI clip if we have notes
    if track.midi_notes:
        _add_midi_clip(device_chain, track.midi_notes, track.name, tempo)

    # Mixer
    mixer = ET.SubElement(device_chain, 'Mixer')
    _add_mixer_content(mixer, track.volume_db, track.pan)


def _add_midi_clip(parent: ET.Element, notes: List, name: str, tempo: float):
    """Add MIDI clip with notes to the device chain."""
    # MainSequencer
    main_seq = ET.SubElement(parent, 'MainSequencer')

    # ClipSlotList
    clip_slots = ET.SubElement(main_seq, 'ClipSlotList')
    clip_slot = ET.SubElement(clip_slots, 'ClipSlot')
    clip_slot.set('Id', '0')

    # ClipSlot Value
    clip_slot_value = ET.SubElement(clip_slot, 'Value')

    # MidiClip
    midi_clip = ET.SubElement(clip_slot_value, 'MidiClip')
    midi_clip.set('Id', '0')

    # Time
    time_elem = ET.SubElement(midi_clip, 'Time')
    time_elem.set('Value', '0')

    # Name
    clip_name = ET.SubElement(midi_clip, 'Name')
    clip_name.set('Value', name)

    # Color
    color = ET.SubElement(midi_clip, 'Color')
    color.set('Value', '9')

    # Loop settings
    loop = ET.SubElement(midi_clip, 'Loop')
    loop_start = ET.SubElement(loop, 'LoopStart')
    loop_start.set('Value', '0')
    loop_end = ET.SubElement(loop, 'LoopEnd')

    # Calculate end time
    if notes:
        max_end = max(n[2] for n in notes)  # end time in seconds
        end_beats = (max_end / 60.0) * tempo
    else:
        end_beats = 4.0
    loop_end.set('Value', str(end_beats))

    # Notes
    notes_elem = ET.SubElement(midi_clip, 'Notes')
    key_tracks = ET.SubElement(notes_elem, 'KeyTracks')

    # Group notes by pitch
    notes_by_pitch = {}
    for pitch, start, end, vel in notes:
        if pitch not in notes_by_pitch:
            notes_by_pitch[pitch] = []
        # Convert time from seconds to beats
        start_beats = (start / 60.0) * tempo
        duration_beats = ((end - start) / 60.0) * tempo
        notes_by_pitch[pitch].append((start_beats, duration_beats, vel))

    for pitch, pitch_notes in sorted(notes_by_pitch.items()):
        key_track = ET.SubElement(key_tracks, 'KeyTrack')
        key_track.set('Id', str(pitch))

        midi_key = ET.SubElement(key_track, 'MidiKey')
        midi_key.set('Value', str(pitch))

        notes_list = ET.SubElement(key_track, 'Notes')

        for start_beats, duration_beats, vel in pitch_notes:
            midi_note = ET.SubElement(notes_list, 'MidiNoteEvent')
            midi_note.set('Time', f'{start_beats:.6f}')
            midi_note.set('Duration', f'{duration_beats:.6f}')
            midi_note.set('Velocity', str(vel))
            midi_note.set('VelocityDeviation', '0')
            midi_note.set('OffVelocity', '64')
            midi_note.set('Probability', '1')
            midi_note.set('IsEnabled', 'true')


def _add_device_chain_content(parent: ET.Element, effects: Optional[List[Dict]]):
    """Add device chain content (effects)."""
    # AutomationLanes
    auto_lanes = ET.SubElement(parent, 'AutomationLanes')

    # Devices
    devices = ET.SubElement(parent, 'Devices')

    if effects:
        for i, effect in enumerate(effects):
            _add_effect_device(devices, effect, i)


def _add_effect_device(parent: ET.Element, effect: Dict, device_id: int):
    """Add an effect device to the chain."""
    # This is simplified - Ableton devices have complex XML structures
    # For now, we add comments/annotations about what effects should be there

    device = ET.SubElement(parent, 'PluginDevice')
    device.set('Id', str(device_id))

    # Note about the effect
    ET.SubElement(device, 'Comment').text = f"Suggested: {effect.get('name', 'Unknown Effect')}"


def _add_mixer_content(parent: ET.Element, volume_db: float, pan: float):
    """Add mixer settings."""
    # Volume
    volume = ET.SubElement(parent, 'Volume')
    vol_manual = ET.SubElement(volume, 'Manual')
    # Convert dB to Ableton's linear scale (0-1 where 0.85 ≈ 0dB)
    vol_linear = 10 ** (volume_db / 20) * 0.85
    vol_manual.set('Value', f'{min(vol_linear, 1.0):.6f}')

    # Pan
    pan_elem = ET.SubElement(parent, 'Pan')
    pan_manual = ET.SubElement(pan_elem, 'Manual')
    pan_manual.set('Value', f'{pan:.6f}')

    # Solo/Mute
    solo = ET.SubElement(parent, 'Solo')
    solo.set('Value', 'false')

    mute = ET.SubElement(parent, 'Mute')
    mute.set('Value', 'false')


def _add_master_track_content(parent: ET.Element):
    """Add master track content."""
    lom = ET.SubElement(parent, 'LomId')
    lom.set('Value', '0')

    # DeviceChain
    device_chain = ET.SubElement(parent, 'DeviceChain')
    mixer = ET.SubElement(device_chain, 'Mixer')
    _add_mixer_content(mixer, 0.0, 0.0)


def _add_transport(parent: ET.Element, session: AbletonSession):
    """Add transport settings (tempo, time signature)."""
    # Tempo
    tempo = ET.SubElement(parent, 'Tempo')
    tempo_manual = ET.SubElement(tempo, 'Manual')
    tempo_manual.set('Value', f'{session.tempo_bpm:.6f}')

    # TimeSignature
    time_sig = ET.SubElement(parent, 'TimeSignature')
    ts_num = ET.SubElement(time_sig, 'Numerator')
    ts_num.set('Value', str(session.time_signature[0]))
    ts_denom = ET.SubElement(time_sig, 'Denominator')
    ts_denom.set('Value', str(session.time_signature[1]))

    # Global Quantization
    global_quant = ET.SubElement(parent, 'GlobalQuantisation')
    global_quant.set('Value', '4')

    # Key and Scale (Ableton 11+)
    scale = ET.SubElement(parent, 'Scale')
    root_note = ET.SubElement(scale, 'RootNote')
    root_note.set('Value', str(session.key_root))
    scale_name = ET.SubElement(scale, 'Name')
    scale_name.set('Value', session.key_scale.title())


def _add_scenes(parent: ET.Element, session: AbletonSession):
    """Add scene information."""
    scenes = ET.SubElement(parent, 'Scenes')

    # Add one default scene
    scene = ET.SubElement(scenes, 'Scene')
    scene.set('Id', '0')

    name = ET.SubElement(scene, 'Name')
    name.set('Value', 'Scene 1')

    tempo = ET.SubElement(scene, 'Tempo')
    tempo.set('Value', f'{session.tempo_bpm:.0f}')


def _add_locator(parent: ET.Element, marker: SessionMarker, loc_id: int):
    """Add a locator/marker."""
    locator = ET.SubElement(parent, 'Locator')
    locator.set('Id', str(loc_id))

    lom = ET.SubElement(locator, 'LomId')
    lom.set('Value', '0')

    time_elem = ET.SubElement(locator, 'Time')
    time_elem.set('Value', f'{marker.time:.6f}')

    name = ET.SubElement(locator, 'Name')
    name.set('Value', marker.name)


def _convert_fx_to_devices(fx_analysis) -> List[Dict]:
    """Convert FX analysis to device list."""
    devices = []

    if hasattr(fx_analysis, 'suggested_chain'):
        for fx_name in fx_analysis.suggested_chain:
            devices.append({'name': fx_name, 'type': 'plugin'})

    return devices


def _chords_to_midi(chords) -> List[Tuple[int, float, float, int]]:
    """Convert chord objects to MIDI notes."""
    notes = []
    base_octave = 4  # C4 = 60

    for chord in chords:
        # Get chord intervals
        from .chord_detector import CHORD_TEMPLATES
        intervals = CHORD_TEMPLATES.get(chord.quality, [0, 4, 7])

        for interval in intervals[:4]:  # Limit to 4 notes
            pitch = chord.root + interval + (base_octave * 12)
            notes.append((
                pitch,
                chord.start_time,
                chord.end_time,
                80  # Medium velocity
            ))

    return notes
