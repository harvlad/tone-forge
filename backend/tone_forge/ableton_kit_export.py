"""
Ableton Drum Rack export of a song's Auto Kit ("Export for Ableton").

Turns the Performance-Intelligence kit (``performance.serve.kit_payload``)
into a self-contained Live Pack folder, delivered as one zip:

    {Song} Jam Kit/
        {Song} Jam Kit.adg     — Drum Rack, one chain per kit pad
        {Song} Jam Kit.sfz     — fallback mapping (any SFZ sampler)
        Samples/*.wav          — the rendered stem slices
        kit.json               — the kit manifest (provenance + metadata)
        README.txt             — pad map + branding

The .adg is generated XML (gzipped), targeting Live 11+. Chains carry the
kit's category colors and descriptive names so the rack reads like the
in-app launchpad. A Drum Rack cannot carry a custom plugin UI — that
requires a Max for Live device or a real VST — so "premium" here means:
colored, named, organized, and documented.

Stem audio: prefers server-local stem files (analysis box / dev). On a
serving box where stems live in R2, the caller must have refreshed the
presigned URLs (``_refresh_r2_stem_urls``); slices are then fetched to a
temp file per stem before cutting.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger(__name__)

# Rendered slice format: 16-bit PCM at the stem's native rate.
_WAV_SUBTYPE = "PCM_16"

# Drum Rack pad MIDI notes: C1 = 36 upward, bottom-left pad first —
# the standard 16-pad drum layout every controller expects.
_FIRST_PAD_NOTE = 36

# Ableton stores a DrumBranch's ReceivingNote INVERTED relative to the
# displayed pad note (stored = 128 - displayed); C1/36 persists as 92.
def _receiving_note(midi_note: int) -> int:
    return 128 - midi_note


# Kit category hex -> nearest Ableton chain-color palette index.
# Live's color picker indexes 0..69; these are the closest first-row hues.
_CATEGORY_ABLETON_COLOR = {
    "DRUMS": 6,     # red
    "BASS": 12,     # green
    "CHORDS": 2,    # amber
    "LEAD": 1,      # orange
    "VOCAL": 14,    # pink/magenta
    "RHYTHM": 18,   # blue
    "TEXTURE": 16,  # cyan
    "FX": 11,       # purple
    "STAB": 49,     # violet
}
_DEFAULT_CHAIN_COLOR = 26  # gray


def _safe_name(name: str, limit: int = 48) -> str:
    cleaned = "".join(c if c.isalnum() or c in " -_()" else "_" for c in name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit] or "Song"


def _sample_filename(idx: int, pad: Dict) -> str:
    label = _safe_name(str(pad.get("name") or f"Pad {idx + 1}"), limit=32)
    return f"{idx + 1:02d} {label}.wav"


# ---------------------------------------------------------------------------
# Stem audio access
# ---------------------------------------------------------------------------

def _materialize_stems(
    result: Dict, roles: List[str], scratch: Path
) -> Dict[str, Path]:
    """Local file per needed stem role — shared logic in stem_fetch."""
    from tone_forge.stem_fetch import materialize_stems
    return materialize_stems(result, scratch, roles=roles)


def _render_slice(
    stem_path: Path, start_sec: float, end_sec: float, dest: Path
) -> Optional[Tuple[int, int]]:
    """Cut [start_sec, end_sec] from the stem into a PCM_16 WAV.
    Returns (frames, sample_rate) or None on failure."""
    try:
        import soundfile as sf
    except ImportError:
        logger.error("[ableton-kit] soundfile not available")
        return None
    try:
        with sf.SoundFile(str(stem_path)) as f:
            sr = f.samplerate
            start = max(0, int(start_sec * sr))
            stop = min(len(f), int(end_sec * sr))
            if stop <= start:
                return None
            f.seek(start)
            data = f.read(stop - start)
        sf.write(str(dest), data, sr, subtype=_WAV_SUBTYPE)
        return (stop - start, sr)
    except Exception as exc:
        logger.warning("[ableton-kit] slice failed (%s): %s", stem_path.name, exc)
        return None


# ---------------------------------------------------------------------------
# Drum Rack (.adg) XML
# ---------------------------------------------------------------------------

def _simpler_xml(
    device_id: int,
    sample_rel_path: str,
    pad_name: str,
    frames: int,
    sample_rate: int,
    file_size: int,
    loop: bool,
) -> str:
    """A minimal OriginalSimpler preset playing one sample.
    Live fills unspecified parameters with defaults on load."""
    loop_mode = 1 if loop else 0
    name = _xml_escape(pad_name, {'"': "&quot;"})
    rel = _xml_escape(sample_rel_path, {'"': "&quot;"})
    return f"""<AbletonDevicePreset Id="{device_id}">
