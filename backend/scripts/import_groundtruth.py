"""Ground-truth notation importer.

Converts human-authored notation into the corpus fixture JSON that
``bench.corpus`` consumes, so curator-provided transcriptions feed
the evaluation corpus without hand-computing timestamps.

Supported input formats
-----------------------

1. **Chord chart** (``.chart`` / ``.txt``) — bar-grid text format,
   the fastest to author. Timestamps are derived from BPM + time
   signature + a start offset. See ``CHART_FORMAT_HELP`` below.
2. **MIREX .lab** (``.lab``) — ``start end label`` per line, already
   timestamped. ``--sections-lab`` accepts a second .lab with section
   labels.
3. **MIDI** (``.mid`` / ``.midi``) — note-level ground truth via
   pretty_midi (optional dep). Markers become sections when present.
4. **Guitar Pro** (``.gp3/.gp4/.gp5/.gpx/.gp``) — via PyGuitarPro
   (optional dep). Yields tempo map, section markers, and note-level
   ground truth; chord regions when the tab carries chord diagrams.

Output fixture JSON is schema v2 plus additive fields:

    "sections": [{"start": s, "end": e, "label": "Chorus"}, ...]
    "key": "A major"
    "notes": [{"pitch": 57, "start": s, "end": e, "velocity": v}, ...]
    "source_format": "chart" | "lab" | "midi" | "guitarpro"

``regions`` (chords) stays the schema-v1 required shape so every
existing loader/validator keeps working. ``regression_floor_*`` is
written as 0.0 — pin it with ``python -m bench.corpus add
--measure-floor`` after attaching audio.

Usage
-----

    python -m scripts.import_groundtruth chart mysong.chart \
        [--out fixtures_dir/mysong.json] [--split train]
    python -m scripts.import_groundtruth lab chords.lab \
        --duration 213.4 [--sections-lab sections.lab]
    python -m scripts.import_groundtruth midi mysong.mid
    python -m scripts.import_groundtruth gp mysong.gp5

Then attach audio + pin floor:

    python -m bench.corpus add --json mysong.json \
        --other other.wav --bass bass.wav --measure-floor
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional, Tuple


CHART_FORMAT_HELP = """\
Chord chart format (bar grid):

    song: Pub Feed
    artist: The Chats
    bpm: 97
    time: 4/4
    offset: 0.34        # seconds into the audio where bar 1 starts
    key: A major
    duration: 147.06    # optional; defaults to end of last bar

    [Intro] x2          # section; x2 repeats the whole block
    A5*4                # SYMBOL*N = N bars; bare SYMBOL = 1 bar

    [Chorus 1]
    B5 A5 E5 A5         # 4 bars, one chord each

    [Bridge] bpm=92     # per-section tempo override
    F#5*2 E5*2 D5*2 A5

