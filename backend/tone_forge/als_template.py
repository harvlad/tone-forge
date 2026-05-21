"""
Template-based Ableton Live Set (.als) generation.

Creates valid .als files that Ableton Live 11+ can open.
Uses a working template structure derived from real Ableton files.
"""

import gzip
import base64
import io
import logging
from typing import Dict, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _extract_notes_from_midi_content(midi_b64: str) -> List[Tuple[int, float, float, int]]:
    """
    Extract notes from base64-encoded MIDI content.

    Returns list of tuples: (pitch, start_sec, end_sec, velocity)
    """
    try:
        import pretty_midi
    except ImportError:
        logger.warning("pretty_midi not available, cannot decode MIDI content")
        return []

    try:
        midi_bytes = base64.b64decode(midi_b64)
        midi_file = io.BytesIO(midi_bytes)
        pm = pretty_midi.PrettyMIDI(midi_file)

        notes = []
        for instrument in pm.instruments:
            for note in instrument.notes:
                notes.append((
                    note.pitch,
                    note.start,
                    note.end,
                    note.velocity,
                ))

        # Sort by start time
        notes.sort(key=lambda x: x[1])
        return notes

    except Exception as e:
        logger.warning(f"Failed to extract notes from MIDI: {e}")
        return []


@dataclass
class MidiNote:
    """A single MIDI note."""
    pitch: int
    start_beats: float
    duration_beats: float
    velocity: int = 100


def create_als_from_analysis(
    name: str,
    tempo_bpm: float,
    key_root: int,
    key_scale: str,
    midi_stems: Dict[str, Dict],
    chords: List = None,
    template_path: str = None,
) -> Tuple[bytes, str]:
    """
    Create an ALS file from analysis results.

    Args:
        name: Project name
        tempo_bpm: Detected tempo
        key_root: Key root (0-11)
        key_scale: 'major' or 'minor'
        midi_stems: Dict of stem MIDI data from analysis
        chords: Optional detected chords
        template_path: Ignored (kept for compatibility)

    Returns:
        Tuple of (als_bytes, filename)
    """
    # Track colors by stem type
    STEM_COLORS = {
        'drums': 1,    # Red
        'bass': 3,     # Yellow
        'guitar': 2,   # Orange
        'piano': 9,    # Blue
        'other': 10,   # Purple
        'vocals': 12,  # Magenta
    }

    # Build tracks XML
    tracks_xml = []
    track_id = 3
    auto_id = 100

    stem_order = ['drums', 'bass', 'guitar', 'piano', 'other', 'vocals']

    for stem_key in stem_order:
        if stem_key not in midi_stems:
            continue

        stem_data = midi_stems[stem_key]

        # Try to get notes directly first (for test data)
        notes_raw = stem_data.get('notes', [])

        # If no notes but we have MIDI content, extract from it
        if not notes_raw and stem_data.get('content'):
            logger.debug(f"Extracting notes from MIDI content for stem: {stem_key}")
            notes_raw = _extract_notes_from_midi_content(stem_data['content'])

        if not notes_raw:
            logger.debug(f"No notes found for stem: {stem_key}")
            continue

        label = stem_data.get('label', stem_key.title())
        color = STEM_COLORS.get(stem_key, -1)

        # Convert notes to MidiNote objects
        notes = []
        for note_data in notes_raw:
            if len(note_data) >= 4:
                pitch, start_sec, end_sec, vel = note_data[:4]
                start_beats = (start_sec / 60.0) * tempo_bpm
                duration_beats = ((end_sec - start_sec) / 60.0) * tempo_bpm
                notes.append(MidiNote(
                    pitch=int(pitch),
                    start_beats=start_beats,
                    duration_beats=max(0.0625, duration_beats),
                    velocity=int(vel),
                ))

        if notes:
            track_xml, auto_id = _create_midi_track(
                track_id, f"{label} MIDI", color, notes, auto_id
            )
            tracks_xml.append(track_xml)
            track_id += 1

    # Build locators from chords
    locators_xml = _build_locators_xml(chords or [], tempo_bpm)

    # Build full ALS
    als_xml = _build_als_xml(
        tracks="\n".join(tracks_xml),
        tempo=tempo_bpm,
        key_root=key_root,
        key_scale=key_scale,
        locators_xml=locators_xml,
    )

    # Compress
    als_bytes = gzip.compress(als_xml.encode('utf-8'))

    # Filename
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)[:50]
    filename = f"{safe_name}.als"

    logger.info(f"Created ALS: {filename} ({len(tracks_xml)} tracks, {len(als_bytes)} bytes)")
    return als_bytes, filename


