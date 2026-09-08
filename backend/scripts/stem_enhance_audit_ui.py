#!/usr/bin/env python3
"""Blind A/B listening UI for a stem-enhance audit pack.

Serves the pack produced by ``make_stem_enhance_audit.py``: per clip
X1/X2 (identities hidden — the server never reads ANSWER_KEY.json, so
it cannot leak). Adapted from ``tuner_audit_ui.py`` with the two
protocol differences this pack needs: no reference player (the
question is "which sounds better", not "closer to a ref"), and the
verdict vocabulary is better ∈ {X1, X2, none}.

    .venv/bin/python scripts/stem_enhance_audit_ui.py \
        [--pack lab_data/stem_enhance_audit_v1] [--port 8767]
    # open http://127.0.0.1:8767

Loopback only, resumable, one verdict per clip (re-judging a clip
overwrites its verdict line in verdicts.jsonl).
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _State:
    def __init__(self, pack: Path):
        self.pack = pack
        self.lock = threading.Lock()
        self.clips: List[str] = sorted(
            d.name for d in pack.iterdir()
            if d.is_dir() and (d / "X1.wav").is_file()
        )
        self.verdicts_path = pack / "verdicts.jsonl"
        self.verdicts: Dict[str, dict] = {}
        if self.verdicts_path.exists():
            for line in self.verdicts_path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                    self.verdicts[rec["clip"]] = rec
                except (json.JSONDecodeError, KeyError):
                    continue

    def save(self, rec: dict) -> None:
        with self.lock:
            self.verdicts[rec["clip"]] = rec
            with self.verdicts_path.open("w") as fh:
                for name in self.clips:
                    if name in self.verdicts:
                        fh.write(json.dumps(self.verdicts[name]) + "\n")


class _Handler(BaseHTTPRequestHandler):
    state: _State

    def log_message(self, *_a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        st = self.state
        if self.path == "/":
            body = _PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/state":
            with st.lock:
                done = dict(st.verdicts)
            self._json({"clips": st.clips, "verdicts": done})
            return
        if self.path.startswith("/audio/"):
            parts = self.path.split("/")  # /audio/<clip>/<file>
            if len(parts) == 4:
                clip, fname = Path(parts[2]).name, Path(parts[3]).name
                if clip in st.clips and fname in ("X1.wav", "X2.wav"):
                    data = (st.pack / clip / fname).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(data)))
                    # Packs regenerate under the same filenames — a
                    # cached X is a silently stale verdict.
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
            self._json({"error": "not found"}, 404)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        st = self.state
        if self.path != "/api/verdict":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, 400)
            return
        clip = payload.get("clip")
        better = payload.get("better")
        if clip not in st.clips or better not in ("X1", "X2", "none"):
            self._json({"error": "clip + better(X1|X2|none) required"}, 400)
            return
        st.save({
            "clip": clip,
            "better": better,
            "notes": str(payload.get("notes", ""))[:500],
        })
        self._json({"ok": True})


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Stem-enhance blind audit</title>
<style>
  :root { color-scheme: dark; }
  body { font: 15px/1.5 -apple-system, sans-serif; background: #111; color: #eee;
         max-width: 680px; margin: 3rem auto; padding: 0 1rem; }
  h1 { font-size: 1.1rem; color: #999; }
  .card { background: #1c1c1e; border-radius: 12px; padding: 1.1rem 1.3rem; margin: 1rem 0; }
  .name { font-weight: 600; font-size: 1.05rem; margin-bottom: .5rem; }
  .row { display: flex; align-items: center; gap: .6rem; margin: .35rem 0; }
  .row .label { width: 3rem; color: #999; font-size: .8rem; text-transform: uppercase; }
  audio { flex: 1; height: 2rem; }
  .buttons { display: flex; gap: .5rem; margin-top: .8rem; }
  button { flex: 1; padding: .55rem 0; border: 0; border-radius: 9px; cursor: pointer;
           color: #fff; font-weight: 600; background: #333; }
  button.sel { outline: 2px solid #8b7cf6; background: #3a3360; }
  input[type=text] { width: 100%; margin-top: .6rem; background: #2a2a2c; border: 0;
           border-radius: 8px; color: #eee; padding: .5rem .7rem; }
  #progress { color: #888; margin-bottom: 1rem; }
  .done { opacity: .55; }
</style>
<h1>Stem-enhance blind audit — which sounds BETTER (air / detail without
added hiss, crunch, or metallic edge)? Use good headphones.</h1>
<div id="progress"></div>
<div id="list"></div>
<script>
async function refresh() {
  const s = await (await fetch('/api/state')).json();
  const doneN = Object.keys(s.verdicts).length;
  document.getElementById('progress').textContent = `${doneN}/${s.clips.length} judged`;
  const list = document.getElementById('list');
  list.innerHTML = '';
  for (const clip of s.clips) {
    const v = s.verdicts[clip];
    const card = document.createElement('div');
    card.className = 'card' + (v ? ' done' : '');
    card.innerHTML = `
      <div class="name">${clip}</div>
      <div class="row"><span class="label">X1</span>
        <audio controls preload="none" src="/audio/${clip}/X1.wav"></audio></div>
      <div class="row"><span class="label">X2</span>
        <audio controls preload="none" src="/audio/${clip}/X2.wav"></audio></div>
      <div class="buttons">
        ${['X1','X2','none'].map(c =>
          `<button data-c="${c}" class="${v && v.better===c ? 'sel':''}">${
            {X1:'X1 better', X2:'X2 better', none:'No difference'}[c]
          }</button>`).join('')}
      </div>
      <input type="text" placeholder="notes (hiss / air / artifacts…)"
             value="${v && v.notes ? v.notes.replace(/"/g,'&quot;') : ''}">`;
    const notesEl = card.querySelector('input');
    card.querySelectorAll('button').forEach(btn => {
      btn.onclick = async () => {
        await fetch('/api/verdict', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({clip, better: btn.dataset.c, notes: notesEl.value})});
        refresh();
      };
    });
    list.appendChild(card);
  }
}
refresh();
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", type=Path,
                        default=_REPO_ROOT / "lab_data" / "stem_enhance_audit_v1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    state = _State(args.pack)
    if not state.clips:
        raise SystemExit(f"no clips in {args.pack}")
    _Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    print(f"[audit-ui] {len(state.clips)} clips — open http://127.0.0.1:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
