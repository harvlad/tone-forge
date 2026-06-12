# Devices

**Purpose**: Detect the user's playback path (device class, capabilities) and expose `DeviceCaps`. Adapters per device class live here too.

**Owner**: Platform & Engine attention pool.

**Status**: Landed. P7 MVP complete — `caps.py` exposes `DeviceCaps`; `discovery.py` wraps the CoreAudio + USB-MIDI probe (`connect devices --json`); `preferences.py` persists onboarding answers to `device.json`. `audio_input_name` plumbs end-to-end into Connect via `TONEFORGE_AUDIO_INPUT_NAME` (Swift-side `AudioEngine.applyPreferredInputDevice()`). Phase 2 (USB MIDI sysex probing, bidirectional preset apply, multi-rig profiles) deferred per EXECUTION_PLAN.md §8.