<Device>
<OriginalSimpler Id="{device_id}">
<LomId Value="0"/>
<IsExpanded Value="true"/>
<On><LomId Value="0"/><Manual Value="true"/></On>
<Player>
<MultiSampleMap>
<SampleParts>
<MultiSamplePart Id="0" HasImportedSlicePoints="false" NeedsAnalysisData="true">
<LomId Value="0"/>
<Name Value="{name}"/>
<Selection Value="true"/>
<IsActive Value="true"/>
<Solo Value="false"/>
<KeyRange><Min Value="0"/><Max Value="127"/><CrossfadeMin Value="0"/><CrossfadeMax Value="127"/></KeyRange>
<VelocityRange><Min Value="1"/><Max Value="127"/><CrossfadeMin Value="1"/><CrossfadeMax Value="127"/></VelocityRange>
<SelectorRange><Min Value="0"/><Max Value="127"/><CrossfadeMin Value="0"/><CrossfadeMax Value="127"/></SelectorRange>
<RootKey Value="60"/>
<Detune Value="0"/>
<TuneScale Value="100"/>
<Panorama Value="0"/>
<Volume Value="1"/>
<Link Value="false"/>
<SampleStart Value="0"/>
<SampleEnd Value="{frames}"/>
<SustainLoop><Start Value="0"/><End Value="{frames}"/><Mode Value="{loop_mode}"/><Crossfade Value="0"/><Detune Value="0"/></SustainLoop>
<ReleaseLoop><Start Value="0"/><End Value="{frames}"/><Mode Value="3"/><Crossfade Value="0"/><Detune Value="0"/></ReleaseLoop>
<SampleRef>
<FileRef>
<RelativePathType Value="3"/>
<RelativePath Value="{rel}"/>
<Path Value=""/>
<Type Value="1"/>
<LivePackName Value=""/>
<LivePackId Value=""/>
<OriginalFileSize Value="{file_size}"/>
<OriginalCrc Value="0"/>
</FileRef>
<LastModDate Value="0"/>
<SourceContext/>
<SampleUsageHint Value="0"/>
<DefaultDuration Value="{frames}"/>
<DefaultSampleRate Value="{sample_rate}"/>
</SampleRef>
</MultiSamplePart>
</SampleParts>
<LoadInRam Value="false"/>
</MultiSampleMap>
</Player>
<VolumeAndPan><Volume><Manual Value="-6"/></Volume><Panorama><Manual Value="0"/></Panorama></VolumeAndPan>
</OriginalSimpler>
</Device>
<PresetRef/>
<BranchDeviceId Value="device:ableton:simpler:"/>
</AbletonDevicePreset>"""


def _branch_xml(
    branch_id: int,
    pad_name: str,
    color: int,
    midi_note: int,
    simpler: str,
) -> str:
    name = _xml_escape(pad_name, {'"': "&quot;"})
    return f"""<DrumBranchPreset Id="{branch_id}">
