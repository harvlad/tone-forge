# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Tone Forge — AI audio analysis + tone recreation, shipping under the product
name **Jamn** (`https://jamn.app`). Upload audio → stem separation, MIDI
extraction, chord/section understanding, gear recommendations, and DAW-ready
exports. A monorepo: one Python backend plus five native clients that share
algorithms rather than re-implement them.

## Authoritative documents (read before planning work)

| Doc | Role |
|---|---|
| `EXECUTION_PLAN.md` | **The execution surface.** Supersedes every `backend/*.md` strategy/RCA/roadmap doc. §0 Completion Log is ground truth for "what actually shipped". |
| `OUTSTANDING.md` | Deferred items from the full project review, with status. |
| `docs/README.md` | Documentation policy (see below). |
| `mobile-ios/DECISIONS.md`, `jam-desktop/DECISIONS.md` | Chronological decision logs. Never delete an entry — supersede it with a new one that references the old. |

Strategy is frozen. Do **not** add new strategy documents. RCA/debugging notes
for frozen subsystems go in commit messages, not new markdown. New docs for
active subsystems live next to the code (e.g. `backend/tone_forge/monitor/README.md`).
`docs/_archive/` is historical — do not extend it.

## Repo map

```
backend/          Python 3.10+ / FastAPI — analysis, jobs, auth, exports, web UI
  tone_forge/       ~288 modules; subsystem packages (see boundary rules)
  tone_forge_api.py ~8.4k lines, ~103 routes + WS bridge — the ONLY composition point
  local_engine/     GPU worker, RunPod autoscaler, tray app, plugin scanner
  lab/, lab_data/   Riley Data Factory (corpus manufacture, separator R&D)
  static/           web UI: jam.html (home), index.html (guitar), studio.html (admin), debug.html
  tests/            231 test files, pytest
connect/          Swift (macOS 12+) — CoreAudio companion: low-latency monitoring, tone chains
jam-desktop/      Swift/SwiftUI (macOS 14+) — native Jam app; Core/Audio/App targets
jam-app/          DEPRECATED WKWebView wrapper. No new features; removed at jam-desktop parity.
mobile-ios/       Swift/SwiftUI (iOS 17+) — ToneForgeEngine / ToneForgeML / ToneForgeMobile
plugin/           C++20 / JUCE 8 — "jamn Kit" AU + VST3 + Standalone sampler
tools/            BeatModelTrainer (Swift/CoreML), handrig (Python/Blender hand-rig research)
scripts/          deploy + beat-model train/publish shell scripts
```

Sharing is by path dependency, not duplication: `jam-desktop` depends on
`connect` (ConnectCore) and `mobile-ios` (ToneForgeEngine). The iOS
`ToneForgeEngine` is the Swift port of `backend/static/launchpad.js` — a change
to one should land in the same commit as the other so drift stays visible.

## Commands

Backend (run from `backend/`):
```bash
pip install -r requirements.txt -r requirements-dev.txt
./start_server.sh                       # uvicorn --reload :8000, ONNX logging suppressed
uvicorn tone_forge_api:app --port 8000  # plain
python -m pytest tests -q               # full suite (as CI runs it)
ruff check tone_forge tone_forge_api.py local_engine tests cli.py
python cli.py path/to/clip.wav --hardware helix
```

Swift packages:
```bash
cd connect      && swift build && swift test          # needs full Xcode for XCTest
cd jam-desktop  && swift run JamDesktop ; swift test
cd mobile-ios   && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift test
cd mobile-ios   && xcodegen generate && xcodebuild test -project ToneForgeMobile.xcodeproj \
                     -scheme ToneForgeMobileApp -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```
`swift test` needs Xcode's toolchain — Command Line Tools ship no XCTest, and
mixing toolchains produces incompatible `.swiftmodule` errors. Set
`DEVELOPER_DIR` (or `xcode-select -s`) and stay on one.

Plugin:
```bash
cd plugin && cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --target JamnKit_All -j
```

Packaging: `jam-desktop/build_app.sh`, `jam-app/build_app.sh`,
`connect/build_release.sh`, `plugin/scripts/package.sh`.

## Architecture rules (enforced)

From `EXECUTION_PLAN.md` §1–§2:

1. One codebase. No rewrites, no microservices, no queues.
2. `tone_forge_api.py` is the only composition point between subsystems.
3. Subsystems communicate **exclusively** through `tone_forge/contracts.py`
   DTOs — frozen dataclasses, `str`-subclass enums, seconds as `*_s`,
   confidences in `[0, 1]`. Add the field to `contracts.py` before adding it
   to any subsystem signature.
