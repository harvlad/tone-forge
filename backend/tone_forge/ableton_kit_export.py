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
# Drum Rack (.adg) XML — template-based
# ---------------------------------------------------------------------------
#
# Live rejects hand-approximated preset XML ("The preset cannot be
# loaded.", verified in Live 12.0.5). The generator therefore patches a
# REAL Live 12 Drum Rack skeleton (the factory Slicing default, cleaned
# of authoring-machine paths — committed as assets/
# ableton_drum_rack_template.xml.gz): one DrumBranchPreset with an
# OriginalSimpler and an empty SampleParts, cloned per pad with a
# MultiSamplePart (schema copied from a factory kit) inserted.
#
# Sample refs use RelativePathType 6 = relative to the USER LIBRARY
# (verified in Live 12: type 3 leaves "Media files are missing", type 6
# resolves) — so the pack folder must be installed under
# `User Library/JamKits/`, which is exactly what the README says.

_TEMPLATE_PATH = Path(__file__).parent / "assets" / "ableton_drum_rack_template.xml.gz"

_MULTISAMPLE_PART = """<MultiSamplePart Id="0" HasImportedSlicePoints="false" NeedsAnalysisData="true">
<LomId Value="0" />
<Name Value="{name}" />
<Selection Value="true" />
<IsActive Value="true" />
<Solo Value="false" />
<KeyRange><Min Value="0" /><Max Value="127" /><CrossfadeMin Value="0" /><CrossfadeMax Value="127" /></KeyRange>
<VelocityRange><Min Value="1" /><Max Value="127" /><CrossfadeMin Value="1" /><CrossfadeMax Value="127" /></VelocityRange>
<SelectorRange><Min Value="0" /><Max Value="127" /><CrossfadeMin Value="0" /><CrossfadeMax Value="127" /></SelectorRange>
<RootKey Value="60" />
<Detune Value="0" />
<TuneScale Value="100" />
<Panorama Value="0" />
<Volume Value="1" />
<Link Value="false" />
<SampleStart Value="0" />
<SampleEnd Value="{frames}" />
<SustainLoop><Start Value="0" /><End Value="{frames}" /><Mode Value="{loop_mode}" /><Crossfade Value="0" /><Detune Value="0" /></SustainLoop>
<ReleaseLoop><Start Value="0" /><End Value="{frames}" /><Mode Value="3" /><Crossfade Value="0" /><Detune Value="0" /></ReleaseLoop>
<SampleRef>
<FileRef>
<RelativePathType Value="6" />
<RelativePath Value="{rel_path}" />
<Path Value="" />
<Type Value="2" />
<LivePackName Value="" />
<LivePackId Value="" />
<OriginalFileSize Value="{file_size}" />
<OriginalCrc Value="0" />
</FileRef>
<LastModDate Value="0" />
<SourceContext />
<SampleUsageHint Value="0" />
<DefaultDuration Value="{frames}" />
<DefaultSampleRate Value="{sample_rate}" />
</SampleRef>
<SlicingThreshold Value="100" />
<SlicingBeatGrid Value="4" />
<SlicingRegions Value="8" />
<SlicingStyle Value="0" />
<SampleWarpProperties>
<WarpMarkers>
<WarpMarker Id="0" SecTime="0" BeatTime="0" />
<WarpMarker Id="1" SecTime="0.015625" BeatTime="0.03125" />
</WarpMarkers>
<WarpMode Value="0" />
<GranularityTones Value="30" />
<GranularityTexture Value="65" />
<FluctuationTexture Value="25" />
<ComplexProFormants Value="100" />
<ComplexProEnvelope Value="128" />
<TransientResolution Value="6" />
<TransientLoopMode Value="2" />
<TransientEnvelope Value="100" />
<IsWarped Value="false" />
<Onsets><UserOnsets /><HasUserOnsets Value="false" /></Onsets>
<TimeSignature><TimeSignatures><RemoteableTimeSignature Id="0"><Numerator Value="4" /><Denominator Value="4" /><Time Value="0" /></RemoteableTimeSignature></TimeSignatures></TimeSignature>
<BeatGrid><FixedNumerator Value="1" /><FixedDenominator Value="16" /><GridIntervalPixel Value="20" /><Ntoles Value="2" /><SnapToGrid Value="true" /><Fixed Value="false" /></BeatGrid>
</SampleWarpProperties>
<InitialSlicePointsFromOnsets />
<SlicePoints />
<ManualSlicePoints />
<BeatSlicePoints />
<RegionSlicePoints />
<UseDynamicBeatSlices Value="true" />
<UseDynamicRegionSlices Value="true" />
<AreSlicesFromOnsetsEditable Value="true" />
</MultiSamplePart>"""


def _build_sample_part(
    pad_name: str,
    sample_rel_path: str,
    frames: int,
    sample_rate: int,
    file_size: int,
    loop: bool,
):
    import xml.etree.ElementTree as ET

    xml = _MULTISAMPLE_PART.format(
        name=_xml_escape(pad_name, {'"': "&quot;"}),
        rel_path=_xml_escape(sample_rel_path, {'"': "&quot;"}),
        frames=frames,
        loop_mode=1 if loop else 0,
        file_size=file_size,
        sample_rate=sample_rate,
    )
    return ET.fromstring(xml)


