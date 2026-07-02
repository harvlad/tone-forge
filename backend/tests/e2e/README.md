# End-to-end verification harnesses

Playwright-driven scripts that boot a real headless Chromium
against a running dev server at http://127.0.0.1:8000 and
assert against a live analysis bundle.

These are NOT run by `pytest` (see the top-level test-runner
policy) — they require a live dev server and network. Run
manually as part of a shipping checklist.

## Prerequisites

```sh
pip install -r backend/requirements-dev.txt
python3 -m playwright install chromium
```

## Harnesses

| Script | What it checks |
|---|---|
| `verify_debug.py` | `/debug` visualizer loads, tag CSS tokens defined. 6 assertions. |
| `verify_jam_followups.py` | jam Rehearsal v2 follow-ups: BPM helper, warm-up bar, best-rep filename, skill map gate, debug tag visibility. 17 assertions. |
| `verify_jam_e2e.py` | Full end-to-end against real session `5fff8bd2` (Paramore, 129 BPM, 14 sections). 12 assertions covering skill-map auto-open, deep-link load, debug tag pills, BPM chip, WAV download. |

## Run

```sh
# In one terminal:
cd backend && python3 -m tone_forge_api

# In another:
python3 backend/tests/e2e/verify_debug.py
python3 backend/tests/e2e/verify_jam_followups.py
python3 backend/tests/e2e/verify_jam_e2e.py
```

Exit code 0 = all assertions passed. Non-zero = a check failed
(stdout will name it).
