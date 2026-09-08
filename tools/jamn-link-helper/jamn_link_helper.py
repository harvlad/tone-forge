#!/usr/bin/env python3
"""Jamn Link Helper — bridges an Ableton Link session into jamn.app web.

Browsers cannot join Link (LAN UDP multicast; no UDP in the web
platform). This helper runs on the SAME machine/LAN as the Link peers
(DJ gear, Ableton, TR-8S, ...), joins the session natively, and posts
tempo + beat to the backend, which fans it out to browsers over SSE.
The web Jam page then follows Link tempo.

INTERNAL TESTING tool. Accuracy through the relay is ~tens of ms —
jam-along grade, not pro beatmatching.

Setup (macOS or Windows, Python 3.10+):
    pip install aalink requests
    python jamn_link_helper.py                     # default https://jamn.app
    python jamn_link_helper.py --backend http://127.0.0.1:8000
    TONEFORGE_LINK_TOKEN=... python jamn_link_helper.py   # if the backend gates

Stop with Ctrl-C. While running, every Link tempo change (and a 1 Hz
keepalive) reaches the backend; browsers show a Link chip and can
enable "Follow Link".
"""
import argparse
import asyncio
import os
import sys
import time

try:
    import aalink
except ImportError:
    sys.exit("pip install aalink requests   (prebuilt wheels for mac + win)")
try:
    import requests
except ImportError:
    sys.exit("pip install requests")


async def run(backend: str, token: str | None) -> None:
    loop = asyncio.get_running_loop()
    link = aalink.Link(120, loop)
    link.enabled = True
    print(f"[link-helper] joined Link, posting to {backend}")
    session = requests.Session()
    headers = {"x-link-token": token} if token else {}
    last_post = 0.0
    last_bpm = 0.0
    fails = 0
    while True:
        # Wake on the beat so the posted beat number is freshly aligned;
        # sync(1) resolves at the next whole Link beat.
        try:
            beat = await link.sync(1)
        except Exception:
            await asyncio.sleep(0.25)
            continue
        bpm = float(link.tempo)
        now = time.time()
        # Post on tempo change immediately, else 1 Hz keepalive — the
        # backend marks state stale after 5 s of silence.
        if abs(bpm - last_bpm) < 0.01 and now - last_post < 1.0:
            continue
        try:
            r = session.post(
                f"{backend}/api/link/state",
                json={"bpm": bpm, "beat": float(beat),
                      "peers": int(getattr(link, "num_peers", 0) or 0)},
                headers=headers, timeout=3,
            )
            r.raise_for_status()
            if abs(bpm - last_bpm) >= 0.01:
                print(f"[link-helper] tempo {bpm:.2f} bpm")
            last_bpm = bpm
            last_post = now
            fails = 0
        except Exception as exc:
            fails += 1
            if fails in (1, 10) or fails % 60 == 0:
                print(f"[link-helper] post failed ({exc}); retrying")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="https://jamn.app")
    args = ap.parse_args()
    token = os.environ.get("TONEFORGE_LINK_TOKEN")
    try:
        asyncio.run(run(args.backend.rstrip("/"), token))
    except KeyboardInterrupt:
        print("\n[link-helper] bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
