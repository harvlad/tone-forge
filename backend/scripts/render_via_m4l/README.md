# M4L automated render — PoC (Stage 1)

**Goal of this PoC:** prove that a Max for Live device with `sfrecord~` on the
master chain produces a WAV indistinguishable from Live's manual "Export
Audio" of the same ALS. If it passes, we have a fully unattended render path
for the 99 Analog + 861 non-Analog presets.

This document is **Stage 1** only — single preset, mostly manual,
verification-focused. Stage 2 (batch driver for all 960 presets) will be
written only after Stage 1 passes.

---

## Why M4L (and why real-time)

- Live's Python Remote Script API does **not** expose render-to-disk,
  freeze, flatten, or bounce. Verified against the 11.x API reference.
- M4L can host `sfrecord~`, Max's stereo file recorder, which captures
  audio from the master chain in real time.
- This is a real-time capture, not an offline render — clip length
  is the floor for render time. For the existing 8-bar test sequences
  (~3 s each at 120 BPM) the wall-clock cost is small.

---

## Stage 1 PoC — one preset, round-trip

### Pre-requisites

- Ableton Live 12.x **Suite** (or Live with Max for Live installed)
- Max 8 or Max 9
- Any one ALS file in `preset_catalog_output/als/` — `analog_saw_filter_bass.als`
  is the recommended PoC target (matches the plan-mode plan's equivalence-test
  preset)
- A scratch directory: `/tmp/tone_forge_render_poc/` (will be auto-created)

### Step 1. Build the M4L recorder device

In Live, on the **Master** track, drop a fresh **Max Audio Effect** device
(`Max for Live > Max Audio Effect`). Click the **pencil/edit icon** to open
Max with the blank patcher template.

Max opens with two default objects: `plugin~` (audio in from Live) and
`plugout~` (audio out to Live). Leave both in place. Add the following
nine boxes and connect them per the wiring list. Use `n` in Max to create
a new box, then type the box content listed below.

#### Boxes to add (in addition to the default `plugin~` / `plugout~`):

| # | Object box content                                | Purpose                                                  |
|---|---------------------------------------------------|----------------------------------------------------------|
| 1 | `live.thisdevice`                                 | Fires `bang` from left outlet once device is fully ready |
| 2 | `live.observer @path live_set is_playing`         | Watches transport; outputs `0` (stopped) or `1` (playing) |
| 3 | `live.path live_set`                              | Resolves to the live_set id; not strictly needed but lets us address it from `live.observer` |
| 4 | `sel 1 0`                                         | Splits play=1 vs stopped=0 into two outlets              |
| 5 | `sprintf /tmp/tone_forge_render_poc/render_%ld.wav` | Builds an output path containing a unix timestamp        |
| 6 | `date @output milliseconds`                       | Generates a millisecond timestamp on bang                |
| 7 | `prepend open`                                    | Prefixes the path with `open` so `sfrecord~` opens it    |
| 8 | `sfrecord~ 2 @samptype float32`                   | The actual recorder; 2 ch, float32 WAV                   |
| 9 | `print TF_RENDER`                                 | Logs state to the Max console for debugging              |

#### Wiring:

