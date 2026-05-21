# Tone Forge

AI-powered audio analysis and tone recreation. Upload any audio → get gear recommendations, MIDI extraction, and DAW-ready exports.

## What It Does

**Analyze any audio** (YouTube link, file upload, or URL) and get:

- **Guitar/Bass**: Amp family detection, gain staging, cab character, effects chain → Helix presets, pedal recommendations
- **Synth**: Oscillator type, filter settings, envelope, LFO → hardware synth matches (Volca, Minilogue, etc.)
- **Drums**: Kick/snare/hihat analysis, tempo detection → drum machine recommendations (TR-808, 909, etc.)
- **MIDI Extraction**: Polyphonic transcription from any stem with genre-aware post-processing
- **Ableton Export**: Full .als Live Sets with MIDI tracks, or Wavetable/Analog presets

## Features

### Analysis Modes

| Mode | What It Does | Time |
|------|--------------|------|
| **Quick** | Analyze uploaded audio directly | ~5 sec |
| **Deep** | AI stem separation (Demucs) → per-instrument analysis + MIDI | 2-4 min |

### Instrument Detection

Automatically detects whether audio contains guitar, bass, synth, or drums and routes to appropriate analyzer.

### Export Formats

- **Helix .hlx** — Native Line 6 preset files
- **HX Stomp .hlx** — Optimized for 6-block limit
- **Neural DSP** — Quad Cortex compatible JSON
- **Ableton Live Set .als** — Full project with MIDI tracks
- **Ableton Wavetable .adv** — Synth preset (Suite only)
- **Ableton Analog .adv** — Synth preset
- **Project Bundle .zip** — MIDI files + analysis + presets
- **JSON** — Raw analysis data

### MIDI Extraction

- **Polyphonic** — Uses Spotify's basic-pitch ML model
- **Drum-specific** — Onset detection with frequency band classification
- **Genre-aware** — Synthwave-optimized profiles for bass, pads, leads
- **Post-processing** — Key detection, quantization, noise removal, note merging

## Architecture

```
   audio                   abstract                hardware
   ─────                   ────────                ────────
  ┌──────────┐   ┌─────────────────────┐   ┌──────────────────┐
  │ Analyzer │ → │  Tone Descriptor    │ → │   Translators    │
  │ (DSP+ML) │   │  (JSON, hardware-   │   │  helix / pedals  │
  │          │   │   agnostic)         │   │  / synths / ...  │
  └──────────┘   └─────────────────────┘   └──────────────────┘
```

- **Analyzers** — Demucs stem separation → feature extraction → classifiers
  - `analyzer.py` — Guitar tone analysis
  - `synth_analyzer.py` — Synthesizer analysis
  - `bass_analyzer.py` — Bass-specific analysis
  - `drum_analyzer.py` — Drum/beat analysis
  - `auto_detect.py` — Instrument type detection

- **Descriptors** — Hardware-agnostic JSON schemas
  - `ToneDescriptor` — Guitar amp/cab/effects
  - `SynthDescriptor` — Oscillator/filter/envelope
  - `BassDescriptor` — Bass amp/technique
  - `DrumDescriptor` — Kick/snare/hihat characteristics

- **Translators** — Convert descriptors to gear recommendations
  - `helix_translator.py` — Line 6 Helix/HX
  - `pedal_translator.py` — Real pedal recommendations
  - `synth_translator.py` — Hardware synth matching
  - `bass_translator.py` — Bass gear
  - `drum_translator.py` — Drum machines

## Repo Layout

