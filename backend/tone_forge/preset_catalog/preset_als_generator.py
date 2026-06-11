"""Generate minimal ALS files for preset rendering.

Creates Ableton Live Sets that:
- Contain MIDI notes from test sequence
- Reference a specific preset with full UltraAnalog device
- Can be batch-exported to audio

The approach is to embed the full UltraAnalog device XML from a template,
with the preset reference modified to point to the target preset.
"""
from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass
from html import escape as html_escape
from pathlib import Path
from typing import List, Optional, Tuple

from .preset_discovery import PresetInfo, safe_filename


def xml_escape(s: str) -> str:
    """Escape special XML characters."""
    return html_escape(s, quote=True)
from .test_sequence import TestNote, get_test_sequence_for_type

logger = logging.getLogger(__name__)

# Ableton Core Library path pattern
CORE_LIBRARY_PATTERN = "Contents/App-Resources/Core Library"


@dataclass(frozen=True)
class DeviceConfig:
    """Per-instrument schema descriptor.

    Captures the device-element name in the ``.adv`` XML, the minimum
    direct-child count required for a non-trivial parameter tree, and a
    tuple of children that must be present. Lets the otherwise generic
    embed / validate logic remain in one place across all supported
    Ableton instruments.

    Each entry must be empirically validated against a real ``.adv``
    sample of that instrument; see ``scripts/discover_device_schema.py``.
    """

    instrument: str            # e.g. "Analog", "Wavetable"
    tag_name: str              # device XML element, e.g. "UltraAnalog"
    min_children: int          # below this is treated as empty/defective
    required_children: tuple   # element names that MUST appear in body


# Per-instrument schema configs. Add entries here as new instruments are
# brought online via Phase 2 (empirical discovery + smoke test).
#
# To add a new instrument:
#   1. Run scripts/discover_device_schema.py against a sample .adv for it.
#   2. Pick 4-6 representative direct children that distinguish a real
#      parameter tree from an empty default.
#   3. Choose min_children to be slightly below the empirical median
#      direct-child count for that device.
#   4. Add the entry below. The generation pipeline will pick it up
#      automatically via DEVICE_CONFIG[preset.instrument].
DEVICE_CONFIG: dict = {
    "Analog": DeviceConfig(
        instrument="Analog",
        tag_name="UltraAnalog",
        # Live 12 .adv files contain ~60 direct children; the previous
        # defective template emitted 0 children plus a LastPresetRef.
        min_children=30,
        # Representative of the UltraAnalog Live 12 schema (signal chains,
        # polyphony, global volume / octave) — covers both oscillator +
        # filter routing and global voicing parameters.
        required_children=(
            "SignalChain1",
            "SignalChain2",
            "Volume",
            "Polyphony",
            "Octave",
        ),
    ),
    # The following 7 entries were derived empirically via
    # scripts/discover_device_schema.py against the Ableton Live 12
    # Standard Core Library (10 samples per instrument). min_children
    # is 0.6 × median direct-child count; required_children are
    # device-specific tags universal across 10/10 samples.
    "Wavetable": DeviceConfig(
        instrument="Wavetable",
        tag_name="InstrumentVector",
        # Empirical median = 132 direct children.
        min_children=79,
        required_children=(
            "Voice_Oscillator1_On",
            "Voice_Oscillator1_Pitch_Transpose",
            "Voice_Oscillator1_Wavetables_WavePosition",
            "AmpEnvelopeDisplayMode",
            "SpriteName1",
        ),
    ),
    "Operator": DeviceConfig(
        instrument="Operator",
        tag_name="Operator",
        # Empirical median = 30 direct children. Note: the four FM
        # operators are list-suffixed children (<Operator.0> …
        # <Operator.3>) and validate via the list-member Id check;
        # we use the non-list synthesis sub-modules here.
        min_children=18,
        required_children=(
            "Globals",
            "Filter",
            "Lfo",
            "EnvScale",
            "Shaper",
        ),
    ),
    "Drift": DeviceConfig(
        instrument="Drift",
        tag_name="Drift",
        # Empirical median = 101 direct children.
        min_children=60,
        required_children=(
            "Filter_Frequency",
            "Filter_Resonance",
            "Filter_Type",
            "Lfo_Mode",
            "Lfo_Rate",
        ),
    ),
    "Meld": DeviceConfig(
        instrument="Meld",
        tag_name="InstrumentMeld",
        # Empirical median = 154 direct children.
        min_children=92,
        required_children=(
            "MeldVoice_EngineA_On",
            "MeldVoice_EngineA_Oscillator_OscillatorType",
            "MeldVoice_EngineA_Filter_FilterType",
            "MeldVoice_EngineA_Filter_Frequency",
            "MeldVoice_EngineA_Lfo1_GeneratorType",
        ),
    ),
    "Electric": DeviceConfig(
        instrument="Electric",
        # Historical Live name — the Electric preset device is still
        # called LoungeLizard in the .adv XML.
        tag_name="LoungeLizard",
        # Empirical median = 52 direct children.
        min_children=31,
        required_children=(
            "MalletStiffness",
            "MalletForceStrength",
            "MalletNoisePitch",
            "ForkReleaseTime",
            "ForkTineDecay",
        ),
    ),
    "Tension": DeviceConfig(
        instrument="Tension",
        # Historical Live name — Tension's .adv XML uses StringStudio.
        tag_name="StringStudio",
        # Empirical median = 129 direct children.
        min_children=77,
        required_children=(
            "Polyphony",
            "Octave",
            "Transpose",
            "VibratoToggle",
            "KeyboardUnisonToggle",
        ),
    ),
    "Collision": DeviceConfig(
        instrument="Collision",
        tag_name="Collision",
        # Empirical median = 39 direct children.
        min_children=23,
        required_children=(
            "Mallet",
            "Noise",
            "Resonator1",
            "Resonator2",
            "Polyphony",
        ),
    ),
}


