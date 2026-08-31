# jamn VST3/AU Plugin — Scope

Goal: the proprietary-looking version of the Jam Kit inside any DAW —
a fully custom-skinned plugin that loads a song's kit pack and plays it
host-synced. This is the productization step past the two bridges that
already exist (Drum Rack `.adg` export; jamn desktop + Ableton Link).

## Why a plugin at all

- `.adg` export: works today, zero UI control — Ableton chrome only.
- jamn + Link: our brand on screen, but a second app beside the DAW.
- Plugin: our UI **inside** the DAW, cross-DAW (Live Standard, Logic,
  FL, Reaper), and the thing users mean by "premium plugin".

## Architecture

**Framework:** JUCE 8 (AU + VST3 from one codebase; AUv3 later if
iPad DAWs matter). C++20.

**One generic plugin, per-song data packs.** The plugin is a player;
songs are packs. Reuses the EXACT pack the Ableton exporter already
emits (`kit.json` + `Samples/*.wav`) — backend work is DONE. Pack
sources, in order of build effort:
1. "Open pack folder/zip" (file chooser) — v0, no auth needed.
2. Signed-in browser hitting `GET /api/history` + `/ableton-kit`
   (reuse AuthContext token flow) — v1.

**Audio engine** (mirrors the app semantics, small surface):
- 16 voices, one per pad; sample playback from preloaded buffers.
- Loop pads: seamless loop with crossfade (`crossfadeMs` from kit.json,
  same 8–30 ms clamp), bar-snapped length.
- Launch quantize: from the HOST — `AudioPlayHead::PositionInfo`
  (tempo, ppqPosition, bar start). Pads queue to the next bar. This is
  the plugin's version of loop-lock; no Link needed, the host clock IS
  the grid.
- Choke: retrigger self-chokes (same as SampleScheduler hold mode).
- MIDI in: C1..D#2 → pads (identical map to the .adg), so a controller
  or piano-roll clip drives it; UI pads mirror.

**UI spec** (the actual point):
- 4×4 pad grid, category colors = `_CATEGORY_HEX` (drums red EF4444,
  bass green 22C55E, chords amber F59E0B…), descriptive labels,
  ringing/armed states, loop playhead sweep — visual parity with the
  mobile `SamplePadGrid4x4` / desktop launchpad.
- jamn wordmark + song title header; LoopCycleStrip-style bar sweep.
- 8 macro knobs (filter, space/reverb send, crush, gain…) mapped to
  plugin parameters so DAW automation works.
- JUCE `LookAndFeel_V4` subclass carrying TFTheme (dark, rounded 10px
  tiles, chip typography). All custom drawing — zero stock JUCE look.

## Milestones

| # | Deliverable | Est |
|---|-------------|-----|
| 0 | Repo `plugin/` with JUCE via CMake FetchContent; empty AU+VST3 loads in Live/Logic | 2–3 d |
| 1 | Pack loader (kit.json + WAVs), MIDI-triggered one-shot playback | 3–5 d |
| 2 | Loop engine: crossfaded loops, host-bar quantize, choke, release | 1 wk |
| 3 | Skinned UI: pad grid, states, playheads, header | 1–2 wk |
| 4 | Macros/params + automation, preset (= pack path) state save in the DAW project | 3–5 d |
| 5 | Signed-in pack browser (history list → download pack) | 1 wk |
| 6 | Codesign + notarize + installer (pkg), CI build | 3–5 d |

Total: ~5–7 weeks part-time. v0 demo (milestones 0–2, stock-ish UI) in
about two weeks.

## Risks / decisions

- **JUCE licensing:** GPLv3 free, or commercial tier (~$40/mo Indie
  under $500k revenue). Fine either way pre-revenue; decide at ship.
- **Sample memory:** 16 × 8 s stereo 44.1k float ≈ 45 MB/pack — fine.
- **Host quantize edge cases:** ppq math differs per host at loop
  points/tempo ramps; test matrix = Live, Logic, Reaper.
- **AUv3/iPad:** out of scope until desktop proves demand.
- **Do NOT build per-song plugin binaries** — data-as-pack is the
  model (same reason every sampler works this way; signing per song
  would be misery).

## Relationship to existing bridges

All three share the kit-pack format. Exporter stays (users without the
plugin), Link stays (full-app experience). Plugin milestone 1 consumes
the exporter's zip verbatim — nothing thrown away.