```
tone-forge/
├── backend/
│   ├── tone_forge/
│   │   ├── analyzer.py           # Guitar tone analysis
│   │   ├── synth_analyzer.py     # Synth analysis
│   │   ├── bass_analyzer.py      # Bass analysis
│   │   ├── drum_analyzer.py      # Drum analysis
│   │   ├── auto_detect.py        # Instrument detection
│   │   ├── midi_extractor.py     # Audio → MIDI
│   │   ├── stem_separator.py     # Demucs integration
│   │   ├── als_template.py       # Ableton Live Set generation
│   │   ├── preset_export.py      # All export formats
│   │   ├── helix_translator.py   # Helix chain builder
│   │   ├── synth_translator.py   # Synth matcher
│   │   └── descriptor.py         # Data classes
│   ├── data/
│   │   ├── helix_blocks.json     # Helix amp/cab/fx catalog
│   │   ├── hardware_synths.json  # Synth database
│   │   ├── bass_blocks.json      # Bass gear catalog
│   │   └── drum_machines.json    # Drum machine database
│   ├── static/                   # Web UI
│   │   ├── index.html
│   │   ├── style.css
│   │   ├── app.js
│   │   └── synth-panels/         # SVG synth visualizations
│   ├── tests/
│   ├── cli.py                    # Command-line interface
│   ├── tone_forge_api.py         # FastAPI server
│   └── requirements.txt
└── frontend/                     # (Future React app)
```

## Quickstart

```bash
cd backend
pip install -r requirements.txt

# Web UI on http://localhost:8000
uvicorn tone_forge_api:app --reload --port 8000

# Or CLI
python cli.py path/to/clip.wav --hardware helix
```

### Requirements

- Python 3.10+
- ~4GB disk for Demucs models (downloaded on first deep analysis)
- FFmpeg (for YouTube downloads)

### Key Dependencies

- `demucs` — AI stem separation
- `basic-pitch` — Polyphonic MIDI extraction
- `librosa` — Audio analysis
- `pretty_midi` — MIDI file handling
- `yt-dlp` — YouTube downloads

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analyze` | POST | Analyze audio (file, URL, or YouTube) |
| `/api/export` | POST | Export to preset format |
| `/api/history` | GET | Get analysis history |
| `/api/history/{id}` | GET | Get specific analysis |

## How Analysis Works

### Guitar

1. **Spectral analysis** — FFT, spectral centroid, rolloff, contrast
2. **Harmonic analysis** — Harmonic-to-noise ratio, fundamental tracking
3. **Amp family classification** — Rules engine maps features to amp types
4. **Gain staging** — RMS, crest factor, distortion characteristics
5. **Cabinet detection** — Speaker resonance, mic position estimation
6. **Effects detection** — Modulation, delay, reverb presence

### Synth

1. **Oscillator detection** — Waveform shape (saw, square, sine, etc.)
2. **Filter analysis** — Cutoff frequency, resonance, type
3. **Envelope extraction** — Attack, decay, sustain, release
4. **LFO detection** — Rate and depth estimation
5. **Hardware matching** — Compare to synth database

### Drums

1. **HPSS separation** — Isolate percussive content
2. **Band-pass filtering** — Split into kick/snare/hihat bands
3. **Onset detection** — Find hit times with RMS analysis
4. **Tempo estimation** — Beat tracking
5. **Machine matching** — Compare to drum machine database

### MIDI Extraction

1. **Stem separation** — Demucs isolates instruments
2. **Pitch detection** — basic-pitch for melodic, onset detection for drums
3. **Post-processing** — Key filtering, quantization, note merging
4. **Genre optimization** — Synthwave profiles for bass/pad/lead

## Configuration

### Synthwave Optimization

The MIDI extractor has genre-specific profiles. Synthwave mode:

- Lower onset thresholds for soft synth attacks
- Aggressive note merging for sustained bass
- Octave correction for sub-bass detection
- Delay/echo filtering for lead lines

### Stem Types

| Stem | Profile | Notes |
|------|---------|-------|
| bass | Long notes, low threshold, octave shift | Sub-bass correction |
| drums | Onset-based, tight quantization | Band-split detection |
| pad | Very low threshold, long notes | Captures slow attacks |
| lead | Medium threshold, delay filtering | Removes echo artifacts |
| synth | Balanced settings | Generic synth |

## Development

```bash
# Run tests
cd backend
pytest tests/ -v

# Type checking
mypy tone_forge/

# Format
black tone_forge/
```

## License

MIT