def build_drum_rack_adg(
    rack_name: str,
    pads: List[Dict],
    annotation: str = "",
) -> bytes:
    """Gzipped Drum Rack preset from the factory-derived template.
    ``pads`` entries: name, category, sample_rel_path, frames,
    sample_rate, file_size, loop, midi_note."""
    import copy
    import xml.etree.ElementTree as ET

    xml = gzip.decompress(_TEMPLATE_PATH.read_bytes()).decode("utf-8")
    root = ET.fromstring(xml)
    gdp = root.find("GroupDevicePreset")
    container = gdp.find("BranchPresets")
    proto = container.find("DrumBranchPreset")
    if proto is None:
        raise RuntimeError("drum rack template has no branch prototype")
    for child in list(container):
        container.remove(child)

    # Rack identity: name the device so the title bar reads the song.
    dgd = gdp.find("Device/DrumGroupDevice")
    if dgd is not None:
        user_name = dgd.find("UserName")
        if user_name is not None:
            user_name.set("Value", rack_name)
        note = dgd.find("Annotation")
        if note is not None:
            note.set("Value", annotation)

    next_id = [20000]  # well clear of the template's own Ids

    for i, p in enumerate(pads):
        b = copy.deepcopy(proto)
        name = str(p["name"])
        b.find("Name").set("Value", name)

        zone = b.find("ZoneSettings")
        zone.find("ReceivingNote").set("Value", str(_receiving_note(p["midi_note"])))
        zone.find("ChokeGroup").set("Value", "0")

        # Chain color = kit category (AutoColored off so ours sticks).
        color_el = b.find("DocumentColorIndex")
        if color_el is not None:
            color_el.set("Value", str(
                _CATEGORY_ABLETON_COLOR.get(p.get("category") or "", _DEFAULT_CHAIN_COLOR)))
        auto_col = b.find("AutoColored")
        if auto_col is not None:
            auto_col.set("Value", "false")

        parts = b.find(".//SampleParts")
        if parts is None:
            raise RuntimeError("branch prototype has no SampleParts")
        parts.append(_build_sample_part(
            pad_name=name,
            sample_rel_path=p["sample_rel_path"],
            frames=p["frames"],
            sample_rate=p["sample_rate"],
            file_size=p["file_size"],
            loop=bool(p.get("loop", True)),
        ))

        # Unique Ids per branch — duplicate Ids across chains make Live
        # refuse the preset (same strategy as als_template's rack embed).
        for el in b.iter():
            if el.get("Id") is not None:
                el.set("Id", str(next_id[0]))
                next_id[0] += 1

        container.append(b)

    out = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode")
    return gzip.compress(out.encode("utf-8"))


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
    import hashlib
    import os

    from tone_forge.performance import serve as _perf

    kit = _perf.kit_payload(entry_id, result, skill=skill, pads=pads)
    kit_pads = kit.get("pads") or []
    if not kit_pads:
        raise ValueError("Song has no performance kit (no ranked assets)")

    # Rendered-zip cache: the kit provenance embeds the graph hash, so
    # (entry, skill, pads, provenance) uniquely names an output. Repeat
    # downloads (plugin Browse, re-export) return in milliseconds
    # instead of re-fetching stems + re-slicing. TONEFORGE_KIT_CACHE=0
    # disables (tests point it at a tmp dir).
    cache_dir_raw = os.environ.get("TONEFORGE_KIT_CACHE")
    cache_dir = None
    if cache_dir_raw != "0":
        cache_dir = Path(cache_dir_raw) if cache_dir_raw else (
            Path.home() / ".toneforge" / "kit_zip_cache")
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            cache_dir = None
    cache_key = hashlib.sha1(
        f"{entry_id}|{skill}|{pads}|{kit.get('provenance', '')}|{song_name}"
        .encode()).hexdigest()
    cache_file = cache_dir / f"{cache_key}.zip" if cache_dir else None
    filename_default = f"{_safe_name(song_name)} Jam Kit.zip"
    if cache_file is not None and cache_file.exists() \
            and cache_file.stat().st_size > 0:
        return cache_file.read_bytes(), filename_default

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
                # User-Library-relative (RelativePathType 6): resolves
                # when the pack folder sits in User Library/JamKits/.
                "sample_rel_path": f"JamKits/{pack_title}/Samples/{fname}",
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
            # `samples` = the RENDERED pad list in MIDI order — consumers
            # (the jamn Kit plugin) must use this, not filename guessing:
            # kit pads whose slice failed to render are absent here while
            # still present in `kit.pads`.
            z.writestr(
                f"{root}/kit.json",
                json.dumps({
                    "source": "tone-forge",
                    "entryId": entry_id,
                    "skill": skill,
                    "songName": song_name,
                    "tempoBpm": float(tempo) if tempo else None,
                    "samples": [
                        {
                            "file": f"Samples/{Path(p['sample_rel_path']).name}",
                            "name": p["name"],
                            "category": p.get("category"),
                            "midiNote": p["midi_note"],
                            "loopable": bool(p.get("loop", True)),
                            "sampleRate": p["sample_rate"],
                            "frames": p["frames"],
                        }
                        for p in rendered
                    ],
                    "kit": kit,
                }, indent=2),
            )
            for fname, data in wav_bytes.items():
                # WAVs are already compressed poorly; store as-is is fine,
                # deflate still shaves silence.
                z.writestr(f"{root}/Samples/{fname}", data)
        payload = buf.getvalue()
        if cache_file is not None:
            try:
                tmp = cache_file.with_suffix(".part")
                tmp.write_bytes(payload)
                tmp.rename(cache_file)
            except Exception:
                pass
        return payload, f"{pack_title}.zip"


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
        "  1. Drop this whole folder into your Ableton User Library,",
        "     inside a folder named JamKits:",
        f"       User Library/JamKits/{pack_title}/",
        "     (Live > Settings > Library shows where your User Library is.)",
        f"  2. In Live's browser (User Library), load {pack_title}.adg",
        "     onto a MIDI track.",
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
