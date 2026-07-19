# Outstanding Items

Deferred items from the full project review (2026-07). Everything else in the
35-item review plan is implemented, tested (backend 2915 passed / iOS full
suite green), and deployed.

## 1. TLS / domain — DONE (2026-07-11)

Domain: **jamn.app** (`.app` TLD is HSTS-preloaded, HTTPS mandatory).

- DNS apex A record → 62.238.54.81 (Hetzner VPS).
- nginx 1.28 fronts uvicorn; config at `/etc/nginx/sites-available/toneforge`
  (mirrors `backend/deploy/nginx-toneforge.conf`). HTTP → HTTPS 301.
- Let's Encrypt cert for `jamn.app` + `www.jamn.app` via `certbot --nginx`;
  auto-renew via `certbot.timer` (verified active).
- uvicorn now binds `127.0.0.1:8000` (systemd unit updated on server); ufw
  active allowing only SSH/80/443. Direct `:8000` access blocked.
- `TONEFORGE_ADMIN_TOKEN` set in `/opt/toneforge/.env` (verified: `/studio`
  404 without token, 200 with Bearer token).
- `Config.swift` → `https://jamn.app`; takedown email → `copyright@jamn.app`.
- `project.yml` ATS exceptions reduced to `localhost` only (local dev);
  project regenerated with XcodeGen.

Remaining nice-to-have: the GoDaddy Website Builder site previously attached
to the domain may still be cached by some resolvers until TTL expiry.

## 2. Legal documents (blocks public release)

**Status:** waiting on counsel-drafted text.

- `mobile-ios/Sources/ToneForgeMobile/Views/LegalSheets.swift` — Terms of
  Service and Privacy Policy are placeholders. Replace with counsel text.
- Register a DMCA agent with the US Copyright Office and add the takedown
  contact to the ToS / a `/legal` page on the backend.
- Privacy Policy must cover what `PrivacyInfo.xcprivacy` declares:
  UserDefaults, file timestamps, system boot time, user-uploaded audio
  ("other user content"); no tracking.

## 3. YouTube URL analysis (kept for dev, must stay off in production)

**Status:** intentionally kept for development song ingestion. ToS/DMCA risk
if publicly exposed.

- Endpoints: `POST /api/analyze-url`, `POST /api/analyze-url-stream`, and the
  YouTube waveform preview — all in `backend/tone_forge_api.py`.
- All three are gated by `_require_url_ingest()`: they return 404 unless the
  env flag `TONEFORGE_ENABLE_URL_INGEST=1` is set. Default is OFF.
- **Rule:** set the flag on dev machines only. Never set it on the production
  deployment. Revisit (remove or license-gate) before any public launch.

## 4. Desktop Live Beat + Beat Capture — silent playback (MITIGATED, awaiting runtime confirmation)

**Fixes landed (2026-07-17):**
- `SessionController.ensureEngineStarted()` now self-recovers: if the
  `engineStarted` flag is set but the underlying engine is not running
  (e.g. the ConnectCore reconfig retry loop exhausted and the engine went
  `.failed`), it clears the flag and restarts + rewires. Previously every
  trigger was silently dropped forever at ChopPlayer's `isRunning` guard.
- The two remaining *silent* drop points now log:
  `[ChopPlayer] dropped trigger: engine not running` and
  `[PackPadPlayer] dropped trigger: unknown pack/pad <id>/<idx>`.
- Next repro with the stderr harness (below) is now decisive: one of
  `[BeatCapture] beatkit resolve failed`, `[ChopPlayer] dropped trigger`,
  `[PackPadPlayer] dropped trigger`, `[ChopPlayer] failed to open`, or
  `[Session] engine flagged started but not running` will print.

Original investigation notes:

**Symptom (user, macOS Jamn dist build):**
- Beat Capture detects onsets fine (e.g. background TV speech → 12 hits in ~4s,
  classified Snare×9 / Perc×3), but **Preview / playback makes no sound**.
- Live Beat: taps register (envelope meter + hit counter move) but **no drum
  audio** after calibrating.
- So detection works; the **trigger → audio path is silent** on desktop.

**Ruled out by static analysis (all wiring correct in source):**
- `SessionController+BeatCapture.startLiveBeat()` calls `ensureBeatKitRegistered()`
  + `ensureEngineStarted()`; wires `onTriggerSample → triggerBeatKitSample →
  packPlayer.trigger(BeatKit.packId, role.padIdx, velocity)`.
- Beat Capture Preview (`BeatCaptureSheet.startPreview`) routes
  `SequencerPlayer → sequencerAdapter → playPackPad → packPlayer.trigger` — same
  packPlayer sink. `sequencerAdapter.packPlayer` IS wired (SessionController.swift:179).
- `DrumRole.padIdx` = 0..6, all present in beatkit manifest + pads.
- beatkit resources ARE bundled in the dist app:
  `dist/Jamn.app/Contents/Resources/JamDesktop_JamDesktopAudio.bundle/Samples/beatkit/pads/*.m4a`
  (all 7). `Bundle.module` name matches (`JamDesktop_JamDesktopAudio.bundle`).
- `SampleBank.loadBundled → loadFromDirectory` throws if any pad missing (no
  *partial* silent drop) — resolve either fully works or fully fails + logs
  `[BeatCapture] beatkit resolve failed`.
- `ChopPlayer.schedule` guards `avEngine.isRunning`; `chopPlayer.outputNode` set
  in `ensureEngineStarted`, and reattached on graph rebuild
  (SessionController.swift:166 `onGraphReattached`).
- ConnectCore `AudioEngine` has a config-change observer + auto-restart; desktop
  playback engine is that engine (`engine.engine.avEngine`).

