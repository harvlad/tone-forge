#!/bin/bash
# ToneForge VPS Setup Script
# Tested on Ubuntu 22.04+ / Debian 12+
#
# Usage: ssh root@your-vps 'bash -s' < setup-vps.sh
#
# Prerequisites:
#   - Fresh VPS with 4GB+ RAM (demucs needs ~3GB)
#   - Domain pointing to VPS IP (optional, for HTTPS)

set -euo pipefail

INSTALL_DIR="/opt/toneforge"
TONEFORGE_USER="toneforge"

echo "=== ToneForge VPS Setup ==="

# System packages
apt-get update
apt-get install -y python3.11 python3.11-venv python3-pip ffmpeg nginx certbot python3-certbot-nginx ufw

# Firewall: SSH + HTTP(S) only. The API (port 8000) binds loopback and is
# reachable exclusively through nginx.
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Create service user
if ! id "$TONEFORGE_USER" &>/dev/null; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$TONEFORGE_USER"
fi

# Create directories
mkdir -p "$INSTALL_DIR/backend/data/history"
mkdir -p "$INSTALL_DIR/backend/data/layers"

# Clone or update repo (you'll rsync the code instead if private)
# git clone https://github.com/yourorg/tone-forge.git "$INSTALL_DIR" || true

echo "=== Copy your backend code to $INSTALL_DIR/backend ==="
echo "Example: rsync -avz backend/ root@vps:$INSTALL_DIR/backend/"

# Python venv
python3.11 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip wheel

# Install dependencies (after you rsync the code)
# "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt"

# Fix ownership
chown -R "$TONEFORGE_USER:$TONEFORGE_USER" "$INSTALL_DIR"

# Install systemd service
cp "$INSTALL_DIR/backend/deploy/toneforge.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable toneforge

echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "1. rsync -avz backend/ root@vps:$INSTALL_DIR/backend/"
echo "2. ssh root@vps '$INSTALL_DIR/venv/bin/pip install -r $INSTALL_DIR/backend/requirements.txt'"
echo "3. ssh root@vps 'systemctl start toneforge'"
echo "4. Configure nginx + certbot for HTTPS (required: the API only listens on loopback):"
echo "   cp $INSTALL_DIR/backend/deploy/nginx-toneforge.conf /etc/nginx/sites-available/toneforge"
echo "   ln -s /etc/nginx/sites-available/toneforge /etc/nginx/sites-enabled/"
echo "   certbot --nginx -d your-domain.com"
echo ""
echo "Create /opt/toneforge/.env (loaded by the systemd unit):"
echo "  # Admin/debug endpoints are unreachable remotely without this."
echo "  TONEFORGE_ADMIN_TOKEN=\$(openssl rand -hex 32)"
echo "  # R2 storage (optional)"
echo "  R2_ACCOUNT_ID=your_account_id"
echo "  R2_ACCESS_KEY_ID=your_key"
echo "  R2_SECRET_ACCESS_KEY=your_secret"
echo "  R2_BUCKET=your_bucket"
echo "Then: chmod 600 /opt/toneforge/.env && systemctl restart toneforge"