def get_device_config(instrument: str) -> DeviceConfig:
    """Look up the schema config for an instrument, raising loudly if absent.

    Refusing to default-fall-back is deliberate: a missing config means
    we have not run the discovery + smoke-test step for that instrument,
    and silently emitting an unvalidated ALS would re-introduce the
    exact class of regression the catalog-integrity gate exists to
    prevent.
    """
    cfg = DEVICE_CONFIG.get(instrument)
    if cfg is None:
        raise ValueError(
            f"No DeviceConfig for instrument {instrument!r}. Supported: "
            f"{sorted(DEVICE_CONFIG)}. Run scripts/discover_device_schema.py "
            f"to populate this entry."
        )
    return cfg


class AdvEmbedError(RuntimeError):
    """Raised when a `.adv` cannot be parsed or its device block extracted.

    This intentionally has no silent fallback: returning a default template
    is what caused the original audio-collapse defect, where 99 presets
    rendered to 7 unique waveforms because the device body was empty.
    """


class AlsValidationError(RuntimeError):
    """Raised when the constructed device XML fails structural sanity
    checks (insufficient children or missing required parameters).
    """


def _load_adv_device(
    adv_path: Path,
    preset: "PresetInfo",
    config: DeviceConfig,
    device_id: int = 500,
    internal_id_start: int = 1000,
) -> str:
    """Extract the device parameter tree from a `.adv` preset and prepare
    it for embedding into an ALS.

    Steps:
    1. gunzip the `.adv` bytes
    2. Verify root is `<Ableton>` and contains exactly one ``<config.tag_name>``
       child
    3. Extract the ``<config.tag_name>...</config.tag_name>`` substring
    4. Patch `<UserName Value="...">` to ``preset.name`` (xml-escaped)
    5. Renumber all ``Id="0"`` to unique sequential IDs starting at 1000 so
       the embedded block's IDs do not collide with the surrounding ALS

    Raises:
        AdvEmbedError: on any failure. No silent fallback.
    """
    tag = config.tag_name
    try:
        raw = gzip.decompress(adv_path.read_bytes())
    except Exception as exc:
        raise AdvEmbedError(f"failed to gunzip {adv_path}: {exc}") from exc

    text = raw.decode("utf-8", errors="replace")

    # Verify <Ableton ...> root marker is present.
    if "<Ableton" not in text:
        raise AdvEmbedError(f"{adv_path}: missing <Ableton> root")

    # Extract the FIRST <tag ...>...</tag> block.
    # Note: `\b` alone matches between word char and `.`, so `<Operator\b`
    # would match both `<Operator ...>` (device) AND `<Operator.0>` (list
    # member) — the FM operator device. Use a stricter lookahead that
    # only accepts whitespace / `/` / `>` after the tag name, so list-
    # suffixed children (`<Foo.0>`) are not confused with the device tag.
    boundary = r"(?=[\s/>])"
    block_re = re.compile(rf"<{tag}{boundary}[\s\S]*?</{tag}>")
    match = block_re.search(text)
    if not match:
        raise AdvEmbedError(f"{adv_path}: no <{tag}> element found")

    # There must be exactly one occurrence (sanity check on .adv).
    n_devices = len(re.findall(rf"<{tag}{boundary}", text))
    if n_devices != 1:
        raise AdvEmbedError(
            f"{adv_path}: expected exactly one <{tag}>, found {n_devices}"
        )

    block = match.group(0)

    # The opening device tag is itself a list member of the surrounding
    # <Devices> list and must carry an Id. Source .adv files omit this (the
    # browser fills it on import). Without it, Ableton fails ALS parse with
    # "Not all list members have Ids." Reuse a fixed Id in a range disjoint
    # from the renumbered .adv-internal Ids (which start at 1000).
    if not re.match(rf'<{tag}\b[^>]*\bId\s*=', block):
        block = re.sub(
            rf'<{tag}\b',
            f'<{tag} Id="{device_id}"',
            block,
            count=1,
        )

    # Patch UserName so it reflects the preset name when loaded into the ALS.
    escaped_name = xml_escape(preset.name)
    block, n_subs = re.subn(
        r'<UserName Value="[^"]*"\s*/>',
        f'<UserName Value="{escaped_name}" />',
        block,
        count=1,
    )
    if n_subs == 0:
        # Insert a <UserName> just before the closing tag if it was absent.
        block = block.replace(
            f"</{tag}>",
            f'<UserName Value="{escaped_name}" /></{tag}>',
        )

    # Renumber Id="0" -> unique sequential IDs starting at internal_id_start
    # to avoid collisions with the rest of the ALS document.
    id_counter = [internal_id_start]

    def _renumber(_m: "re.Match[str]") -> str:
        new_id = id_counter[0]
        id_counter[0] += 1
        return f'Id="{new_id}"'

    block = re.sub(r'Id="0"', _renumber, block)

    # Inject Id="<unique>" on list-suffixed members (<Foo.0>, <Foo.1>, ...)
    # that lack an Id attribute. .adv files omit this attribute on some list
    # children (Ableton fills it on preset import); the surrounding ALS
    # parser refuses to load if any list member is Id-less. Empirically the
    # gap appears as <Envelope.0>/<Envelope.1> inside each SignalChain.
    def _inject_list_id(m: "re.Match[str]") -> str:
        name = m.group(1)
        attrs = m.group(2)
        closer = m.group(3)
        if re.search(r'\bId\s*=', attrs):
            return m.group(0)
        new_id = id_counter[0]
        id_counter[0] += 1
        attrs_trimmed = attrs.strip()
        sep = " " if attrs_trimmed else ""
        return f'<{name} Id="{new_id}"{sep}{attrs_trimmed}{closer}>'

    block = re.sub(
        r'<([A-Za-z_][\w]*\.\d+)\b([^>]*?)(/?)>',
        _inject_list_id,
        block,
    )

    # Neutralise the embedded <LastPresetRef> Path: source .adv files carry
    # Ableton's internal build-server path (/Volumes/data/tmp/trunk/...)
    # which does not exist on operator systems. The embedded parameter tree
    # is the authoritative state; the path is metadata that can prompt
    # missing-file dialogs or trigger default-patch fallbacks on load.
    # Null both <Path> and <RelativePath> to make the reference inert.
    def _neutralise_path(m: "re.Match[str]") -> str:
        body = m.group(0)
        body = re.sub(r'<Path Value="[^"]*"', '<Path Value=""', body)
        body = re.sub(
            r'<RelativePath Value="[^"]*"', '<RelativePath Value=""', body
        )
        return body

    block = re.sub(
        r'<LastPresetRef\b[\s\S]*?</LastPresetRef>',
        _neutralise_path,
        block,
    )

    return block