**Prime remaining suspects (need RUNTIME confirmation):**
1. beatkit resolve fails at runtime in the packaged app (`Bundle.module` miss) →
   look for `[BeatCapture] beatkit resolve failed` in stderr.
2. Playback `avEngine` not running when trigger fires (two-engine device flap:
   LiveBeatTap's dedicated capture `AVAudioEngine` starting may post an
   `AVAudioEngineConfigurationChange` that stops the shared playback engine).
   NOTE: this would NOT explain Beat Capture Preview being silent (no capture
   engine running during Preview) — so if BOTH are silent, suspect #1 or the
   voice→musicBus edge, not the flap.
3. `[ChopPlayer] failed to open <url>` (m4a decode).

**Discriminator not yet answered:** do normal song **stems** play in the desktop
app? stems-play-but-beatkit-silent = packPlayer/beatkit path; all-silent =
musicBus/output.

**Diagnostic harness left running:** launch dist from terminal to capture stderr:
```
jam-desktop/dist/Jamn.app/Contents/MacOS/JamDesktop >/tmp/jamn-beat.log 2>&1 &
```
Then reproduce Preview + Live Beat, read `/tmp/jamn-beat.log` for the failing
branch. As of session end only the startup line was present (input-device note).

**When fixed:** propagate to both platforms if shared (iOS ModeCoordinator+BeatCapture
mirrors the desktop path). Live Beat = Tap controller (template match), not transcribe.

## 5. Desktop Beat Capture — false onsets from background noise (FIXED 2026-07-17)

TV speech in the background produced 12 "drum" hits in ~4s. Fixed with a
percussive gate in `BeatOnsetExtractor` (shared engine — both platforms):
an onset is rejected unless it has (a) fast attack (`attackSec ≤ 0.03`),
(b) decaying tail (last-quarter RMS ≤ 0.6 × peak RMS), and (c) relative
quiet before it (30 ms pre-onset RMS ≤ 0.5 × peak RMS — catches syllable
tail-ends that look percussive once the voice stops). Gate runs *before*
the global-peak noise floor so loud speech can't gate out real quiet hits.
Regression tests: `testSpeechOnlyProducesNoHits`,
`testHitsSurviveInterleavedSpeech` in `BeatOnsetExtractorTests`.

Still relevant: **macOS Voice-Isolation mic mode** flattens percussive
attacks — verify Mic Mode = Standard when testing real taps (a flattened
attack could now be gated out by design).

### Recent Live Beat commits this thread
- `22bea46` feat(livebeat): guided tap-along calibration (shared, both platforms)
- `3e2983c` fix(livebeat): re-arm between taps + guided input meter
  (root cause: tap `updateThresholds` off-ratio 0.25 sat below post-gain noise
  floor → detector latched after first hit; raised to 0.6 + config offThreshold
  mobile 0.024 / desktop 0.012. Regression test `LiveBeatOnsetDetectorTests`.)

## 6. Beat Capture — body-percussion classification (2026-07-18)

Chest/stomach "kicks" read as snares to the per-hit classifier — the
kick/snare distinction in a body-percussion take is *relative*, not
absolute. Landed (shared engine, both platforms):

- **Heuristic kick rescue** in `ModelBackedBeatClassifier`: a non-kick
  model verdict is overridden when the heuristic grades kick with
  confidence ≥ 0.5.
- **Relative role refinement** (`BeatOnsetExtractor.refineRelativeRoles`):
  2-means on spectral centroid over the kick/snare/perc cohort; the dark
  cluster upgrades to kick. Guards: cohort ≥ 6, cluster ≥ 2, brightness
  separation ≥ 1.3×, dark-cluster median loudness ≥ 0.5× bright (soft
  ghost notes are naturally darker — a quiet dark cluster is ghosts, not
  kicks).
- **"Detect kick" toggle** (persisted, `beatCaptureDetectKick`): kick
  takes and ghost-note takes proved statistically inseparable (loudness
  ratios 0.57 vs 0.59 on live captures), so performer intent is declared
  in the UI. Off = single-drum take: refinement skipped, kick verdicts
  become snares. Toggle available pre-record and in review (re-analyzes
  the held take without re-recording). Desktop + mobile sheets.
- **Ghost-note sensitivity**: military-drum ghosts carry ~1–2% of the
  accent's spectral flux (25:1 measured live) — the transient detector's
  6% peak-fraction gate swallowed them. `RecordingProcessor.transients`
  now takes a `peakFraction` parameter; beat capture passes 0.01
  (`BeatOnsetExtractor.fluxPeakFraction`), the sample-classification
  path keeps 0.06 (no percussive gate downstream — held tones would
  sprout jitter onsets, caught by `testHeldToneClassifiesAsSustainedNote`).
  Extractor's relative noise floor also halved (0.10 → 0.05).
- **Per-role velocity normalization**: loudest hit per role = accent
  (velocity 1); chest kicks no longer buried at ~0.15 by a global peak.

Verified on live desktop captures: kick+snare takes refine correctly;
toggle-off take = 20/20 snare with velocity spread; military-drum take
kept 34 hits incl. peaks at 4% of accent.

## Production hardening reminders (not blocking, already safe by default)

- `TONEFORGE_ADMIN_TOKEN` must be set in production — without it, `/studio`,
  `/api/admin/*`, `/api/debug/*` only accept direct loopback and reject
  proxied requests, but a token is the intended production posture.
- Upload cap tunable via `TONEFORGE_MAX_UPLOAD_MB` (default 500); nginx
  `client_max_body_size` should match.