def create_als_from_analysis_base64(
    name: str,
    tempo_bpm: float,
    key_root: int,
    key_scale: str,
    midi_stems: Dict[str, Dict],
    chords: List = None,
    template_path: str = None,
) -> Tuple[str, str]:
    """Same as create_als_from_analysis but returns base64."""
    als_bytes, filename = create_als_from_analysis(
        name, tempo_bpm, key_root, key_scale, midi_stems, chords, template_path
    )
    return base64.b64encode(als_bytes).decode('ascii'), filename


def _create_midi_track(
    track_id: int,
    name: str,
    color: int,
    notes: List[MidiNote],
    auto_id_start: int,
) -> Tuple[str, int]:
    """Create a MIDI track XML with notes."""

    auto_id = auto_id_start

    # Calculate clip length (round up to nearest bar)
    if notes:
        max_end = max(n.start_beats + n.duration_beats for n in notes)
        # Round up to next bar (4 beats), minimum 4 beats
        clip_end = max(4, ((int(max_end) + 3) // 4) * 4)
    else:
        clip_end = 4

    # Group notes by pitch
    notes_by_pitch: Dict[int, List[MidiNote]] = {}
    for note in notes:
        if note.pitch not in notes_by_pitch:
            notes_by_pitch[note.pitch] = []
        notes_by_pitch[note.pitch].append(note)

    # Build KeyTracks XML
    key_tracks_xml = []
    note_id = 1
    for kt_id, pitch in enumerate(sorted(notes_by_pitch.keys())):
        pitch_notes = notes_by_pitch[pitch]
        notes_xml = []
        for n in pitch_notes:
            notes_xml.append(
                f'<MidiNoteEvent Time="{n.start_beats:.6f}" Duration="{n.duration_beats:.6f}" '
                f'Velocity="{n.velocity}" VelocityDeviation="0" OffVelocity="64" '
                f'Probability="1" IsEnabled="true" NoteId="{note_id}"/>'
            )
            note_id += 1

        key_tracks_xml.append(f'''<KeyTrack Id="{kt_id}">
	<MidiKey Value="{pitch}"/>
	<Notes>
		{chr(10).join(notes_xml)}
	</Notes>
</KeyTrack>''')

    # Build the full track
    track_xml = f'''<MidiTrack Id="{track_id}">
	<LomId Value="0"/>
	<LomIdView Value="0"/>
	<IsContentSelectedInDocument Value="false"/>
	<PreferredContentViewMode Value="0"/>
	<TrackDelay>
		<Value Value="0"/>
		<IsValueSampleBased Value="false"/>
	</TrackDelay>
	<Name>
		<EffectiveName Value="{name}"/>
		<UserName Value="{name}"/>
		<Annotation Value=""/>
		<MemorizedFirstClipName Value=""/>
	</Name>
	<Color Value="{color}"/>
	<AutomationEnvelopes>
		<Envelopes/>
	</AutomationEnvelopes>
	<TrackGroupId Value="-1"/>
	<TrackUnfolded Value="true"/>
	<DevicesListWrapper LomId="0"/>
	<ClipSlotsListWrapper LomId="0"/>
	<ViewData Value="{{}}"/>
	<TakeLanes>
		<TakeLanes/>
	</TakeLanes>
	<LinkedTrackGroupId Value="-1"/>
	<SavedPlayingSlot Value="-1"/>
	<SavedPlayingOffset Value="0"/>
	<Freeze Value="false"/>
	<VelocityDetail Value="0"/>
	<NeedArrangerRefreeze Value="true"/>
	<PostProcessFreezeClips Value="0"/>
	<DeviceChain>
		<AutomationLanes>
			<AutomationLanes/>
			<AreAdditionalAutomationLanesFolded Value="false"/>
		</AutomationLanes>
		<MidiInputRouting>
			<Target Value="MidiIn/External.All/-1"/>
			<UpperDisplayString Value="Ext: All Ins"/>
			<LowerDisplayString Value=""/>
			<MpeSettings>
				<Zone Value="0"/>
			</MpeSettings>
		</MidiInputRouting>
		<MidiOutputRouting>
			<Target Value="MidiOut/None"/>
			<UpperDisplayString Value="None"/>
			<LowerDisplayString Value=""/>
			<MpeSettings>
				<Zone Value="0"/>
			</MpeSettings>
		</MidiOutputRouting>
		<Mixer>
			<LomId Value="0"/>
			<LomIdView Value="0"/>
			<IsExpanded Value="true"/>
			<On>
				<LomId Value="0"/>
				<Manual Value="true"/>
				<AutomationTarget Id="{auto_id}">
					<LockEnvelope Value="0"/>
				</AutomationTarget>
				<MidiCCOnOffThresholds>
					<Min Value="64"/>
					<Max Value="127"/>
				</MidiCCOnOffThresholds>
			</On>
			<Volume>
				<LomId Value="0"/>
				<Manual Value="1"/>
				<MidiControllerRange>
					<Min Value="0.0003162"/>
					<Max Value="1.99526"/>
				</MidiControllerRange>
				<AutomationTarget Id="{auto_id + 1}">
					<LockEnvelope Value="0"/>
				</AutomationTarget>
				<ModulationTarget Id="{auto_id + 2}">
					<LockEnvelope Value="0"/>
				</ModulationTarget>
			</Volume>
			<Pan>
				<LomId Value="0"/>
				<Manual Value="0"/>
				<MidiControllerRange>
					<Min Value="-1"/>
					<Max Value="1"/>
				</MidiControllerRange>
				<AutomationTarget Id="{auto_id + 3}">
					<LockEnvelope Value="0"/>
				</AutomationTarget>
				<ModulationTarget Id="{auto_id + 4}">
					<LockEnvelope Value="0"/>
				</ModulationTarget>
			</Pan>
			<SendsListWrapper LomId="0"/>
			<Speaker>
				<LomId Value="0"/>
				<Manual Value="true"/>
				<AutomationTarget Id="{auto_id + 5}">
					<LockEnvelope Value="0"/>
				</AutomationTarget>
			</Speaker>
			<SoloSink Value="false"/>
			<PanMode Value="0"/>
			<Sends/>
			<CrossFadeState>
				<LomId Value="0"/>
				<Manual Value="1"/>
				<AutomationTarget Id="{auto_id + 6}">
					<LockEnvelope Value="0"/>
				</AutomationTarget>
			</CrossFadeState>
			<ViewStateSesstionTrackWidth Value="93"/>
		</Mixer>
		<MainSequencer>
			<LomId Value="0"/>
			<LomIdView Value="0"/>
			<IsExpanded Value="true"/>
			<On>
				<LomId Value="0"/>
				<Manual Value="true"/>
				<AutomationTarget Id="{auto_id + 7}">
					<LockEnvelope Value="0"/>
				</AutomationTarget>
				<MidiCCOnOffThresholds>
					<Min Value="64"/>
					<Max Value="127"/>
				</MidiCCOnOffThresholds>
			</On>
			<ModulationSourceCount Value="0"/>
			<ParametersListWrapper LomId="0"/>
			<Pointee Id="{auto_id + 8}"/>
			<LastSelectedTimeableIndex Value="0"/>
			<LastSelectedClipEnvelopeIndex Value="0"/>
			<LastPresetRef>
				<Value/>
			</LastPresetRef>
			<LockedScripts/>
			<IsFolded Value="false"/>
			<ShouldShowPresetName Value="false"/>
			<UserName Value=""/>
			<Annotation Value=""/>
			<SourceContext>
				<Value/>
			</SourceContext>
			<ClipSlotList>
				<ClipSlot Id="0">
					<LomId Value="0"/>
					<ClipSlot>
						<Value>
							<MidiClip Id="{auto_id + 9}" Time="0">
								<LomId Value="0"/>
								<LomIdView Value="0"/>
								<CurrentStart Value="0"/>
								<CurrentEnd Value="{clip_end}"/>
								<Loop>
									<LoopStart Value="0"/>
									<LoopEnd Value="{clip_end}"/>
									<StartRelative Value="0"/>
									<LoopOn Value="true"/>
									<OutMarker Value="{clip_end}"/>
									<HiddenLoopStart Value="0"/>
									<HiddenLoopEnd Value="{clip_end}"/>
								</Loop>
								<Name Value="{name}"/>
								<Annotation Value=""/>
								<Color Value="-1"/>
								<LaunchMode Value="0"/>
								<LaunchQuantisation Value="0"/>
								<TimeSelection>
									<AnchorTime Value="0"/>
									<OtherTime Value="0"/>
								</TimeSelection>
								<EnvelopesListWrapper LomId="0"/>
								<ScrollerTimePreserver>
									<LeftTime Value="0"/>
									<RightTime Value="{clip_end}"/>
								</ScrollerTimePreserver>
								<TimeSignature>
									<TimeSignatures>
										<RemoteableTimeSignature Id="0">
											<Numerator Value="4"/>
											<Denominator Value="4"/>
											<Time Value="0"/>
										</RemoteableTimeSignature>
									</TimeSignatures>
								</TimeSignature>
								<Envelopes>
									<Envelopes/>
								</Envelopes>
								<Grid>
									<FixedNumerator Value="1"/>
									<FixedDenominator Value="16"/>
									<GridIntervalPixel Value="20"/>
									<Ntoles Value="2"/>
									<SnapToGrid Value="true"/>
									<Fixed Value="false"/>
								</Grid>
								<FreezeStart Value="0"/>
								<FreezeEnd Value="0"/>
								<IsWarped Value="true"/>
								<TakeId Value="1"/>
								<Notes>
									<KeyTracks>
										{chr(10).join(key_tracks_xml)}
									</KeyTracks>
									<PerNoteEventStore/>
									<NoteIdGenerator/>
								</Notes>
								<BankSelectCoarse Value="-1"/>
								<BankSelectFine Value="-1"/>
								<ProgramChange Value="-1"/>
								<NoteEditorFoldInZoom Value="-1"/>
								<NoteEditorFoldInScroll Value="0"/>
								<NoteEditorFoldOutZoom Value="562"/>
								<NoteEditorFoldOutScroll Value="-410"/>
								<NoteEditorFoldScaleZoom Value="-1"/>
								<NoteEditorFoldScaleScroll Value="0"/>
								<IsRelative Value="false"/>
								<GrooveSettings>
									<GrooveId Value="-1"/>
								</GrooveSettings>
								<Disabled Value="false"/>
								<VelocityAmount Value="0"/>
								<FollowAction>
									<FollowTime Value="4"/>
									<IsLinked Value="true"/>
									<LoopIterations Value="1"/>
									<FollowActionA Value="4"/>
									<FollowActionB Value="0"/>
									<FollowChanceA Value="100"/>
									<FollowChanceB Value="0"/>
									<JumpIndexA Value="1"/>
									<JumpIndexB Value="1"/>
									<FollowActionEnabled Value="false"/>
								</FollowAction>
								<Ram Value="false"/>
							</MidiClip>
						</Value>
					</ClipSlot>
					<HasStop Value="true"/>
					<NeedRefreeze Value="true"/>
				</ClipSlot>
			</ClipSlotList>
			<MonitoringEnum Value="1"/>
			<Sample>
				<Value/>
			</Sample>
			<VolumeModulationTarget Id="{auto_id + 10}">
				<LockEnvelope Value="0"/>
			</VolumeModulationTarget>
			<TranspositionModulationTarget Id="{auto_id + 11}">
				<LockEnvelope Value="0"/>
			</TranspositionModulationTarget>
			<GrainSizeModulationTarget Id="{auto_id + 12}">
				<LockEnvelope Value="0"/>
			</GrainSizeModulationTarget>
			<FluxModulationTarget Id="{auto_id + 13}">
				<LockEnvelope Value="0"/>
			</FluxModulationTarget>
			<SampleOffsetModulationTarget Id="{auto_id + 14}">
				<LockEnvelope Value="0"/>
			</SampleOffsetModulationTarget>
			<PitchViewScrollPosition Value="-1073741824"/>
			<SampleOffsetModulationScrollPosition Value="-1073741824"/>
			<Recorder>
				<IsArmed Value="false"/>
				<TakeCounter Value="0"/>
			</Recorder>
		</MainSequencer>
		<FreezeSequencer>
			<LomId Value="0"/>
			<LomIdView Value="0"/>
			<IsExpanded Value="true"/>
			<On>
				<LomId Value="0"/>
				<Manual Value="true"/>
				<AutomationTarget Id="{auto_id + 15}">
					<LockEnvelope Value="0"/>
				</AutomationTarget>
				<MidiCCOnOffThresholds>
					<Min Value="64"/>
					<Max Value="127"/>
				</MidiCCOnOffThresholds>
			</On>
			<ModulationSourceCount Value="0"/>
			<ParametersListWrapper LomId="0"/>
			<Pointee Id="{auto_id + 16}"/>
			<LastSelectedTimeableIndex Value="0"/>
			<LastSelectedClipEnvelopeIndex Value="0"/>
			<LastPresetRef>
				<Value/>
			</LastPresetRef>
			<LockedScripts/>
			<IsFolded Value="false"/>
			<ShouldShowPresetName Value="false"/>
			<UserName Value=""/>
			<Annotation Value=""/>
			<SourceContext>
				<Value/>
			</SourceContext>
			<ClipSlotList>
				<ClipSlot Id="0">
					<LomId Value="0"/>
					<ClipSlot>
						<Value/>
					</ClipSlot>
					<HasStop Value="true"/>
					<NeedRefreeze Value="true"/>
				</ClipSlot>
			</ClipSlotList>
			<MonitoringEnum Value="1"/>
			<Sample>
				<Value/>
			</Sample>
			<VolumeModulationTarget Id="{auto_id + 17}">
				<LockEnvelope Value="0"/>
			</VolumeModulationTarget>
			<TranspositionModulationTarget Id="{auto_id + 18}">
				<LockEnvelope Value="0"/>
			</TranspositionModulationTarget>
			<GrainSizeModulationTarget Id="{auto_id + 19}">
				<LockEnvelope Value="0"/>
			</GrainSizeModulationTarget>
			<FluxModulationTarget Id="{auto_id + 20}">
				<LockEnvelope Value="0"/>
			</FluxModulationTarget>
			<SampleOffsetModulationTarget Id="{auto_id + 21}">
				<LockEnvelope Value="0"/>
			</SampleOffsetModulationTarget>
			<PitchViewScrollPosition Value="-1073741824"/>
			<SampleOffsetModulationScrollPosition Value="-1073741824"/>
			<Recorder>
				<IsArmed Value="false"/>
				<TakeCounter Value="0"/>
			</Recorder>
		</FreezeSequencer>
		<Devices/>
	</DeviceChain>
</MidiTrack>'''

    return track_xml, auto_id + 22


def _build_locators_xml(chords: List, tempo_bpm: float) -> str:
    """Build locators XML from chord progression."""
    if not chords:
        return "<Locators/>"

    locators = []
    locator_id = 0

    for chord in chords:
        # Handle both Chord objects and dicts
        if hasattr(chord, 'start_time'):
            time_sec = chord.start_time
            name = chord.name
        elif isinstance(chord, dict):
            time_sec = chord.get('start_time', 0)
            name = chord.get('name', 'Chord')
        else:
            continue

        # Convert time to beats
        time_beats = (time_sec / 60.0) * tempo_bpm

        locators.append(f'''			<Locator Id="{locator_id}">
				<LomId Value="0"/>
				<Time Value="{time_beats:.4f}"/>
				<Name Value="{name}"/>
				<Annotation Value=""/>
				<IsSongStart Value="false"/>
			</Locator>''')
        locator_id += 1

    if locators:
        return "<Locators>\n" + "\n".join(locators) + "\n\t\t</Locators>"
    return "<Locators/>"


def _build_als_xml(tracks: str, tempo: float, key_root: int, key_scale: str, locators_xml: str = "<Locators/>") -> str:
    """Build the complete ALS XML."""

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="11.0_11300" SchemaChangeCount="3" Creator="Tone Forge" Revision="">
	<LiveSet>
		<NextPointeeId Value="20000"/>
		<OverwriteProtectionNumber Value="2817"/>
		<LomId Value="0"/>
		<LomIdView Value="0"/>
		<Tracks>
{tracks}
		</Tracks>
		<MasterTrack>
			<LomId Value="0"/>
			<LomIdView Value="0"/>
			<IsContentSelectedInDocument Value="false"/>
			<PreferredContentViewMode Value="0"/>
			<TrackDelay>
				<Value Value="0"/>
				<IsValueSampleBased Value="false"/>
			</TrackDelay>
			<Name>
				<EffectiveName Value="Master"/>
				<UserName Value=""/>
				<Annotation Value=""/>
				<MemorizedFirstClipName Value=""/>
			</Name>
			<Color Value="-1"/>
			<AutomationEnvelopes>
				<Envelopes/>
			</AutomationEnvelopes>
			<TrackGroupId Value="-1"/>
			<TrackUnfolded Value="true"/>
			<DevicesListWrapper LomId="0"/>
			<ClipSlotsListWrapper LomId="0"/>
			<ViewData Value="{{}}"/>
			<TakeLanes>
				<TakeLanes/>
			</TakeLanes>
			<LinkedTrackGroupId Value="-1"/>
			<DeviceChain>
				<AutomationLanes>
					<AutomationLanes/>
					<AreAdditionalAutomationLanesFolded Value="false"/>
				</AutomationLanes>
				<AudioOutputRouting>
					<Target Value="AudioOut/External/S0"/>
					<UpperDisplayString Value="Ext. Out"/>
					<LowerDisplayString Value="1/2"/>
					<MpeSettings>
						<Zone Value="0"/>
					</MpeSettings>
				</AudioOutputRouting>
				<Mixer>
					<LomId Value="0"/>
					<LomIdView Value="0"/>
					<IsExpanded Value="true"/>
					<On>
						<LomId Value="0"/>
						<Manual Value="true"/>
						<AutomationTarget Id="1">
							<LockEnvelope Value="0"/>
						</AutomationTarget>
						<MidiCCOnOffThresholds>
							<Min Value="64"/>
							<Max Value="127"/>
						</MidiCCOnOffThresholds>
					</On>
					<Tempo>
						<LomId Value="0"/>
						<Manual Value="{tempo}"/>
						<MidiControllerRange>
							<Min Value="60"/>
							<Max Value="200"/>
						</MidiControllerRange>
						<AutomationTarget Id="2">
							<LockEnvelope Value="0"/>
						</AutomationTarget>
						<ModulationTarget Id="50">
							<LockEnvelope Value="0"/>
						</ModulationTarget>
					</Tempo>
					<TimeSignature>
						<TimeSignatures>
							<RemoteableTimeSignature Id="0">
								<Numerator Value="4"/>
								<Denominator Value="4"/>
								<Time Value="0"/>
							</RemoteableTimeSignature>
						</TimeSignatures>
					</TimeSignature>
					<GlobalGrooveAmount>
						<LomId Value="0"/>
						<Manual Value="100"/>
						<MidiControllerRange>
							<Min Value="0"/>
							<Max Value="131.069"/>
						</MidiControllerRange>
						<AutomationTarget Id="51">
							<LockEnvelope Value="0"/>
						</AutomationTarget>
						<ModulationTarget Id="52">
							<LockEnvelope Value="0"/>
						</ModulationTarget>
					</GlobalGrooveAmount>
					<CrossFade>
						<LomId Value="0"/>
						<Manual Value="0"/>
						<MidiControllerRange>
							<Min Value="-1"/>
							<Max Value="1"/>
						</MidiControllerRange>
						<AutomationTarget Id="53">
							<LockEnvelope Value="0"/>
						</AutomationTarget>
						<ModulationTarget Id="54">
							<LockEnvelope Value="0"/>
						</ModulationTarget>
					</CrossFade>
					<TempoAutomationViewBottom Value="60"/>
					<TempoAutomationViewTop Value="200"/>
					<Prehear>
						<Volume>
							<LomId Value="0"/>
							<Manual Value="0.707107"/>
							<MidiControllerRange>
								<Min Value="0.0003162"/>
								<Max Value="1.99526"/>
							</MidiControllerRange>
							<AutomationTarget Id="55">
								<LockEnvelope Value="0"/>
							</AutomationTarget>
							<ModulationTarget Id="56">
								<LockEnvelope Value="0"/>
							</ModulationTarget>
						</Volume>
						<MuteOnTarget Value="false"/>
					</Prehear>
					<Volume>
						<LomId Value="0"/>
						<Manual Value="1"/>
						<MidiControllerRange>
							<Min Value="0.0003162"/>
							<Max Value="1.99526"/>
						</MidiControllerRange>
						<AutomationTarget Id="57">
							<LockEnvelope Value="0"/>
						</AutomationTarget>
						<ModulationTarget Id="58">
							<LockEnvelope Value="0"/>
						</ModulationTarget>
					</Volume>
					<Pan>
						<LomId Value="0"/>
						<Manual Value="0"/>
						<MidiControllerRange>
							<Min Value="-1"/>
							<Max Value="1"/>
						</MidiControllerRange>
						<AutomationTarget Id="59">
							<LockEnvelope Value="0"/>
						</AutomationTarget>
						<ModulationTarget Id="60">
							<LockEnvelope Value="0"/>
						</ModulationTarget>
					</Pan>
					<ViewStateSesstionTrackWidth Value="93"/>
					<CrossFadeState>
						<LomId Value="0"/>
						<Manual Value="0"/>
						<AutomationTarget Id="61">
							<LockEnvelope Value="0"/>
						</AutomationTarget>
					</CrossFadeState>
				</Mixer>
			</DeviceChain>
		</MasterTrack>
		<Scenes>
			<Scene Id="0">
				<LomId Value="0"/>
				<Name Value=""/>
				<Annotation Value=""/>
				<Color Value="-1"/>
				<Tempo Value="{tempo}"/>
				<IsTempoEnabled Value="false"/>
				<TimeSignatureId Value="201"/>
				<IsTimeSignatureEnabled Value="false"/>
				<LomIdView Value="0"/>
				<ClipSlotsListWrapper LomId="0"/>
			</Scene>
		</Scenes>
		<Transport>
			<PhaseNudgeTempo Value="10"/>
			<LoopOn Value="false"/>
			<LoopStart Value="0"/>
			<LoopLength Value="16"/>
			<LoopIsSongStart Value="false"/>
			<CurrentTime Value="0"/>
			<PunchIn Value="false"/>
			<PunchOut Value="false"/>
			<MetronomeClickOn Value="false"/>
			<DrawMode Value="false"/>
		</Transport>
		{locators_xml}
		<DetailClipKeyMidis/>
		<TracksListWrapper LomId="0"/>
		<VisibleTracksListWrapper LomId="0"/>
		<ReturnTracksListWrapper LomId="0"/>
		<ScenesListWrapper LomId="0"/>
		<CuePointsListWrapper LomId="0"/>
		<ChooserBar>
			<Value Value="0"/>
		</ChooserBar>
		<Annotation Value=""/>
		<InstrumentBrowserScale Value="0"/>
		<Scale>
			<RootNote Value="{key_root}"/>
			<Name Value="{key_scale.title()}"/>
		</Scale>
		<ColorSequenceIndex Value="0"/>
	</LiveSet>
</Ableton>'''