def _assert_device_nontrivial(xml: str, config: DeviceConfig) -> None:
    """Fail-loud structural check on a constructed device XML block.

    Used in :func:`create_preset_als` after device-XML construction to ensure
    we never write an empty or under-populated device chain to disk. The
    previous defective implementation (Analog) emitted only a
    ``<LastPresetRef>`` pointer with zero parameter children; that
    produced 99 distinct ALS files which all rendered to a default-patch
    sound.

    Raises:
        AlsValidationError: if the device block has fewer than
            ``config.min_children`` direct children OR is missing any of
            the parameters in ``config.required_children``.
    """
    tag = config.tag_name
    match = re.search(rf"<{tag}\b([^>]*)>([\s\S]*)</{tag}>", xml)
    if not match:
        raise AlsValidationError(f"no <{tag}> element in device xml")
    opening_attrs = match.group(1)
    body = match.group(2)

    # The device opening tag is itself a list member of <Devices>;
    # ALS parser requires Id="…" on it.
    if not re.search(r'\bId\s*=', opening_attrs):
        raise AlsValidationError(
            f"{tag} opening tag missing Id attribute "
            "(required as <Devices> list member)"
        )
    # Count direct top-level children by matching only opening tags whose
    # depth is 1 inside the device. Approximate via a depth counter.
    depth = 0
    direct_children = 0
    for tok in re.finditer(r"<(/?)([A-Za-z_][\w.-]*)\b[^>]*?(/?)>", body):
        is_close = bool(tok.group(1))
        is_self_close = bool(tok.group(3))
        if is_close:
            depth -= 1
        else:
            if depth == 0:
                direct_children += 1
            if not is_self_close:
                depth += 1
    if direct_children < config.min_children:
        raise AlsValidationError(
            f"{tag} has only {direct_children} direct children "
            f"(< {config.min_children}); device chain looks empty"
        )
    missing = [
        name
        for name in config.required_children
        if not re.search(rf"<{name}\b", body)
    ]
    if missing:
        raise AlsValidationError(
            f"{tag} missing required children: {missing}"
        )

    # List-suffixed members (<Foo.0>, <Foo.1>, ...) must each carry an Id
    # attribute. Source .adv files omit this on some members (Ableton fills
    # it on preset import); the surrounding ALS parser refuses to load if
    # any list member is Id-less, with the diagnostic
    #   "Not all list members have Ids. (at line N, column M)"
    # surfacing at the device-opening tag. Closes the coverage gap that
    # let the Saw Filter Bass equivalence-test ALS pass this validator
    # while Ableton itself rejected it at parse time.
    list_missing: list[str] = []
    for m in re.finditer(r"<([A-Za-z_][\w]*\.\d+)\b([^>]*?)(/?)>", body):
        if not re.search(r"\bId\s*=", m.group(2)):
            list_missing.append(m.group(1))
    if list_missing:
        examples = sorted(set(list_missing))[:10]
        raise AlsValidationError(
            f"{tag} has {len(list_missing)} list members missing Id "
            f"attribute (examples: {examples})"
        )


