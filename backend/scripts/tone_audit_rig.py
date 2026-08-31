#!/usr/bin/env python3
"""Local blind-listening rig for the tone audit pack (Phase 0).

Serves the clips in a tone_audit pack for blind A/B judging and writes
verdicts to ``VERDICTS.json`` in the pack directory. Blind integrity:
``ANSWER_KEY.json`` is never served — open it on disk only after every
verdict is in.

- ``GET /``                    rig HTML
- ``GET /api/clips``           clip list + any saved verdicts
- ``POST /api/verdicts``       overwrite VERDICTS.json atomically
- ``GET /audio?clip=N&file=F`` audio, restricted to the pack dir and to
                               ref_target/X1/X2 (no key material)

Run (from backend/):
    python3 scripts/tone_audit_rig.py [--pack lab_data/tone_audit_v1]
then open the printed URL. Ctrl-C to stop.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
from pathlib import Path

RIG_HTML = Path(__file__).resolve().parent / "tone_audit_rig.html"
ALLOWED_FILES = {"ref_target.wav", "X1.wav", "X2.wav"}

PACK_DIR: Path = Path("lab_data/tone_audit_v1")


def _atomic_write(target: Path, payload: object) -> None:
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, target)


def _clips() -> list[dict]:
    out = []
    for d in sorted(PACK_DIR.iterdir()):
        if not (d.is_dir() and (d / "ref_target.wav").exists()):
            continue
        out.append({
            "name": d.name,
            "hasBlindPair": (d / "X1.wav").exists() and (d / "X2.wav").exists(),
        })
    return out


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: object, status: int = 200) -> None:
        self._send(status, "application/json",
                   json.dumps(payload).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self._send(200, "text/html; charset=utf-8", RIG_HTML.read_bytes())
            return

        if parsed.path == "/api/clips":
            verdicts = {}
            vfile = PACK_DIR / "VERDICTS.json"
            if vfile.exists():
                try:
                    verdicts = json.loads(vfile.read_text())
                except json.JSONDecodeError:
                    verdicts = {}
            self._send_json({"pack": str(PACK_DIR), "clips": _clips(),
                             "verdicts": verdicts})
            return

        if parsed.path == "/audio":
            qs = parse_qs(parsed.query)
            clip = (qs.get("clip") or [""])[0]
            fname = (qs.get("file") or [""])[0]
            # Blind integrity + path safety: only the three listening
            # files, only directly inside a pack clip dir.
            if fname not in ALLOWED_FILES or "/" in clip or ".." in clip:
                self._send(403, "text/plain", b"forbidden")
                return
            path = (PACK_DIR / clip / fname).resolve()
            try:
                path.relative_to(PACK_DIR.resolve())
            except ValueError:
                self._send(403, "text/plain", b"forbidden")
                return
            if not path.exists():
                self._send(404, "text/plain", b"not found")
                return
            self._send(200, "audio/wav", path.read_bytes())
            return

        self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/verdicts":
            self._send(404, "text/plain", b"not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"error": "bad json"}, status=400)
            return
        _atomic_write(PACK_DIR / "VERDICTS.json", payload)
        self._send_json({"ok": True})

    def log_message(self, fmt: str, *args) -> None:  # quiet
        pass


def main() -> None:
    global PACK_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="lab_data/tone_audit_v1")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()
    PACK_DIR = Path(args.pack).resolve()
    if not PACK_DIR.exists():
        raise SystemExit(f"pack dir not found: {PACK_DIR}")

    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(f"Tone audit rig: http://127.0.0.1:{args.port}/  "
              f"(pack: {PACK_DIR})")
        print("Verdicts save to VERDICTS.json. ANSWER_KEY.json is never "
              "served — open it only when done.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
