#!/usr/bin/env python3
"""Browser-based labeling UI for the tone-retrieval calibration corpus.

Same contract as ``label_tone_clips.py`` (the CLI labeler) — walks the
clip corpus, shows the top-1 preset match, records y/n to the same
JSONL the fitter consumes. The two tools are interchangeable mid-run:
both read the JSONL to resume, both append identical records, so a
session can start in one and finish in the other.

Why a second front-end exists at all: a 90-clip session lives or dies
on operator ergonomics. The browser gives inline audio players for the
clip AND the matched preset's reference render (A/B with two spacebars
instead of juggling afplay windows), keyboard y/n/s, and a progress
bar. The labeling *semantics* stay in the CLI module — this file
imports its helpers rather than re-implementing them, so the record
schema cannot drift between the two.

Run:

    .venv/bin/python scripts/label_tone_clips_ui.py
    # then open http://127.0.0.1:8765

Serves 127.0.0.1 only — this is an operator tool, never a network
service.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_REPO_ROOT))

import label_tone_clips as cli  # noqa: E402  (sibling script = shared schema)

_AUDIO_MIME = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}


def _match_guitar(clip: Path) -> List[dict]:
    """Match a clip against the 5-chain guitar monitor bank.

    Adapts ``guitar_catalog.recommend`` output to the same dict shape
    ``match_audio_file`` returns, so the rest of the UI is
    matcher-agnostic. Uses the public debug ranking rather than the
    private scoring internals — same distances the production tone
    card sees, which is the whole point: labels collected here
    calibrate the card's confidence, not the synth preset surface.
    """
    from tone_forge.tone import guitar_catalog as gc

    rec = gc.recommend(clip)
    ranking = (rec.debug or {}).get("ranking") or ()
    if not ranking:
        raise RuntimeError(rec.rationale or "guitar_catalog returned no ranking")
    names = {e.chain_id: (e.display_name, e.family) for e in gc._get_catalog().entries}
    out = []
    for row in ranking:
        chain_id = row["chain_id"]
        display_name, family = names.get(chain_id, (chain_id, ""))
        render = gc._CHAINS_ROOT / f"{chain_id}.wav"
        out.append({
            "preset_id": chain_id,
            "preset_name": display_name,
            "instrument": "guitar",
            "category": getattr(family, "value", str(family)),
            "sound_type": "monitor chain",
            "distance": float(row["distance"]),
            "audio_path": (
                str(render.relative_to(_REPO_ROOT)) if render.exists() else None
            ),
        })
    return out


class _State:
    """All mutable session state, guarded by one lock.

    The match cache is filled by a single prefetch thread walking the
    corpus in the same sorted order the UI serves it, so by the time
    the operator reaches clip N its fingerprint is usually already
    computed — extraction latency hides behind listening time.
    """

    def __init__(self, clips_dir: Path, labels_path: Path, instrument: str,
                 matcher: str):
        self.clips_dir = clips_dir
        self.labels_path = labels_path
        self.instrument = instrument
        self.matcher = matcher
        self.lock = threading.Lock()
        self.clips: List[Path] = list(cli._iter_clips(clips_dir))
        self.labeled: set = cli._already_labeled(labels_path)
        self.skipped: set = set()          # session-only, like the CLI
        self.matches: Dict[str, object] = {}   # clip_path -> list | Exception

    def pending(self) -> List[Path]:
        with self.lock:
            return [
                p for p in self.clips
                if str(p) not in self.labeled and str(p) not in self.skipped
            ]

    def prefetch_loop(self) -> None:
        if self.matcher == "guitar":
            run = _match_guitar
        else:
            from tone_forge.preset_catalog.preset_retrieval import match_audio_file

            def run(clip: Path) -> List[dict]:
                return match_audio_file(clip, k=3, instrument=self.instrument)

        for clip in self.clips:
            key = str(clip)
            with self.lock:
                if key in self.labeled or key in self.matches:
                    continue
            try:
                result = run(clip)
            except Exception as exc:  # surfaced to the UI per clip
                result = exc
            with self.lock:
                self.matches[key] = result


def _match_payload(state: _State, clip: Path) -> Optional[dict]:
    with state.lock:
        result = state.matches.get(str(clip))
    if result is None:
        return None  # still computing
    if isinstance(result, Exception):
        return {"error": str(result)}
    if not result:
        return {"error": "no matches returned"}
    top = result[0]

    def _entry(m: dict) -> dict:
        audio = m.get("audio_path")
        has_preview = bool(audio) and (_REPO_ROOT / audio).exists()
        return {
            "preset_id": m.get("preset_id", "<unknown>"),
            "preset_name": m.get("preset_name", m.get("preset_id", "?")),
            "category": m.get("category", ""),
            "sound_type": m.get("sound_type", ""),
            "distance": float(m["distance"]),
            "preview_url": (
                f"/audio/preset/{m.get('preset_id')}" if has_preview else None
            ),
        }

    return {"top": _entry(top), "alternates": [_entry(m) for m in result[1:]]}


class _Handler(BaseHTTPRequestHandler):
    state: _State  # injected

    # -- helpers ---------------------------------------------------------
    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        mime = _AUDIO_MIME.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args) -> None:  # keep the terminal quiet
        pass

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        st = self.state
        if self.path == "/":
            body = _PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/next":
            pending = st.pending()
            with st.lock:
                done = len(st.labeled)
            total = len(st.clips)
            if not pending:
                self._json({"finished": True, "done": done, "total": total})
                return
            clip = pending[0]
            match = _match_payload(st, clip)
            self._json({
                "finished": False,
                "done": done,
                "total": total,
                "clip": {
                    "path": str(clip),
                    "name": clip.name,
                    "url": f"/audio/clip/{clip.name}",
                },
                "computing": match is None,
                "match": match,
            })
            return

        if self.path.startswith("/audio/clip/"):
            name = Path(self.path[len("/audio/clip/"):]).name  # no traversal
            candidates = [p for p in st.clips if p.name == name]
            if not candidates:
                self._json({"error": "unknown clip"}, 404)
                return
            self._file(candidates[0])
            return

        if self.path.startswith("/audio/preset/"):
            preset_id = self.path[len("/audio/preset/"):]
            with st.lock:
                for result in st.matches.values():
                    if isinstance(result, list):
                        for m in result:
                            if m.get("preset_id") == preset_id and m.get("audio_path"):
                                path = _REPO_ROOT / m["audio_path"]
                                if path.exists():
                                    self._file(path)
                                    return
            self._json({"error": "no preview"}, 404)
            return

        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        st = self.state
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, 400)
            return
        clip_path = payload.get("clip_path", "")
        if clip_path not in {str(p) for p in st.clips}:
            self._json({"error": "unknown clip"}, 400)
            return

        if self.path == "/api/skip":
            with st.lock:
                st.skipped.add(clip_path)
            self._json({"ok": True})
            return

        if self.path == "/api/label":
            label = payload.get("label")
            preset_id = payload.get("preset_id")
            distance = payload.get("distance")
            if label not in (0, 1) or not isinstance(preset_id, str):
                self._json({"error": "label must be 0/1 with preset_id"}, 400)
                return
            with st.lock:
                if clip_path in st.labeled:
                    self._json({"ok": True, "already": True})
                    return
                cli._append_label(
                    st.labels_path, Path(clip_path), preset_id,
                    float(distance), int(label),
                )
                st.labeled.add(clip_path)
            self._json({"ok": True})
            return

        self._json({"error": "not found"}, 404)


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Tone calibration labeler</title>
<style>
  :root { color-scheme: dark; }
  body { font: 15px/1.5 -apple-system, sans-serif; background: #111; color: #eee;
         max-width: 640px; margin: 3rem auto; padding: 0 1rem; }
  h1 { font-size: 1.1rem; color: #999; font-weight: 600; }
  .card { background: #1c1c1e; border-radius: 12px; padding: 1.2rem 1.4rem; margin: 1rem 0; }
  .label { font-size: .75rem; text-transform: uppercase; letter-spacing: .06em; color: #888; }
  .name { font-size: 1.15rem; font-weight: 600; margin: .15rem 0 .6rem; }
  .meta { color: #9a9aa0; font-size: .85rem; margin-bottom: .5rem; }
  audio { width: 100%; margin-top: .3rem; }
  .buttons { display: flex; gap: .6rem; margin-top: 1.2rem; }
  button { flex: 1; font-size: 1rem; padding: .7rem 0; border: 0; border-radius: 10px;
           cursor: pointer; color: #fff; font-weight: 600; }
  .yes { background: #2e7d46; } .no { background: #a13535; } .skip { background: #444; }
  button:active { filter: brightness(1.2); }
  #progress { height: 6px; background: #2a2a2c; border-radius: 3px; overflow: hidden; margin: .8rem 0; }
  #bar { height: 100%; background: #8b7cf6; width: 0; transition: width .3s; }
  #status { color: #888; font-size: .85rem; }
  .alts { font-size: .8rem; color: #77777d; margin-top: .6rem; }
  kbd { background: #2a2a2c; border-radius: 4px; padding: 0 .35em; font-size: .8em; }
</style>
<h1>Tone calibration labeler</h1>
<div id="progress"><div id="bar"></div></div>
<div id="status">loading…</div>
<div id="app"></div>
<script>
let cur = null;
async function refresh() {
  const r = await fetch('/api/next'); const s = await r.json();
  document.getElementById('bar').style.width = (100 * s.done / s.total) + '%';
  const status = document.getElementById('status');
  const app = document.getElementById('app');
  if (s.finished) {
    status.textContent = `done — ${s.done}/${s.total} labeled`;
    app.innerHTML = '<div class="card">Corpus fully labeled. Run <code>scripts/fit_tone_calibration.py</code>.</div>';
    return;
  }
  status.textContent = `${s.done}/${s.total} labeled — ${s.clip.name}`;
  if (s.computing) {
    app.innerHTML = '<div class="card">Fingerprinting clip…</div>';
    setTimeout(refresh, 1500); return;
  }
  if (s.match.error) {
    cur = { clip_path: s.clip.path };
    app.innerHTML = `<div class="card">fingerprint failed: ${s.match.error}
      <div class="buttons"><button class="skip" onclick="skip()">Skip (s)</button></div></div>`;
    return;
  }
  const m = s.match.top;
  cur = { clip_path: s.clip.path, preset_id: m.preset_id, distance: m.distance };
  const alts = (s.match.alternates || [])
    .map(a => `${a.preset_name} (d=${a.distance.toFixed(2)})`).join(' · ');
  app.innerHTML = `
    <div class="card">
      <div class="label">Clip</div>
      <div class="name">${s.clip.name}</div>
      <audio controls autoplay src="${s.clip.url}"></audio>
    </div>
    <div class="card">
      <div class="label">Top match — is this the right sound?</div>
      <div class="name">${m.preset_name}</div>
      <div class="meta">${m.category} · ${m.sound_type} · distance ${m.distance.toFixed(3)}</div>
      ${m.preview_url ? `<audio controls src="${m.preview_url}"></audio>` : '<div class="meta">no preview render</div>'}
      <div class="alts">${alts ? 'runners-up: ' + alts : ''}</div>
      <div class="buttons">
        <button class="yes" onclick="mark(1)">Correct (y)</button>
        <button class="no" onclick="mark(0)">Wrong (n)</button>
        <button class="skip" onclick="skip()">Skip (s)</button>
      </div>
      <div class="meta" style="margin-top:.8rem">keys: <kbd>y</kbd> <kbd>n</kbd> <kbd>s</kbd>,
        <kbd>1</kbd>/<kbd>2</kbd> replay clip/match</div>
    </div>`;
}
async function mark(label) {
  if (!cur || !cur.preset_id) return;
  await fetch('/api/label', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({...cur, label})});
  refresh();
}
async function skip() {
  if (!cur) return;
  await fetch('/api/skip', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({clip_path: cur.clip_path})});
  refresh();
}
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'AUDIO') return;
  if (e.key === 'y') mark(1);
  if (e.key === 'n') mark(0);
  if (e.key === 's') skip();
  const players = document.querySelectorAll('audio');
  if (e.key === '1' && players[0]) { players[0].currentTime = 0; players[0].play(); }
  if (e.key === '2' && players[1]) { players[1].currentTime = 0; players[1].play(); }
});
refresh();
</script>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--clips-dir", type=Path, default=cli._DEFAULT_CLIPS_DIR)
    parser.add_argument(
        "--labels", type=Path, default=None,
        help="labels JSONL (default depends on --matcher; the two "
             "surfaces must never share a file — their distance "
             "scales are incompatible)",
    )
    parser.add_argument(
        "--matcher", choices=("guitar", "preset"), default="guitar",
        help="'guitar' scores the 5-chain monitor bank behind the Jam "
             "tone card; 'preset' scores the synth preset catalog "
             "(the CLI labeler's surface). Default: guitar.",
    )
    parser.add_argument(
        "--instrument", default="Analog",
        help="preset-matcher catalog instrument (ignored for guitar)",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    labels_path = args.labels or (
        _REPO_ROOT / "data" / "tone_calibration_labels_guitar.jsonl"
        if args.matcher == "guitar" else cli._DEFAULT_LABELS
    )
    state = _State(args.clips_dir, labels_path, args.instrument, args.matcher)
    if not state.clips:
        raise SystemExit(f"no audio clips found in {args.clips_dir}")

    threading.Thread(target=state.prefetch_loop, daemon=True).start()

    _Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    print(f"[labeler-ui] {len(state.clips)} clips, "
          f"{len(state.labeled)} already labeled")
    print(f"[labeler-ui] open http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[labeler-ui] stopped; labels are already on disk")


if __name__ == "__main__":
    main()