@dataclass
class RenderJob:
    """A preset rendering job."""

    preset: PresetInfo
    output_path: Path
    midi_notes: List[TestNote]
    tempo: float = 120.0


def create_preset_als(
    preset: PresetInfo,
    notes: List[TestNote],
    tempo: float = 120.0,
) -> bytes:
    """Create a minimal ALS file with preset and MIDI notes.

    Args:
        preset: Preset information
        notes: Test MIDI notes
        tempo: BPM

    Returns:
        Gzipped ALS bytes
    """
    # Calculate clip length
    if notes:
        max_end = max(n.start_beats + n.duration_beats for n in notes)
        clip_end = max(4, ((int(max_end) + 3) // 4) * 4)
    else:
        clip_end = 8

    # Build notes XML
    key_tracks_xml = _build_key_tracks_xml(notes)

    # Build device XML (preset's instrument with embedded .adv parameter tree).
    # The embedded device block already carries its own <LastPresetRef>
    # from the source .adv, so _build_preset_ref_xml() is intentionally not
    # invoked here. It's retained for potential future track-level references.
    config = get_device_config(preset.instrument)
    device_xml = _build_device_xml(preset, config)

    # Structural sanity check — fails-loud if device chain is empty/minimal.
    # Prevents the regression that produced 99 distinct ALS files all
    # rendering to a default Analog patch (see RENDER_PIPELINE_RCA.md).
    _assert_device_nontrivial(device_xml, config)

    # Build track XML
    track_xml = _build_midi_track_xml(
        track_id=3,
        name=xml_escape(preset.name),
        clip_end=clip_end,
        key_tracks_xml=key_tracks_xml,
        device_xml=device_xml,
    )

    # Build full ALS
    als_xml = _build_als_xml(
        tracks=track_xml,
        tempo=tempo,
    )

    return gzip.compress(als_xml.encode('utf-8'))


def _build_key_tracks_xml(notes: List[TestNote]) -> str:
    """Build KeyTracks XML from notes."""
    # Group notes by pitch
    notes_by_pitch: dict = {}
    for note in notes:
        if note.pitch not in notes_by_pitch:
            notes_by_pitch[note.pitch] = []
        notes_by_pitch[note.pitch].append(note)

    key_tracks = []
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

        key_tracks.append(f'''<KeyTrack Id="{kt_id}">
    <MidiKey Value="{pitch}"/>
    <Notes>
        {chr(10).join(notes_xml)}
    </Notes>
</KeyTrack>''')

    return "\n".join(key_tracks)


def _build_preset_ref_xml(preset: PresetInfo) -> str:
    """Build preset file reference XML matching Ableton's format."""
    # Determine relative path based on source
    preset_path = str(preset.path)

    if preset.source == "core":
        # Core Library preset
        # Extract path relative to Core Library
        path_str = preset_path
        if CORE_LIBRARY_PATTERN in path_str:
            idx = path_str.find(CORE_LIBRARY_PATTERN) + len(CORE_LIBRARY_PATTERN) + 1
            relative_path = path_str[idx:]
        else:
            relative_path = f"Devices/Instruments/{preset.instrument}/{preset.category}/{preset.name}.adv"

        return f'''<FilePresetRef Id="0">
					<FileRef>
						<RelativePathType Value="5" />
						<RelativePath Value="{xml_escape(relative_path)}" />
						<Path Value="{xml_escape(preset_path)}" />
						<Type Value="2" />
						<LivePackName Value="Core Library" />
						<LivePackId Value="www.ableton.com/0" />
						<OriginalFileSize Value="0" />
						<OriginalCrc Value="0" />
					</FileRef>
				</FilePresetRef>'''

    elif preset.source == "pack":
        # Factory Pack preset - extract relative path within pack
        path_str = preset_path
        # Try to extract relative path from Factory Packs structure
        pack_pattern = "Factory Packs"
        if pack_pattern in path_str:
            idx = path_str.find(pack_pattern)
            pack_start = path_str[idx:]
            # Find the pack name folder (e.g., "Synth Essentials")
            parts = pack_start.split("/")
            if len(parts) > 2:
                # Relative path within the pack (after pack name)
                relative_path = "/".join(parts[2:])
            else:
                relative_path = ""
        else:
            relative_path = ""

        return f'''<FilePresetRef Id="0">
					<FileRef>
						<RelativePathType Value="5" />
						<RelativePath Value="{xml_escape(relative_path)}" />
						<Path Value="{xml_escape(preset_path)}" />
						<Type Value="2" />
						<LivePackName Value="{xml_escape(preset.pack_name or '')}" />
						<LivePackId Value="" />
						<OriginalFileSize Value="0" />
						<OriginalCrc Value="0" />
					</FileRef>
				</FilePresetRef>'''

    else:
        # User Library preset
        return f'''<FilePresetRef Id="0">
					<FileRef>
						<RelativePathType Value="1" />
						<RelativePath Value="" />
						<Path Value="{xml_escape(preset_path)}" />
						<Type Value="1" />
						<LivePackName Value="" />
						<LivePackId Value="" />
						<OriginalFileSize Value="0" />
						<OriginalCrc Value="0" />
					</FileRef>
				</FilePresetRef>'''


def _build_device_xml(preset: PresetInfo, config: DeviceConfig) -> str:
    """Build the instrument's device XML by embedding the full ``.adv``
    parameter tree of the target preset.

    Previously this function emitted a minimal ``<UltraAnalog>`` containing
    only a ``<LastPresetRef>`` pointer to the ``.adv``. Empirically that
    pointer is **display metadata** in Ableton Live 12, not an auto-load
    directive: Live would render the default patch and ignore the
    referenced preset file. The result was 99 distinct ALS files collapsing
    to 7 unique decoded waveforms (see ``RENDER_PIPELINE_RCA.md``).

    The fix embeds the source ``.adv``'s ``<config.tag_name>`` block
    directly into the ALS so the synthesis state is fully self-contained.

    Args:
        preset: Source preset; ``preset.path`` must point at a ``.adv`` file.
        config: Schema descriptor for ``preset.instrument``.

    Returns:
        ``<config.tag_name>...</config.tag_name>`` XML with the full
        parameter tree.

    Raises:
        AdvEmbedError: if the ``.adv`` cannot be read or parsed.
    """
    return _load_adv_device(preset.path, preset, config)


def _build_midi_track_xml(
    track_id: int,
    name: str,
    clip_end: int,
    key_tracks_xml: str,
    device_xml: str,
) -> str:
    """Build complete MIDI track XML."""
    return f'''<MidiTrack Id="{track_id}">
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
    <Color Value="3"/>
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
                <AutomationTarget Id="100">
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
                <AutomationTarget Id="101">
                    <LockEnvelope Value="0"/>
                </AutomationTarget>
                <ModulationTarget Id="102">
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
                <AutomationTarget Id="103">
                    <LockEnvelope Value="0"/>
                </AutomationTarget>
                <ModulationTarget Id="104">
                    <LockEnvelope Value="0"/>
                </ModulationTarget>
            </Pan>
            <SendsListWrapper LomId="0"/>
            <Speaker>
                <LomId Value="0"/>
                <Manual Value="true"/>
                <AutomationTarget Id="105">
                    <LockEnvelope Value="0"/>
                </AutomationTarget>
            </Speaker>
            <SoloSink Value="false"/>
            <PanMode Value="0"/>
            <Sends/>
            <CrossFadeState>
                <LomId Value="0"/>
                <Manual Value="1"/>
                <AutomationTarget Id="106">
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
                <AutomationTarget Id="107">
                    <LockEnvelope Value="0"/>
                </AutomationTarget>
                <MidiCCOnOffThresholds>
                    <Min Value="64"/>
                    <Max Value="127"/>
                </MidiCCOnOffThresholds>
            </On>
            <ModulationSourceCount Value="0"/>
            <ParametersListWrapper LomId="0"/>
            <Pointee Id="108"/>
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
                            <MidiClip Id="200" Time="0">
                                <LomId Value="0"/>
                                <LomIdView Value="0"/>
                                <CurrentStart Value="0"/>
                                <CurrentEnd Value="{clip_end}"/>
                                <Loop>
                                    <LoopStart Value="0"/>
                                    <LoopEnd Value="{clip_end}"/>
                                    <StartRelative Value="0"/>
                                    <LoopOn Value="false"/>
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
                                        {key_tracks_xml}
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
            <KeepRecordMonitoringLatency Value="true"/>
            <ClipTimeable>
                <ArrangerAutomation>
                    <Events>
                        <MidiClip Id="201" Time="0">
                            <LomId Value="0"/>
                            <LomIdView Value="0"/>
                            <CurrentStart Value="0"/>
                            <CurrentEnd Value="{clip_end}"/>
                            <Loop>
                                <LoopStart Value="0"/>
                                <LoopEnd Value="{clip_end}"/>
                                <StartRelative Value="0"/>
                                <LoopOn Value="false"/>
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
                                    <RemoteableTimeSignature Id="1">
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
                                    {key_tracks_xml}
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
                    </Events>
                    <AutomationTransformViewState>
                        <IsTransformPending Value="false"/>
                        <TimeAndValueTransforms/>
                    </AutomationTransformViewState>
                </ArrangerAutomation>
            </ClipTimeable>
            <VolumeModulationTarget Id="109">
                <LockEnvelope Value="0"/>
            </VolumeModulationTarget>
            <TranspositionModulationTarget Id="110">
                <LockEnvelope Value="0"/>
            </TranspositionModulationTarget>
            <GrainSizeModulationTarget Id="111">
                <LockEnvelope Value="0"/>
            </GrainSizeModulationTarget>
            <FluxModulationTarget Id="112">
                <LockEnvelope Value="0"/>
            </FluxModulationTarget>
            <SampleOffsetModulationTarget Id="113">
                <LockEnvelope Value="0"/>
            </SampleOffsetModulationTarget>
            <PitchViewScrollPosition Value="-1073741824"/>
            <SampleOffsetModulationScrollPosition Value="-1073741824"/>
            <Recorder>
                <IsArmed Value="false"/>
                <TakeCounter Value="1"/>
            </Recorder>
        </MainSequencer>
        <FreezeSequencer>
            <LomId Value="0"/>
            <LomIdView Value="0"/>
            <IsExpanded Value="true"/>
            <On>
                <LomId Value="0"/>
                <Manual Value="true"/>
                <AutomationTarget Id="114">
                    <LockEnvelope Value="0"/>
                </AutomationTarget>
                <MidiCCOnOffThresholds>
                    <Min Value="64"/>
                    <Max Value="127"/>
                </MidiCCOnOffThresholds>
            </On>
            <ModulationSourceCount Value="0"/>
            <ParametersListWrapper LomId="0"/>
            <Pointee Id="115"/>
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
            <VolumeModulationTarget Id="116">
                <LockEnvelope Value="0"/>
            </VolumeModulationTarget>
            <TranspositionModulationTarget Id="117">
                <LockEnvelope Value="0"/>
            </TranspositionModulationTarget>
            <GrainSizeModulationTarget Id="118">
                <LockEnvelope Value="0"/>
            </GrainSizeModulationTarget>
            <FluxModulationTarget Id="119">
                <LockEnvelope Value="0"/>
            </FluxModulationTarget>
            <SampleOffsetModulationTarget Id="120">
                <LockEnvelope Value="0"/>
            </SampleOffsetModulationTarget>
            <PitchViewScrollPosition Value="-1073741824"/>
            <SampleOffsetModulationScrollPosition Value="-1073741824"/>
            <Recorder>
                <IsArmed Value="false"/>
                <TakeCounter Value="1"/>
            </Recorder>
        </FreezeSequencer>
        <DeviceChain>
            <Devices>
                {device_xml}
            </Devices>
            <SignalModulations/>
        </DeviceChain>
    </DeviceChain>
</MidiTrack>'''


def _build_als_xml(tracks: str, tempo: float) -> str:
    """Build complete ALS XML structure."""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12049" Creator="ToneForge Preset Catalog" Revision="">
    <LiveSet>
        <NextPointeeId Value="5000"/>
        <OverwriteProtectionNumber Value="3075"/>
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
            <TrackUnfolded Value="false"/>
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
                    <Volume>
                        <LomId Value="0"/>
                        <Manual Value="1"/>
                        <MidiControllerRange>
                            <Min Value="0.0003162"/>
                            <Max Value="1.99526"/>
                        </MidiControllerRange>
                        <AutomationTarget Id="2">
                            <LockEnvelope Value="0"/>
                        </AutomationTarget>
                        <ModulationTarget Id="3">
                            <LockEnvelope Value="0"/>
                        </ModulationTarget>
                    </Volume>
                    <Tempo>
                        <LomId Value="0"/>
                        <Manual Value="{tempo}"/>
                        <MidiControllerRange>
                            <Min Value="60"/>
                            <Max Value="200"/>
                        </MidiControllerRange>
                        <AutomationTarget Id="4">
                            <LockEnvelope Value="0"/>
                        </AutomationTarget>
                        <ModulationTarget Id="5">
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
                        <Manual Value="1"/>
                        <MidiControllerRange>
                            <Min Value="0"/>
                            <Max Value="1.31"/>
                        </MidiControllerRange>
                        <AutomationTarget Id="6">
                            <LockEnvelope Value="0"/>
                        </AutomationTarget>
                        <ModulationTarget Id="7">
                            <LockEnvelope Value="0"/>
                        </ModulationTarget>
                    </GlobalGrooveAmount>
                    <CrossFade Value="0"/>
                    <CrossFadeState>
                        <LomId Value="0"/>
                        <Manual Value="1"/>
                        <AutomationTarget Id="8">
                            <LockEnvelope Value="0"/>
                        </AutomationTarget>
                    </CrossFadeState>
                    <TempoAutomationViewBottom Value="60"/>
                    <TempoAutomationViewTop Value="200"/>
                    <Pan>
                        <LomId Value="0"/>
                        <Manual Value="0"/>
                        <MidiControllerRange>
                            <Min Value="-1"/>
                            <Max Value="1"/>
                        </MidiControllerRange>
                        <AutomationTarget Id="9">
                            <LockEnvelope Value="0"/>
                        </AutomationTarget>
                        <ModulationTarget Id="10">
                            <LockEnvelope Value="0"/>
                        </ModulationTarget>
                    </Pan>
                    <SendsListWrapper LomId="0"/>
                    <Speaker>
                        <LomId Value="0"/>
                        <Manual Value="true"/>
                        <AutomationTarget Id="11">
                            <LockEnvelope Value="0"/>
                        </AutomationTarget>
                    </Speaker>
                    <SoloSink Value="false"/>
                    <PanMode Value="0"/>
                    <Sends/>
                    <ViewStateSesstionTrackWidth Value="93"/>
                </Mixer>
                <PreHearVolume>
                    <LomId Value="0"/>
                    <Manual Value="1"/>
                    <MidiControllerRange>
                        <Min Value="0.0003162"/>
                        <Max Value="1.99526"/>
                    </MidiControllerRange>
                    <AutomationTarget Id="12">
                        <LockEnvelope Value="0"/>
                    </AutomationTarget>
                    <ModulationTarget Id="13">
                        <LockEnvelope Value="0"/>
                    </ModulationTarget>
                </PreHearVolume>
                <DeviceChain>
                    <Devices/>
                    <SignalModulations/>
                </DeviceChain>
            </DeviceChain>
        </MasterTrack>
        <PreHearTrack>
            <LomId Value="0"/>
            <LomIdView Value="0"/>
            <IsContentSelectedInDocument Value="false"/>
            <PreferredContentViewMode Value="0"/>
            <TrackDelay>
                <Value Value="0"/>
                <IsValueSampleBased Value="false"/>
            </TrackDelay>
            <Name>
                <EffectiveName Value="Preview"/>
                <UserName Value=""/>
                <Annotation Value=""/>
                <MemorizedFirstClipName Value=""/>
            </Name>
            <Color Value="-1"/>
            <DeviceChain>
                <AutomationLanes>
                    <AutomationLanes/>
                    <AreAdditionalAutomationLanesFolded Value="false"/>
                </AutomationLanes>
                <Mixer>
                    <LomId Value="0"/>
                    <LomIdView Value="0"/>
                    <IsExpanded Value="true"/>
                    <On>
                        <LomId Value="0"/>
                        <Manual Value="true"/>
                        <AutomationTarget Id="14">
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
                        <AutomationTarget Id="15">
                            <LockEnvelope Value="0"/>
                        </AutomationTarget>
                        <ModulationTarget Id="16">
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
                        <AutomationTarget Id="17">
                            <LockEnvelope Value="0"/>
                        </AutomationTarget>
                        <ModulationTarget Id="18">
                            <LockEnvelope Value="0"/>
                        </ModulationTarget>
                    </Pan>
                    <SendsListWrapper LomId="0"/>
                    <Speaker>
                        <LomId Value="0"/>
                        <Manual Value="true"/>
                        <AutomationTarget Id="19">
                            <LockEnvelope Value="0"/>
                        </AutomationTarget>
                    </Speaker>
                    <SoloSink Value="false"/>
                    <PanMode Value="0"/>
                    <Sends/>
                    <ViewStateSesstionTrackWidth Value="93"/>
                </Mixer>
                <DeviceChain>
                    <Devices/>
                    <SignalModulations/>
                </DeviceChain>
            </DeviceChain>
        </PreHearTrack>
        <SendsPre/>
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
            <PhaseNudgeTempo Value="{tempo}"/>
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
        <GlobalQuantisation Value="5"/>
        <AutoQuantisation Value="0"/>
        <Grid>
            <FixedNumerator Value="1"/>
            <FixedDenominator Value="16"/>
            <GridIntervalPixel Value="20"/>
            <Ntoles Value="2"/>
            <SnapToGrid Value="true"/>
            <Fixed Value="false"/>
        </Grid>
        <ScaleInformation>
            <RootNote Value="0"/>
            <Name Value="Major"/>
        </ScaleInformation>
        <InKey Value="false"/>
        <SmpteFormat Value="0"/>
        <TimeSelection>
            <AnchorTime Value="0"/>
            <OtherTime Value="0"/>
        </TimeSelection>
        <SequencerNavigator>
            <BeatTimeHelper>
                <CurrentZoom Value="0.5"/>
            </BeatTimeHelper>
            <TimeOrigin Value="0"/>
            <HasDetailClip Value="false"/>
        </SequencerNavigator>
        <IsContentSelectedInDocument Value="false"/>
        <PreferredContentViewMode Value="0"/>
        <ViewStateArrangerHasDetail Value="false"/>
        <ViewStateSessionHasDetail Value="false"/>
        <ViewStateDetailIsSample Value="false"/>
        <ViewStates>
            <SessionIO Value="0"/>
            <ArrangerIO Value="0"/>
            <BrowserModeEnabled Value="true"/>
            <ControlSurfaceMode Value="false"/>
            <MaximumScreenPercentage Value="1"/>
            <ShowStatusBar Value="true"/>
        </ViewStates>
        <Locators>
            <Locators/>
        </Locators>
        <DetailClipKeyMidi>
            <RootNote Value="36"/>
            <KeyMidi Value="-1"/>
        </DetailClipKeyMidi>
        <TracksListWrapper LomId="0"/>
        <VisibleTracksListWrapper LomId="0"/>
        <ReturnTracksListWrapper LomId="0"/>
        <ScenesListWrapper LomId="0"/>
        <CuePointsListWrapper LomId="0"/>
        <ChooserBar>
            <Value Value="2"/>
        </ChooserBar>
        <Annotation Value=""/>
        <SourceContext>
            <Value/>
        </SourceContext>
        <SoloOrPflSavedValue Value="true"/>
        <SoloInPlace Value="false"/>
        <CrossfadeCurve Value="2"/>
        <LatencyCompensation Value="0"/>
        <HighlightedTrackIndex Value="0"/>
        <Groove>
            <GroovePool>
                <Grooves/>
            </GroovePool>
        </Groove>
        <AutoColorPickerForPlayerAndGroupTracks>
            <NextColorIndex Value="18"/>
        </AutoColorPickerForPlayerAndGroupTracks>
        <AutoColorPickerForReturnAndMasterTracks>
            <NextColorIndex Value="3"/>
        </AutoColorPickerForReturnAndMasterTracks>
        <ViewData Value="{{}}"/>
        <MidiFoldIn Value="false"/>
        <MidiFoldOut Value="false"/>
        <MidiPrelisten Value="true"/>
        <UseManualRead Value="false"/>
        <UseManualWrite Value="false"/>
        <AccidentalSpellingPreference Value="3"/>
        <PreferFlatRootNote Value="false"/>
        <UseMultiSampleStreaming Value="true"/>
        <EnableTrackOutputsOnArm Value="true"/>
        <MainTrackEnabled Value="false"/>
        <MainTrackColor Value="-1"/>
        <MainTrack>
            <LomId Value="0"/>
        </MainTrack>
    </LiveSet>
</Ableton>'''


def generate_render_jobs(
    presets: List[PresetInfo],
    output_dir: Path,
    tempo: float = 120.0,
) -> List[RenderJob]:
    """Generate render jobs for a list of presets.

    Args:
        presets: List of presets to render
        output_dir: Directory for output files
        tempo: BPM for rendering

    Returns:
        List of RenderJob objects
    """
    jobs = []

    for preset in presets:
        # Get appropriate test sequence for this preset type
        notes, _ = get_test_sequence_for_type(preset.sound_type, tempo)

        # Generate output path
        safe_name = safe_filename(preset.preset_id)
        output_path = output_dir / f"{safe_name}.wav"

        jobs.append(RenderJob(
            preset=preset,
            output_path=output_path,
            midi_notes=notes,
            tempo=tempo,
        ))

    return jobs


def create_als_for_job(job: RenderJob, als_dir: Path) -> Path:
    """Create ALS file for a render job.

    Args:
        job: The render job
        als_dir: Directory to save ALS file

    Returns:
        Path to created ALS file
    """
    als_bytes = create_preset_als(
        preset=job.preset,
        notes=job.midi_notes,
        tempo=job.tempo,
    )

    safe_name = safe_filename(job.preset.preset_id)
    als_path = als_dir / f"{safe_name}.als"
    als_path.write_bytes(als_bytes)

    return als_path
