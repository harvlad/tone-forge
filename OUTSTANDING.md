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

## Production hardening reminders (not blocking, already safe by default)

- `TONEFORGE_ADMIN_TOKEN` must be set in production — without it, `/studio`,
  `/api/admin/*`, `/api/debug/*` only accept direct loopback and reject
  proxied requests, but a token is the intended production posture.
- Upload cap tunable via `TONEFORGE_MAX_UPLOAD_MB` (default 500); nginx
  `client_max_body_size` should match.