<Name Value="{name}"/>
<IsSoloed Value="false"/>
<DevicePresets>
{simpler}
</DevicePresets>
<MixerPreset>
<AudioBranchMixerDevice Id="{branch_id}">
<LomId Value="0"/>
<On><LomId Value="0"/><Manual Value="true"/></On>
<Speaker><LomId Value="0"/><Manual Value="true"/></Speaker>
<Volume><LomId Value="0"/><Manual Value="1"/></Volume>
<Panorama><LomId Value="0"/><Manual Value="0"/></Panorama>
<SendInfos/>
</AudioBranchMixerDevice>
</MixerPreset>
<IsExpanded Value="true"/>
<Color Value="{color}"/>
<ZoneSettings>
<ReceivingNote Value="{_receiving_note(midi_note)}"/>
<SendingNote Value="60"/>
<ChokeGroup Value="0"/>
</ZoneSettings>
</DrumBranchPreset>"""


def build_drum_rack_adg(
    rack_name: str,
    pads: List[Dict],
    annotation: str = "",
) -> bytes:
    """Gzipped Drum Rack preset. ``pads`` entries: name, category,
    sample_rel_path, frames, sample_rate, file_size, loop, midi_note."""
    branches = []
    for i, p in enumerate(pads):
        simpler = _simpler_xml(
            device_id=1000 + i,
            sample_rel_path=p["sample_rel_path"],
            pad_name=p["name"],
            frames=p["frames"],
            sample_rate=p["sample_rate"],
            file_size=p["file_size"],
            loop=bool(p.get("loop", True)),
        )
        color = _CATEGORY_ABLETON_COLOR.get(p.get("category") or "", _DEFAULT_CHAIN_COLOR)
        branches.append(_branch_xml(
            branch_id=i,
            pad_name=p["name"],
            color=color,
            midi_note=p["midi_note"],
            simpler=simpler,
        ))
    name = _xml_escape(rack_name, {'"': "&quot;"})
    note = _xml_escape(annotation, {'"': "&quot;"})
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="11.0_11202" SchemaChangeCount="3" Creator="Tone Forge" Revision="0">
<GroupDevicePreset>
<OverwriteProtectionNumber Value="2819"/>
<Name Value="{name}"/>
<Annotation Value="{note}"/>
<Device>
<DrumGroupDevice Id="1">
<LomId Value="0"/>
<IsExpanded Value="true"/>
<On><LomId Value="0"/><Manual Value="true"/></On>
<UserName Value="{name}"/>
<Annotation Value="{note}"/>
<BranchesListWrapper LomId="0"/>
<ReturnBranchesListWrapper LomId="0"/>
</DrumGroupDevice>
</Device>
<BranchPresets>
{chr(10).join(branches)}
</BranchPresets>
<ReturnBranchPresets/>
</GroupDevicePreset>
</Ableton>
"""
    return gzip.compress(xml.encode("utf-8"))


# ---------------------------------------------------------------------------
# SFZ fallback
# ---------------------------------------------------------------------------

def build_sfz(pads: List[Dict]) -> str:
    """Universal fallback mapping — loads in any SFZ player if the .adg
    ever trips on an Ableton schema change."""
    lines = ["// Tone Forge Jam Kit — SFZ fallback", "<control>", "default_path=Samples/", ""]
    for p in pads:
        lines.append(f"// {p['name']} [{p.get('category') or 'SAMPLE'}]")
        lines.append("<region>")
        lines.append(f"sample={Path(p['sample_rel_path']).name}")
        lines.append(f"key={p['midi_note']}")
        if p.get("loop", True):
            lines.append("loop_mode=loop_continuous")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pack assembly
# ---------------------------------------------------------------------------

