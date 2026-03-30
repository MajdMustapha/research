#!/bin/bash
# VPS Setup Script for Algo Trader
# Tested on Ubuntu 22.04 LTS

set -euo pipefail

echo "=== Algo Trader VPS Setup ==="

# Update system
apt-get update && apt-get upgrade -y

# Install Python 3.11
apt-get install -y software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Create trader user
useradd -m -s /bin/bash trader || echo "User 'trader' already exists"

# Setup directory
mkdir -p /opt/algo-trader
chown trader:trader /opt/algo-trader

# Clone repo (update URL as needed)
su - trader -c "
    cd /opt/algo-trader
    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
"

# Copy .env template
cp /opt/algo-trader/.env.example /opt/algo-trader/.env
chown trader:trader /opt/algo-trader/.env
echo ">>> IMPORTANT: Edit /opt/algo-trader/.env with your API keys"

# Install systemd service
cp /opt/algo-trader/deploy/systemd_service.conf /etc/systemd/system/algo-trader.service
systemctl daemon-reload
systemctl enable algo-trader

# Setup cron health check
cp /opt/algo-trader/deploy/cron_health.sh /opt/algo-trader/cron_health.sh
chmod +x /opt/algo-trader/cron_health.sh
echo "*/5 * * * * trader /opt/algo-trader/cron_health.sh" > /etc/cron.d/algo-trader-health

echo "=== Setup complete ==="
echo "1. Edit /opt/algo-trader/.env with your API keys"
echo "2. Start with: systemctl start algo-trader"
echo "3. Check logs: journalctl -u algo-trader -f"
