"""Tiny local HTTP server backing the V3 / FX listening rig.

Serves:
- ``GET /``           the listening rig HTML (``listening_rig.html``)
- ``GET /api/v3``     V3 usefulness rating JSON
- ``GET /api/fx``     FX label audit JSON
- ``POST /api/v3``    overwrite V3 JSON atomically with request body
- ``POST /api/fx``    overwrite FX JSON atomically with request body
- ``GET /audio?path=ABS_PATH``  audio file (path-restricted to the
  ``preset_catalog_output/audio`` directory for safety)

Run: ``python3 scripts/listening_rig_server.py`` then open the printed URL.
Ctrl-C to stop. Listens on ``127.0.0.1:8765`` by default.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
import urllib.parse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIO_ROOT = (PROJECT_ROOT / "preset_catalog_output" / "audio").resolve()
V3_FILE = PROJECT_ROOT / (
    "preset_catalog_output/retrieval/v3_top5_usefulness_rating.json"
)
FX_FILE = PROJECT_ROOT / (
    "preset_catalog_output/retrieval/fx_label_audit_worksheet.json"
)
RIG_HTML = Path(__file__).resolve().parent / "listening_rig.html"


def _atomic_write(target: Path, payload: object) -> None:
    """Write JSON to ``target`` atomically (tmpfile + os.replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, target)


def _audio_path_allowed(p: Path) -> bool:
    try:
        p.resolve().relative_to(AUDIO_ROOT)
        return True
    except ValueError:
        return False


class Handler(http.server.BaseHTTPRequestHandler):
    # -- helpers -----------------------------------------------------------
    def _send_bytes(self, status: int, ctype: str, body: bytes,
                    extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_bytes(status, "application/json", body)

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (stdlib convention)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            if not RIG_HTML.exists():
                self.send_error(500, f"missing {RIG_HTML.name}")
                return
            self._send_bytes(200, "text/html; charset=utf-8",
                             RIG_HTML.read_bytes())
            return

        if path == "/api/v3":
            self._send_json(200, json.loads(V3_FILE.read_text()))
            return

        if path == "/api/fx":
            self._send_json(200, json.loads(FX_FILE.read_text()))
            return

        if path == "/audio":
            qs = urllib.parse.parse_qs(parsed.query)
            raw = qs.get("path", [""])[0]
            if not raw:
                self.send_error(400, "missing path")
                return
            p = Path(raw)
            if not _audio_path_allowed(p):
                self.send_error(403, "path outside audio root")
                return
            if not p.exists():
                self.send_error(404, "audio file not found")
                return
            self._send_bytes(
                200, "audio/wav", p.read_bytes(),
                extra_headers={"Accept-Ranges": "bytes",
                               "Cache-Control": "no-store"},
            )
            return

        self.send_error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"bad json: {exc}"})
            return

        if path == "/api/v3":
            _atomic_write(V3_FILE, payload)
            self._send_json(200, {"ok": True, "written": str(V3_FILE)})
            return
        if path == "/api/fx":
            _atomic_write(FX_FILE, payload)
            self._send_json(200, {"ok": True, "written": str(FX_FILE)})
            return

        self.send_error(404, "not found")

    # quieter logging
    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(
            f"  {self.command} {self.path} -> {format % args}\n"
        )


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    for required in (V3_FILE, FX_FILE, RIG_HTML):
        if not required.exists():
            print(f"ERROR: required file missing: {required}",
                  file=sys.stderr)
            return 2

    server = ThreadingServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Listening rig at {url}")
    print(f"  V3 file:    {V3_FILE}")
    print(f"  FX file:    {FX_FILE}")
    print(f"  audio root: {AUDIO_ROOT}")
    print("Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