4. No subsystem imports another subsystem's internals.
   `tests/test_subsystem_boundaries.py` enforces this with an AST walk and
   fails CI on violation.
5. **Frozen packages get bug fixes only** — no new files, no benchmarks, no
   docs. Frozen: `midi/`, `preset_catalog/`, `reconstruction/`,
   `evaluation/`, plus the retrieval algorithm/embeddings and ALS export.
   Only a package's designated wrapper adapter may import it (e.g. only
   `devices.ableton` imports `als_template`).
6. The WS protocol is versioned — every message carries `{"v": 1, ...}`.

Active workstreams per the priority table: subsystem boundary freeze, Connect
hardening, retrieval confidence calibration, song understanding expansion.

## Deployment

Production is a Hetzner VPS at `jamn.app`: nginx (TLS, Let's Encrypt) →
uvicorn bound to `127.0.0.1:8000` via `backend/deploy/toneforge.service`.
The service runs under `ProtectSystem=strict`, so anything that writes
(`HOME`, `XDG_CACHE_HOME`, `NUMBA_CACHE_DIR`) is redirected into
`backend/data/`. Never bind uvicorn to `0.0.0.0` — it bypasses every
proxy-level protection.

The prod box has no GPU. Deep analysis runs on remote workers that dial **out**
over HTTPS and claim jobs (`/api/engine/claim`), either
`local_engine/remote_worker.py` on a developer Mac or RunPod pods created
on demand by `local_engine/runpod_autoscaler.py` (scale-to-zero, inert unless
`RUNPOD_AUTOSCALE=1`). Stems land in Cloudflare R2 (zero egress); accounts in
Postgres with an in-memory fallback so dev/tests need no infra.

## Environment variables

Backend config is optional-by-default: unset means a working local dev mode.
Common ones — `TONEFORGE_ADMIN_TOKEN` (gates `/studio`, `/api/admin/*`,
`/api/debug/*`; unset means loopback-only, and any forwarding header is
rejected so a misconfigured nginx can't expose them),
`TONEFORGE_ENGINE_TOKEN`, `TONEFORGE_BACKEND_URL`, `TONEFORGE_ANALYSIS_ENGINE`,
`DATABASE_URL`, `R2_*`, `RUNPOD_*`, `RESEND_API_KEY`.

**`TONEFORGE_ENABLE_URL_INGEST` must stay unset in production.** It gates
`/api/analyze-url`, `/api/analyze-url-stream`, and the YouTube waveform
preview (they 404 without it) — kept for dev song ingestion only; ToS/DMCA
risk if publicly exposed. See `OUTSTANDING.md` §3.

Tests set `TONEFORGE_DISABLE_RETENTION=1` in `tests/conftest.py` before the
app imports; keep that ordering if you touch conftest.

## Conventions

- **Commits:** `type(scope): summary` — e.g. `fix(backend):`, `feat(mobile):`,
  `ci(plugin):`, `feat(plugin+desktop):`. Body explains the failure mode and
  what changed, in prose plus a short bullet list. Commit messages carry the
  RCA that no longer goes in markdown, so write them properly.
- **Ruff gate is deliberately narrow** (`E9,F63,F7,F82` — syntax errors,
  undefined names, comparison bugs). The legacy codebase has too many style
  violations to gate on the full set. Do not "fix" unrelated style noise;
  expand the select list only as modules are genuinely cleaned up.
- Comments in this codebase explain *why* — the trade-off, the failure mode
  that motivated the code. Match that density; do not narrate the obvious.
- CI: `backend-tests` (ruff + pytest on ubuntu), `connect-tests` (macos-14),
  plus tag-triggered release pipelines (`connect-v*`, `jamnkit-v*`) and the
  beat-model publish workflow. Releases are signed + notarized with no
  "ship it anyway" path.

## Gotchas

- `demucs` model weights (~4GB) download on first deep analysis; `ffmpeg` is
  required for URL ingest and transcoding.
- Riley corpora and checkpoints are pinned by
  `corpus_hash ↔ checkpoint_sha ↔ campaign_id`. Promotion is blind-gated on
  real audio, never a metric alone.
- Legal placeholders in `mobile-ios/.../LegalSheets.swift` block public
  release (`OUTSTANDING.md` §2).
- `README.md` still has stale corners outside the (now-corrected) layout
  section: its Architecture list names `pedal_translator.py`,
  `bass_translator.py` and `drum_translator.py`, none of which exist, and its
  Development section suggests `mypy` + `black`, neither of which is installed
  or configured anywhere — the lint gate is ruff. Trust this file and
  `EXECUTION_PLAN.md` over the README.