Tokens: `.` repeats the previous chord for 1 bar; `N.C.` marks a
no-chord bar (gap — excluded from chord regions); `|` bar-line
characters are ignored; `#` starts a comment. Fractional bars
allowed: `A5*1.5`.
"""


# ---------------------------------------------------------------------------
# Shared fixture assembly
# ---------------------------------------------------------------------------


@dataclass
class ParsedSong:
    """Format-independent intermediate the emitters consume."""

    song: str = ""
    artist: str = ""
    tempo_bpm: Optional[float] = None
    time_signature: str = "4/4"
    offset_s: float = 0.0
    key: Optional[str] = None
    duration_s: float = 0.0
    genre: Optional[str] = None
    source_format: str = ""
    source_detail: str = ""
    chords: List[Tuple[float, float, str]] = field(default_factory=list)
    sections: List[Tuple[float, float, str]] = field(default_factory=list)
    notes: List[Dict] = field(default_factory=list)


def merge_adjacent(regions: List[Tuple[float, float, str]],
                   eps: float = 1e-6) -> List[Tuple[float, float, str]]:
    """Merge touching regions that carry the same label.

    Keeps fixture JSON compact and matches the hand-authored
    pub_feed style where a 17-bar A5 run is one region.
    """
    out: List[Tuple[float, float, str]] = []
    for start, end, label in sorted(regions, key=lambda r: r[0]):
        if out and out[-1][2] == label and abs(out[-1][1] - start) <= eps:
            out[-1] = (out[-1][0], end, label)
        else:
            out.append((start, end, label))
    return out


def build_fixture(parsed: ParsedSong, *, split: str,
                  curated_by: Optional[str]) -> Dict:
    """Assemble the fixture JSON dict from a ParsedSong."""
    regions = [
        {"start": round(s, 3), "end": round(e, 3), "label": lab}
        for s, e, lab in merge_adjacent(parsed.chords)
        if lab.upper() not in ("N.C.", "NC", "N.C")
    ]
    data: Dict = {
        "song": parsed.song,
        "artist": parsed.artist,
        "source": parsed.source_detail
        or f"imported via scripts.import_groundtruth ({parsed.source_format})",
        "source_format": parsed.source_format,
        "time_signature": parsed.time_signature,
        "duration_s": round(parsed.duration_s, 3),
        "offset_s": round(parsed.offset_s, 3),
        "confidence": f"{parsed.source_format}-import-v1",
        "schema_version": 2,
        "split": split,
        "license": "first-party",
        "tags": [f"{parsed.source_format}-imported"],
        "added_at_unix": int(time.time()),
        "regression_floor_triad_relaxed": 0.0,
        "_regression_floor_notes": (
            "Placeholder. Pin with `python -m bench.corpus add "
            "--measure-floor` once audio is attached."
        ),
        "regions": regions,
    }
    if parsed.tempo_bpm:
        data["tempo_bpm"] = round(parsed.tempo_bpm, 3)
    if parsed.key:
        data["key"] = parsed.key
    if parsed.genre:
        data["genre"] = parsed.genre
    if curated_by:
        data["curated_by"] = curated_by
    if parsed.sections:
        # Sections are NOT merged: two back-to-back Chorus passes are
        # two sections — the repeat boundary is exactly what boundary
        # F-measure evaluates.
        data["sections"] = [
            {"start": round(s, 3), "end": round(e, 3), "label": lab}
            for s, e, lab in sorted(parsed.sections)
        ]
    if parsed.notes:
        data["notes"] = parsed.notes
    return data


# ---------------------------------------------------------------------------
# Chart parser
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*(?P<opts>.*)$")
_HEADER_RE = re.compile(r"^(?P<key>[a-zA-Z_]+)\s*:\s*(?P<value>.+)$")
_CHORD_TOKEN_RE = re.compile(
    r"^(?P<sym>[A-G][#b]?"
    r"(?:maj7|maj9|min7|min9|min|m7|m9|m|dim7|dim|aug|sus2|sus4|add9|maj|dom7|7|5)?"
    r"|N\.?C\.?)"
    r"(?:\*(?P<mult>\d+(?:\.\d+)?))?$",
    re.IGNORECASE,
)


def _beats_per_bar(time_signature: str) -> float:
    """4/4 -> 4 quarter-beats; 6/8 -> 3 (dotted-quarter pulse x2 = 3
    quarter-note beats of clock time per bar: 6 eighths = 3 quarters).

    Bar length in seconds is computed against the quarter-note BPM
    convention used by every chart/tab source we ingest.
    """
    try:
        num, den = time_signature.split("/")
        return float(Fraction(int(num), int(den)) * 4)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"bad time signature {time_signature!r}") from exc


def parse_chart(text: str) -> ParsedSong:
    """Parse the bar-grid chord chart format into a ParsedSong.

    Raises ValueError with a line-numbered message on any syntax
    problem — the curator should fix the chart, not get a half-baked
    fixture.
    """
    parsed = ParsedSong(source_format="chart")
    header: Dict[str, str] = {}
    # (name, bpm_override, repeats, [(symbol, bars), ...])
    sections: List[Tuple[str, Optional[float], int, List[Tuple[str, float]]]] = []
    current: Optional[Tuple[str, Optional[float], int,
                            List[Tuple[str, float]]]] = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        m = _SECTION_RE.match(line)
        if m:
            name = m.group("name").strip()
            bpm_override: Optional[float] = None
            repeats = 1
            for opt in m.group("opts").split():
                if opt.startswith("bpm="):
                    bpm_override = float(opt[4:])
                elif re.fullmatch(r"x\d+", opt):
                    repeats = int(opt[1:])
                elif opt.startswith("bars="):
                    pass  # informational; bar count comes from tokens
                else:
                    raise ValueError(
                        f"line {lineno}: unknown section option {opt!r}"
                    )
            current = (name, bpm_override, repeats, [])
            sections.append(current)
            continue

        if current is None:
            hm = _HEADER_RE.match(line)
            if not hm:
                raise ValueError(
                    f"line {lineno}: expected `key: value` header or "
                    f"[Section], got {line!r}"
                )
            header[hm.group("key").lower()] = hm.group("value").strip()
            continue

        # Chord token line inside a section.
        prev_symbol: Optional[str] = (
            current[3][-1][0] if current[3] else None
        )
        for tok in line.replace("|", " ").split():
            if tok == ".":
                if prev_symbol is None:
                    raise ValueError(
                        f"line {lineno}: `.` with no previous chord"
                    )
                current[3].append((prev_symbol, 1.0))
                continue
            tm = _CHORD_TOKEN_RE.match(tok)
            if not tm:
                raise ValueError(
                    f"line {lineno}: unparsable chord token {tok!r}"
                )
            sym = tm.group("sym")
            # Preserve written case for the root but normalise NC.
            if sym.upper().replace(".", "") == "NC":
                sym = "N.C."
            bars = float(tm.group("mult")) if tm.group("mult") else 1.0
            if bars <= 0:
                raise ValueError(
                    f"line {lineno}: bar multiplier must be > 0 in {tok!r}"
                )
            current[3].append((sym, bars))
            prev_symbol = sym

    # --- headers ---------------------------------------------------------
    if "bpm" not in header:
        raise ValueError("chart missing required header `bpm:`")
    parsed.tempo_bpm = float(header["bpm"])
    parsed.song = header.get("song", "")
    parsed.artist = header.get("artist", "")
    parsed.time_signature = header.get("time", "4/4")
    parsed.offset_s = float(header.get("offset", "0"))
    parsed.key = header.get("key")
    parsed.genre = header.get("genre")
    if not sections:
        raise ValueError("chart has no [Section] blocks")

    beats = _beats_per_bar(parsed.time_signature)

    # --- walk the bar grid ------------------------------------------------
    t = parsed.offset_s
    for name, bpm_override, repeats, tokens in sections:
        if not tokens:
            raise ValueError(f"section [{name}] has no chord tokens")
        bpm = bpm_override if bpm_override else parsed.tempo_bpm
        bar_s = beats * 60.0 / bpm
        for _ in range(repeats):
            sec_start = t
            for sym, bars in tokens:
                dur = bars * bar_s
                parsed.chords.append((t, t + dur, sym))
                t += dur
            parsed.sections.append((sec_start, t, name))

    parsed.duration_s = float(header.get("duration", t))
    if parsed.duration_s < t - 1e-6:
        # Clip the grid to the declared audio duration (charts often
        # notate a final ring-out bar the recording fades before).
        parsed.chords = [
            (s, min(e, parsed.duration_s), lab)
            for s, e, lab in parsed.chords if s < parsed.duration_s
        ]
        parsed.sections = [
            (s, min(e, parsed.duration_s), lab)
            for s, e, lab in parsed.sections if s < parsed.duration_s
        ]
    parsed.source_detail = (
        f"chord chart import; bpm={parsed.tempo_bpm}, "
        f"time={parsed.time_signature}, offset={parsed.offset_s}s"
    )
    return parsed


# ---------------------------------------------------------------------------
# .lab parser
# ---------------------------------------------------------------------------


def parse_lab(text: str) -> List[Tuple[float, float, str]]:
    """Parse MIREX .lab lines: `start end label` (whitespace-separated).

    Blank lines and lines starting with `#` are skipped. Inline `#`
    is NOT a comment — chord labels contain sharps ("F#m").
    """
    out: List[Tuple[float, float, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            raise ValueError(f".lab line {lineno}: expected `start end label`")
        try:
            start, end = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise ValueError(f".lab line {lineno}: bad timestamps") from exc
        label = " ".join(parts[2:])
        if end <= start:
            raise ValueError(f".lab line {lineno}: end <= start")
        out.append((start, end, label))
    out.sort(key=lambda r: r[0])
    return out


# ---------------------------------------------------------------------------
# MIDI parser (pretty_midi, optional)
# ---------------------------------------------------------------------------


def parse_midi(path: Path) -> ParsedSong:
    try:
        import pretty_midi  # noqa: WPS433 — optional dep
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "pretty_midi is required for MIDI import: pip install pretty_midi"
        ) from exc

    pm = pretty_midi.PrettyMIDI(str(path))
    parsed = ParsedSong(source_format="midi", song=path.stem)
    parsed.duration_s = float(pm.get_end_time())
    tempi = pm.get_tempo_changes()[1]
    if len(tempi):
        parsed.tempo_bpm = float(tempi[0])

    for inst in pm.instruments:
        if inst.is_drum:
            continue
        for n in inst.notes:
            parsed.notes.append({
                "pitch": int(n.pitch),
                "start": round(float(n.start), 4),
                "end": round(float(n.end), 4),
                "velocity": int(n.velocity),
                "instrument": inst.name or f"program_{inst.program}",
            })
    parsed.notes.sort(key=lambda n: (n["start"], n["pitch"]))

    # Markers -> sections when the file carries them.
    markers = getattr(pm, "markers", []) or []
    for i, mk in enumerate(markers):
        end = (markers[i + 1].time if i + 1 < len(markers)
               else parsed.duration_s)
        parsed.sections.append((float(mk.time), float(end), str(mk.text)))
    parsed.source_detail = f"MIDI import from {path.name}"
    return parsed


# ---------------------------------------------------------------------------
# Guitar Pro parser (PyGuitarPro, optional)
# ---------------------------------------------------------------------------


def parse_guitarpro(path: Path) -> ParsedSong:
    try:
        import guitarpro  # noqa: WPS433 — optional dep
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "PyGuitarPro is required for Guitar Pro import: "
            "pip install PyGuitarPro"
        ) from exc

    song = guitarpro.parse(str(path))
    parsed = ParsedSong(source_format="guitarpro",
                        song=song.title or path.stem,
                        artist=song.artist or "")
    parsed.tempo_bpm = float(song.tempo)
    parsed.source_detail = f"Guitar Pro import from {path.name}"

    # --- measure clock: walk headers once, honouring tempo changes -----
    measure_starts: List[float] = []
    t = 0.0
    bpm = float(song.tempo)
    for header in song.measureHeaders:
        measure_starts.append(t)
        if header.tempo and header.tempo.value:
            bpm = float(header.tempo.value)
        num = header.timeSignature.numerator
        den = header.timeSignature.denominator.value
        bar_quarters = num * 4.0 / den
        t += bar_quarters * 60.0 / bpm
        # Section markers live on headers.
        if header.marker is not None:
            parsed.sections.append((measure_starts[-1], t, header.marker.title))
    parsed.duration_s = t

    # Extend each marker section to the next marker (headers only know
    # their own bar span).
    if parsed.sections:
        fixed: List[Tuple[float, float, str]] = []
        for i, (s, _e, lab) in enumerate(parsed.sections):
            e = (parsed.sections[i + 1][0]
                 if i + 1 < len(parsed.sections) else parsed.duration_s)
            fixed.append((s, e, lab))
        parsed.sections = fixed

    ts0 = song.measureHeaders[0].timeSignature if song.measureHeaders else None
    if ts0 is not None:
        parsed.time_signature = f"{ts0.numerator}/{ts0.denominator.value}"

    # --- notes + chord diagrams -----------------------------------------
    quarter_ticks = float(guitarpro.Duration.quarterTime)  # 960
    for track in song.tracks:
        if track.isPercussionTrack:
            continue
        strings = {s.number: s.value for s in track.strings}
        bpm = float(song.tempo)
        for mi, measure in enumerate(track.measures):
            header = measure.header
            if header.tempo and header.tempo.value:
                bpm = float(header.tempo.value)
            m_start = measure_starts[mi] if mi < len(measure_starts) else 0.0
            sec_per_tick = 60.0 / bpm / quarter_ticks
            for voice in measure.voices:
                tick = 0.0
                for beat in voice.beats:
                    b_start = m_start + tick * sec_per_tick
                    b_dur = float(beat.duration.time) * sec_per_tick
                    if beat.effect and beat.effect.chord is not None:
                        name = beat.effect.chord.name
                        if name:
                            parsed.chords.append(
                                (b_start, b_start + b_dur, name)
                            )
                    for note in beat.notes:
                        base = strings.get(note.string)
                        if base is None:
                            continue
                        parsed.notes.append({
                            "pitch": int(base + note.value),
                            "start": round(b_start, 4),
                            "end": round(b_start + b_dur, 4),
                            "velocity": int(note.velocity),
                            "instrument": track.name,
                        })
                    tick += float(beat.duration.time)
    parsed.notes.sort(key=lambda n: (n["start"], n["pitch"]))

    # Chord diagrams mark the START of a harmony; extend each to the
    # next chord (or song end) so regions tile the timeline.
    if parsed.chords:
        parsed.chords.sort(key=lambda r: r[0])
        tiled: List[Tuple[float, float, str]] = []
        for i, (s, _e, lab) in enumerate(parsed.chords):
            e = (parsed.chords[i + 1][0]
                 if i + 1 < len(parsed.chords) else parsed.duration_s)
            if e > s:
                tiled.append((s, e, lab))
        parsed.chords = tiled
    return parsed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_fixture(data: Dict, out: Optional[Path], default_stem: str) -> Path:
    if out is None:
        out = Path(f"{default_stem}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.import_groundtruth",
        description="Convert notation into corpus fixture JSON.",
        epilog=CHART_FORMAT_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", type=Path, default=None,
                        help="Output fixture JSON path.")
    parser.add_argument("--split", default="train",
                        choices=("train", "val", "test", "holdout"),
                        help="Corpus split (default: train — user-fed "
                             "corpus drives tuning; keep test held out).")
    parser.add_argument("--curated-by", default=None)
    sub = parser.add_subparsers(dest="fmt", required=True)

    p_chart = sub.add_parser("chart", help="Bar-grid chord chart text.")
    p_chart.add_argument("path", type=Path)

    p_lab = sub.add_parser("lab", help="MIREX .lab chord labels.")
    p_lab.add_argument("path", type=Path)
    p_lab.add_argument("--duration", type=float, required=True,
                       help="Audio duration in seconds (denominator "
                            "for WCSR — .lab carries no duration).")
    p_lab.add_argument("--sections-lab", type=Path, default=None,
                       help="Optional second .lab with section labels.")

    p_midi = sub.add_parser("midi", help="MIDI note-level ground truth.")
    p_midi.add_argument("path", type=Path)

    p_gp = sub.add_parser("gp", help="Guitar Pro tab.")
    p_gp.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    if not args.path.exists():
        sys.stderr.write(f"input not found: {args.path}\n")
        return 2

    if args.fmt == "chart":
        parsed = parse_chart(args.path.read_text(encoding="utf-8"))
        if not parsed.song:
            parsed.song = args.path.stem
    elif args.fmt == "lab":
        parsed = ParsedSong(source_format="lab", song=args.path.stem)
        parsed.chords = parse_lab(args.path.read_text(encoding="utf-8"))
        parsed.duration_s = args.duration
        if args.sections_lab:
            parsed.sections = parse_lab(
                args.sections_lab.read_text(encoding="utf-8"))
        parsed.source_detail = f".lab import from {args.path.name}"
    elif args.fmt == "midi":
        parsed = parse_midi(args.path)
    else:  # gp
        parsed = parse_guitarpro(args.path)

    data = build_fixture(parsed, split=args.split,
                         curated_by=args.curated_by)

    # Validate against the shared schema before writing, unless the
    # fixture is intentionally chord-free (pure MIDI/section truth).
    if data["regions"]:
        from bench.schema import validate_fixture_json
        errors = validate_fixture_json(data)
        if errors:
            for err in errors:
                sys.stderr.write(f"schema: {err}\n")
            return 1

    out = _write_fixture(data, args.out, args.path.stem)
    n_chords = len(data.get("regions", []))
    n_sections = len(data.get("sections", []))
    n_notes = len(data.get("notes", []))
    sys.stdout.write(
        f"wrote {out}\n"
        f"  duration : {data['duration_s']}s\n"
        f"  chords   : {n_chords} regions\n"
        f"  sections : {n_sections}\n"
        f"  notes    : {n_notes}\n"
        f"  split    : {data['split']}\n"
        "Next: python -m bench.corpus add --json "
        f"{out} --other other.wav [--bass bass.wav] --measure-floor\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
