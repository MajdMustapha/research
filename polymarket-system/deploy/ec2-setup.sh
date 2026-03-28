#!/bin/bash
# Polymarket Niche Bot — EC2 One-Shot Setup
# Run as: sudo bash ec2-setup.sh
set -euo pipefail

echo "=== Polymarket Bot EC2 Setup ==="

# 1. System packages
apt-get update -y
apt-get install -y python3 python3-pip python3-venv curl sqlite3 git

# 2. Create app user
useradd -m -s /bin/bash polybot 2>/dev/null || true

# 3. Clone repo (or copy files)
APP_DIR="/home/polybot/polymarket-system"
if [ ! -d "$APP_DIR" ]; then
    sudo -u polybot git clone https://github.com/MajdMustapha/research.git /home/polybot/research
    sudo -u polybot ln -sf /home/polybot/research/polymarket-system "$APP_DIR"
fi

# 4. Python venv + deps
sudo -u polybot python3 -m venv /home/polybot/venv
sudo -u polybot /home/polybot/venv/bin/pip install --upgrade pip
sudo -u polybot /home/polybot/venv/bin/pip install fastapi uvicorn[standard] requests python-dotenv

# 5. Create .env if missing
if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" <<'ENVEOF'
DRY_RUN=true
MAX_BET_USDC=5
MIN_EDGE_POINTS=15
SCAN_INTERVAL_MINUTES=60
POLY_PRIVATE_KEY=dry_run_no_key_needed
POLY_FUNDER_ADDRESS=dry_run_no_key_needed
ENVEOF
    chown polybot:polybot "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
fi

# 6. Systemd service
cat > /etc/systemd/system/polybot.service <<'SVCEOF'
[Unit]
Description=Polymarket Niche Trading Bot
After=network.target

[Service]
Type=simple
User=polybot
WorkingDirectory=/home/polybot/polymarket-system
EnvironmentFile=/home/polybot/polymarket-system/.env
ExecStart=/home/polybot/venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF

# 7. Start
systemctl daemon-reload
systemctl enable polybot
systemctl start polybot

echo ""
echo "=== DONE ==="
echo "Bot running on port 8000"
echo "Check status:  systemctl status polybot"
echo "View logs:     journalctl -u polybot -f"
echo "Edit config:   nano /home/polybot/polymarket-system/.env"
echo "Restart:       systemctl restart polybot"
echo ""
echo "Dashboard: open http://<YOUR-EC2-IP>:8000 in browser"
echo "(Make sure security group allows inbound TCP 8000)"