1. `plugin~` outlet 1 → `sfrecord~` inlet 1
2. `plugin~` outlet 2 → `sfrecord~` inlet 2
3. `plugin~` outlet 1 → `plugout~` inlet 1 (pass-through; keeps Live's audio flowing)
4. `plugin~` outlet 2 → `plugout~` inlet 2
5. `live.thisdevice` left outlet → `live.path live_set` inlet (registers the path on device-ready)
6. `live.observer` outlet → `sel 1 0` inlet
7. `sel 1 0` outlet 1 (matched-1, i.e. playback started) → `date` inlet
8. `date` outlet → `sprintf` inlet (right outlet, the formatted-string carrier)
9. `sprintf` outlet → `prepend open` inlet
10. `prepend open` outlet → `sfrecord~` inlet 1 (sfrecord~ accepts `open <path>` to set the output file)
11. After a 50 ms delay (use a `delay 50` box) feed `1` into `sfrecord~` inlet 1 to start recording
12. `sel 1 0` outlet 2 (matched-0, playback stopped) → `0` message → `sfrecord~` inlet 1 (stops + closes file)
13. Tap any of the bang/state outputs into `print TF_RENDER` so the Max
    console reports state changes

Save (Cmd+S). Max writes the frozen device back to Live as `.amxd`.

> **Sanity check after build:** with Live's transport stopped, the Max
> console should show `TF_RENDER 0`. Press Play in Live; you should see
> `TF_RENDER 1` immediately, the file at `/tmp/tone_forge_render_poc/`
> should grow in size while the transport runs, and pressing Stop should
> see `TF_RENDER 0` again and the file should finalise.

### Step 2. Capture the M4L render

1. In Live, open `preset_catalog_output/als/analog_saw_filter_bass.als`
   (any other ALS works too; this one is the equivalence-test default).
2. Confirm the Master track has the recorder device.
3. Press **Play**. Let the existing test-sequence clip play through
   exactly once. Press **Stop**.
4. The recorded WAV will appear at
   `/tmp/tone_forge_render_poc/render_<ms>.wav`. Rename it
   `m4l_capture.wav`.

### Step 3. Capture a manual export of the same ALS

This is the reference. With the **same ALS still open**:

1. **Disable / remove the M4L recorder device** (so it doesn't taint the
   master output during the export).
2. File > Export Audio/Video, render to
   `/tmp/tone_forge_render_poc/manual_export.wav`. Use:
   - Rendered Track: Master
   - File Type: WAV
   - Sample Rate: 48000 Hz
   - Bit Depth: 24
   - Render as Loop: off
   - Dither: triangular (or any — must match between the two renders)
3. Re-enable the M4L recorder device (so it stays attached for any
   subsequent ALS in this session, but it is no longer the reference's
   master signal).

### Step 4. Run the equivalence test

```bash
cd /Users/mattharvey/Sites/tone-forge/backend
python3 scripts/equivalence_test.py \
    /tmp/tone_forge_render_poc/m4l_capture.wav \
    /tmp/tone_forge_render_poc/manual_export.wav \
    --report /tmp/tone_forge_render_poc/equivalence_report.json
```

### Pass criteria

The existing `equivalence_test.py` thresholds:

- mel-cosine > 0.98
- envelope correlation > 0.90
- centroid correlation > 0.90
- RMS ratio in [0.9, 1.1]

If all four hold, the M4L recorder is acoustically equivalent to manual
export and the renderer is proved. The waveform-correlation diagnostic
may still be low because Analog's LFOs/oscillators are free-running
across the two takes; that is fine.

### Failure modes to investigate (in order of likelihood)

1. **Pass-through loop / double signal.** If the M4L device routes
   `plugin~` to `plugout~` AND `sfrecord~` reads from the master
   simultaneously, the recorded file may contain only the synth — or it
   may contain a 6 dB-boosted version (`plugout~` feeding back into the
   master sum). Check RMS ratio. Fix: ensure the M4L device is an
   **Audio Effect** on the master chain, and `plugout~` is in place so
   Live's master continues to output audio unchanged.

2. **Sample-rate mismatch.** If Live runs at 44.1 kHz but the export
   was set to 48 kHz, the M4L capture will be 44.1 kHz. Set Live's
   audio preferences to 48 kHz before recording.

3. **File still open.** If you read the WAV before Live's transport
   stop has fully flushed `sfrecord~`, the file may be truncated.
   `sfrecord~` only finalises on `0`. Make sure you press Stop and wait
   ~200 ms before copying the file.

4. **Latency offset.** M4L audio devices on master typically have zero
   added latency, but if PDC (Plugin Delay Compensation) is on, there
   may be a slight offset. `equivalence_test.py` already does
   silence-trim, so a small offset is harmless. Only worry about this
   if envelope correlation is suspiciously low (< 0.7).

---

## Stage 2 — batch driver (not built yet)

Once Stage 1 passes, the batch driver looks like this (this section is
**design notes**, not committed code):

```
for als in preset_catalog_output/als/*.als:
    1. AppleScript: tell Live to open <als>
    2. Wait for Live's main window title to contain the basename
    3. AppleScript: send Cmd+. (stop) to reset transport
    4. Send Cmd+space (play) to start
    5. M4L device records to /tmp/.../<preset_id>.wav
    6. Watch the file size; when it stops growing for 250 ms, send Cmd+. (stop)
    7. Move the WAV to preset_catalog_output/audio/<preset_id>.wav
    8. Write a manifest row with input sha + output sha + duration
```

Open questions for Stage 2 that we'll resolve only after Stage 1 passes:

- Whether `open <als> -a "Live 12"` adopts the existing Live instance (it
  should) or spawns a second one.
- Whether the `live.observer` on transport stays connected when Live
  switches projects, or whether the device needs to be re-armed.
- Whether opening a different `.als` resets the M4L device's connection
  to its `live_set` path (we'll need a `live.thisdevice` → re-arm chain).

These are all answerable with one or two probe sessions once Stage 1 is
green.

---

## Operator runbook checklist

- [ ] Live Suite installed
- [ ] Max for Live licence active
- [ ] M4L recorder device built per Step 1
- [ ] Sample rate set to 48 kHz in Live preferences
- [ ] PoC ALS opens cleanly
- [ ] Stage 1 Steps 2–4 executed
- [ ] Report at `/tmp/tone_forge_render_poc/equivalence_report.json`
- [ ] All four equivalence gates PASS

When the checklist is complete, paste the report contents back into the
session and I will write Stage 2.