def build_ableton_kit_zip(
    entry_id: str,
    result: Dict,
    *,
    song_name: str,
    skill: str = "intermediate",
    pads: int = 16,
) -> Tuple[bytes, str]:
    """Render the song's Auto Kit as an Ableton Live Pack zip.

    Raises ValueError when no pad could be rendered (no kit, or no
    reachable stem audio).
    """
    from tone_forge.performance import serve as _perf

    kit = _perf.kit_payload(entry_id, result, skill=skill, pads=pads)
    kit_pads = kit.get("pads") or []
    if not kit_pads:
        raise ValueError("Song has no performance kit (no ranked assets)")

    pack_title = f"{_safe_name(song_name)} Jam Kit"
    tempo = result.get("tempo_bpm") or result.get("tempo")

    with tempfile.TemporaryDirectory(prefix="toneforge_adg_") as scratch_dir:
        scratch = Path(scratch_dir)
        roles = sorted({
            (p.get("stemSlice") or {}).get("stemRole")
            for p in kit_pads
            if (p.get("stemSlice") or {}).get("stemRole")
        })
        stems = _materialize_stems(result, roles, scratch)

        rendered: List[Dict] = []
        wav_bytes: Dict[str, bytes] = {}
        for i, pad in enumerate(kit_pads):
            slice_ = pad.get("stemSlice") or {}
            role = slice_.get("stemRole")
            stem = stems.get(role)
            if stem is None:
                continue
            start = pad.get("loopStartSec", slice_.get("startSec"))
            end = pad.get("loopEndSec", slice_.get("endSec"))
            if start is None or end is None:
                continue
            fname = _sample_filename(len(rendered), pad)
            dest = scratch / fname
            meta = _render_slice(stem, float(start), float(end), dest)
            if meta is None:
                continue
            frames, sr = meta
            data = dest.read_bytes()
            wav_bytes[fname] = data
            rendered.append({
                "name": str(pad.get("name") or f"Pad {i + 1}"),
                "category": pad.get("category"),
                "sample_rel_path": f"Samples/{fname}",
                "frames": frames,
                "sample_rate": sr,
                "file_size": len(data),
                "loop": bool(pad.get("loopable", True)),
                "midi_note": _FIRST_PAD_NOTE + len(rendered),
            })

        if not rendered:
            raise ValueError("No kit pads could be rendered (stems unreachable)")

        annotation = f"Tone Forge Jam Kit — {song_name}" + (
            f" · {round(float(tempo))} BPM" if tempo else "")
        adg = build_drum_rack_adg(pack_title, rendered, annotation=annotation)
        sfz = build_sfz(rendered)
        readme = _readme(pack_title, song_name, tempo, rendered)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            root = pack_title
            z.writestr(f"{root}/{pack_title}.adg", adg)
            z.writestr(f"{root}/{pack_title}.sfz", sfz)
            z.writestr(f"{root}/README.txt", readme)
            z.writestr(
                f"{root}/kit.json",
                json.dumps({"source": "tone-forge", "entryId": entry_id,
                            "skill": skill, "kit": kit}, indent=2),
            )
            for fname, data in wav_bytes.items():
                # WAVs are already compressed poorly; store as-is is fine,
                # deflate still shaves silence.
                z.writestr(f"{root}/Samples/{fname}", data)
        return buf.getvalue(), f"{pack_title}.zip"


def _readme(pack_title: str, song_name: str, tempo, pads: List[Dict]) -> str:
    lines = [
        "=" * 60,
        f"  {pack_title}",
        "  Generated by Tone Forge — jamn.app",
        "=" * 60,
        "",
        f"Song: {song_name}" + (f"  ·  {round(float(tempo))} BPM" if tempo else ""),
        "",
        "HOW TO USE",
        f"  1. Keep this folder together ({pack_title}.adg + Samples/).",
        f"  2. Drag {pack_title}.adg onto a MIDI track in Ableton Live 11+.",
        "  3. Play pads from C1 upward (any pad controller / Push).",
        "  4. Every pad is a bar-friendly loop cut from the song's own",
        "     stems — set clip/global quantize to 1 bar and layer away.",
        "",
        f"  Fallback: {pack_title}.sfz loads the same kit in any SFZ",
        "  sampler (Sforzando, DecentSampler w/ converter, etc).",
        "",
        "PAD MAP",
    ]
    for p in pads:
        note = p["midi_note"]
        octave = note // 12 - 2
        names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        lines.append(
            f"  {names[note % 12]}{octave:<3} {p['name']}"
            f"  [{p.get('category') or 'SAMPLE'}]"
        )
    lines += ["", "Colors match the Tone Forge launchpad: drums red, bass",
              "green, chords amber, lead orange, vocals pink.", ""]
    return "\n".join(lines)
