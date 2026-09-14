#!/usr/bin/env bash
# Deploy the current branch (or $1) to the jamn.app VPS via git.
#
# What it does: fetch the ref on the server's /opt/toneforge repo,
# hard-checkout it (tracked files only — data/history/uploads are
# untracked/ignored and never touched), AUTO CACHE-BUST every static asset
# to the deployed commit hash, restart the toneforge service, verify health.
# Run from anywhere in the repo.
#
# Auto cache-busting (added 2026-09-15): the app pins static assets with a
# manual `?v=N` query (e.g. `kit.js?v=28`, and inside kit.js
# `import("./padengine.js?v=9")`). Bumping those by hand is error-prone —
# a padengine timing fix once shipped to the server but never reached the
# browser because `?v` was unchanged and import() caches modules by URL. So
# every deploy now rewrites ALL `.js`/`.css` `?v=` refs to the deployed short
# commit hash: the query changes on every code change, so browsers (and the
# ES-module cache) always fetch fresh code. The rewrite is re-applied cleanly
# each deploy because the force-checkout above resets the tracked files first.
set -euo pipefail
REF="${1:-$(git rev-parse --abbrev-ref HEAD)}"
KEY="$HOME/.ssh/toneforge_hetzner"
HOST="root@jamn.app"

echo "==> pushing $REF to origin"
git push origin "$REF"

echo "==> deploying $REF to jamn.app"
ssh -o BatchMode=yes -i "$KEY" "$HOST" "bash -s -- '$REF'" <<'REMOTE'
set -e
REF="$1"
cd /opt/toneforge
git fetch -q --depth 1 origin "$REF"
git checkout -qf FETCH_HEAD
HASH=$(git rev-parse --short HEAD)
git log -1 --format='deployed: %h %s'
# Auto cache-bust: point every .js/.css ?v= asset ref at the deployed hash.
find backend/static -type f \( -name '*.html' -o -name '*.js' \) -print0 \
  | xargs -0 --no-run-if-empty \
      sed -i -E "s/\.(js|css)\?v=[A-Za-z0-9._-]+/.\1?v=${HASH}/g"
echo "cache-bust -> ?v=${HASH}"
chown -R toneforge:toneforge /opt/toneforge/backend
systemctl restart toneforge
sleep 8
systemctl is-active toneforge
REMOTE

echo "==> health"
curl -s -m 8 https://jamn.app/api/health && echo
echo "==> done"
