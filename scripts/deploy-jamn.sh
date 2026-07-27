#!/usr/bin/env bash
# Deploy the current branch (or $1) to the jamn.app VPS via git.
#
# What it does: fetch the ref on the server's /opt/toneforge repo,
# hard-checkout it (tracked files only — data/history/uploads are
# untracked/ignored and never touched), restart the toneforge service,
# verify health. Run from anywhere in the repo.
#
# Usage:
#   scripts/deploy-jamn.sh                 # deploys current local branch
#   scripts/deploy-jamn.sh feat/ui-refactor
set -euo pipefail
REF="${1:-$(git rev-parse --abbrev-ref HEAD)}"
KEY="$HOME/.ssh/toneforge_hetzner"
HOST="root@jamn.app"

echo "==> pushing $REF to origin"
git push origin "$REF"

echo "==> deploying $REF to jamn.app"
ssh -o BatchMode=yes -i "$KEY" "$HOST" "set -e
cd /opt/toneforge
git fetch -q --depth 1 origin '$REF'
git checkout -qf FETCH_HEAD
git log -1 --format='deployed: %h %s'
chown -R toneforge:toneforge /opt/toneforge/backend
systemctl restart toneforge
sleep 8
systemctl is-active toneforge"

echo "==> health"
curl -s -m 8 https://jamn.app/api/health && echo
echo "==> done"
