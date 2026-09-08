#!/usr/bin/env python3
"""Set a TestFlight build's "What to Test" from mobile-ios/WhatToTest.en-US.txt.

Enforces the CLAUDE.md hard rule automatically: after an upload, stamp the
latest processed build's beta test notes with the committed file, so testers
never see "bug fixes and improvements".

Auth — App Store Connect API key (you generate it once, we never store it):
  App Store Connect → Users and Access → Integrations → App Store Connect
  API → generate a key with the "App Manager" role → download the .p8 ONCE.
  Note the Key ID and Issuer ID shown there.

  Put the .p8 somewhere outside the repo and point the env at it:
    export ASC_KEY_ID=XXXXXXXXXX
    export ASC_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    export ASC_KEY_PATH=~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8

Usage:
    scripts/set_testflight_notes.py                 # latest build, this bundle
    scripts/set_testflight_notes.py --build 748     # a specific build number

Idempotent: creates the en-US betaBuildLocalization if absent, else updates it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import jwt  # PyJWT
import requests

BUNDLE_ID = "com.harvlad.toneforge.mobile"
NOTES_FILE = Path(__file__).resolve().parent.parent / "mobile-ios" / "WhatToTest.en-US.txt"
API = "https://api.appstoreconnect.apple.com/v1"


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _token() -> str:
    key_id = os.environ.get("ASC_KEY_ID")
    issuer = os.environ.get("ASC_ISSUER_ID")
    key_path = os.environ.get("ASC_KEY_PATH")
    if not (key_id and issuer and key_path):
        _die("set ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_PATH (see file header)")
    p8 = Path(os.path.expanduser(key_path))
    if not p8.exists():
        _die(f"key file not found: {p8}")
    payload = {
        "iss": issuer,
        "iat": int(time.time()),
        "exp": int(time.time()) + 20 * 60,
        "aud": "appstoreconnect-v1",
    }
    return jwt.encode(payload, p8.read_text(), algorithm="ES256",
                      headers={"kid": key_id, "typ": "JWT"})


def _get(sess, url, **params):
    r = sess.get(url, params=params or None)
    if r.status_code != 200:
        _die(f"GET {url} → {r.status_code}: {r.text[:300]}")
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", help="build number (default: latest processed)")
    args = ap.parse_args()

    if not NOTES_FILE.exists():
        _die(f"missing {NOTES_FILE} — the hard rule requires it")
    notes = NOTES_FILE.read_text().strip()
    if len(notes) < 20:
        _die("WhatToTest.en-US.txt looks empty/stale — update it for this build")
    # ASC caps whatsNew at 4000 chars.
    notes = notes[:4000]

    sess = requests.Session()
    sess.headers["Authorization"] = f"Bearer {_token()}"

    apps = _get(sess, f"{API}/apps", **{"filter[bundleId]": BUNDLE_ID})
    data = apps.get("data") or []
    if not data:
        _die(f"no app for bundle {BUNDLE_ID} on this account")
    app_id = data[0]["id"]

    builds = _get(sess, f"{API}/builds",
                  **{"filter[app]": app_id, "sort": "-version",
                     "limit": "20"})
    blist = builds.get("data") or []
    if not blist:
        _die("no builds found")
    if args.build:
        match = [b for b in blist if b["attributes"].get("version") == args.build]
        if not match:
            _die(f"build {args.build} not in the latest 20")
        build = match[0]
    else:
        build = blist[0]
    build_id = build["id"]
    ver = build["attributes"].get("version")

    # Existing en-US localization for this build?
    locs = _get(sess, f"{API}/builds/{build_id}/betaBuildLocalizations")
    en = next((x for x in (locs.get("data") or [])
               if x["attributes"].get("locale") == "en-US"), None)

    if en:
        r = sess.patch(
            f"{API}/betaBuildLocalizations/{en['id']}",
            json={"data": {"id": en["id"], "type": "betaBuildLocalizations",
                           "attributes": {"whatsNew": notes}}})
        action = "updated"
    else:
        r = sess.post(
            f"{API}/betaBuildLocalizations",
            json={"data": {"type": "betaBuildLocalizations",
                           "attributes": {"locale": "en-US", "whatsNew": notes},
                           "relationships": {"build": {"data": {
                               "type": "builds", "id": build_id}}}}})
        action = "created"
    if r.status_code not in (200, 201):
        _die(f"set notes → {r.status_code}: {r.text[:300]}")
    print(f"ok: {action} What-to-Test on build {ver} ({len(notes)} chars)")


if __name__ == "__main__":
    main()
