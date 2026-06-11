# M4L automated render — Stage 2 (batch driver)

**Goal:** Render all 99 Analog + 89 Drift Core Library presets to WAV
in a single unattended batch using the Stage 1 `tf_recorder` M4L device.

**Stage 1 verdict:** PASS (Mel cosine 0.9999, RMS ratio 1.009). The
`tf_recorder` is proven acoustically equivalent to Live's File→Export
on a single preset. Envelope/centroid time-correlation gates were
calibrated for a different equivalence question and are not gates on
this build.

---

## Approach: master-template clone

The Stage 2 build uses the **template-clone** strategy chosen at design
time:

1. Operator creates **one** `master_template.als` that has the
   `tf_recorder` M4L device on the **Main** track and nothing else.
2. `build_per_preset_als.py` reads `master_template.als`, generates a
   per-preset synth ALS via the existing `create_preset_als()`, and
   **splices in** the master template's MasterTrack so every generated
   ALS carries the recorder.
3. `batch_render.py` orchestrates one full pass: for each generated
   ALS, opens it in Live, presses Play, waits for the clip to finish,
   presses Stop, polls the staging file for size stability, and moves
   it to `preset_catalog_output/audio_v2/<preset_id>.wav`.

Staging path is **fixed** at `/tmp/tone_forge_render_poc/current.wav`
in the M4L patcher; the orchestrator renames it per preset.

---

## Operator step — create `master_template.als` (one-time, ~1 min)

This step has to be done **once**, by hand, before Stage 2 runs.

1. Open Ableton Live 12. Create a **new empty project** (File → New
   Live Set).
2. **Delete all default tracks** so only the Main track remains:
   - Click each default MIDI / Audio track, press Cmd+Delete.
   - You should end up with only the Main on the right and no tracks
     in the central area.
3. On Main, drop your saved `tf_recorder.amxd` (from
   `~/Music/Ableton/User Library/Presets/Audio Effects/Max Audio
   Effect/tf_recorder.amxd`). The device should appear with the
   `plugin~ / plugout~ / sfrecord~ 2` patcher you built in Stage 1.
4. Verify Live's preferences are still set to **48 kHz** sample rate.
5. **Save As** → choose
   `/Users/mattharvey/Sites/tone-forge/backend/scripts/render_via_m4l/master_template.als`.
6. Quit Live (or leave it open — doesn't matter for the build step).

The file `master_template.als` must exist at the path above before you
run `build_per_preset_als.py`. The script fails-loud if it isn't there.

---

## Pipeline runbook (after master_template.als exists)

```bash
cd /Users/mattharvey/Sites/tone-forge/backend

# 1. Generate per-preset ALS files for Analog + Drift.
#    Output: preset_catalog_output/als_v2/<preset_id>.als (~188 files)
python3 scripts/render_via_m4l/build_per_preset_als.py \
    --instruments Analog Drift \
    --out-dir preset_catalog_output/als_v2

# 2. PILOT — render one Analog + one Drift preset to confirm the
#    orchestrator works before the full 188-preset batch.
python3 scripts/render_via_m4l/batch_render.py \
    --als-dir preset_catalog_output/als_v2 \
    --audio-dir preset_catalog_output/audio_v2 \
    --pilot

# 3. INSPECT the two pilot WAVs. Listen via afplay; confirm
#    they're distinct sounds and not silence / noise. If the pilot
#    fails, fix and re-run the pilot before the full batch.

# 4. FULL batch render — ~1 hour wall-clock at 120 BPM.
#    Live must be open and the foreground app for keystrokes to work.
python3 scripts/render_via_m4l/batch_render.py \
    --als-dir preset_catalog_output/als_v2 \
    --audio-dir preset_catalog_output/audio_v2

# 5. Integrity check — confirm 99 Analog + 89 Drift WAVs are
#    present, non-trivial size, audibly distinct.
ls -la preset_catalog_output/audio_v2 | wc -l   # expect 189 (188 + header)
```

---

## Risks / known fragilities

- **AppleScript focus**: the orchestrator sends `space` to play and
  stop. Live must be the foreground app. Don't touch the keyboard
  during the batch.
- **`live.thisdevice` only fires on device load**: switching ALS files
  reloads the entire project including the M4L device, so the
  recorder re-arms cleanly each time. Verified in Stage 1.
- **Staging-file race**: `sfrecord~` finalises on the `0` (stop)
  message. The orchestrator waits 250 ms of size stability after
  pressing Stop before reading the file. Conservative; should hold.
- **ID collisions when splicing MasterTrack**: the splice inserts
  Live-assigned IDs from `master_template.als` into a synthetic ALS
  whose IDs were assigned by `create_preset_als()`. Live is usually
  tolerant of overlapping IDs on track-level elements; if the pilot
  ALS fails to load, this is the first place to look.
- **`open <als>` adopting existing Live instance**: macOS `open`
  delegates to LaunchServices, which routes the file to the registered
  handler. Live always adopts the open instance. Verified manually.
