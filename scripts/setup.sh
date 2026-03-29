#!/bin/bash
# Tested on Ubuntu 24.04 LTS (Hetzner CX22 or equivalent)

set -e

echo "=== Polymarket Trading System — VPS Setup ==="

# System packages
apt-get update -qq
apt-get install -y python3.11 python3.11-venv python3-pip supervisor sqlite3 curl wget git

# Verify we're in the US (basic check)
VPS_COUNTRY=$(curl -s https://ipapi.co/country/ 2>/dev/null || echo "unknown")
echo "VPS country detected: $VPS_COUNTRY"
if [ "$VPS_COUNTRY" != "US" ]; then
    echo "Warning: VPS appears to be outside the US ($VPS_COUNTRY)."
    echo "   Polymarket US account requires US-based access."
    echo "   Proceeding anyway — geoblock check below will confirm."
fi

# Install polymarket-cli (pin to v0.1.5)
echo ""
echo "=== Installing polymarket-cli ==="
curl -sSL https://raw.githubusercontent.com/Polymarket/polymarket-cli/main/install.sh | sh
polymarket --version

# Geoblock check (Section 23.4)
echo ""
echo "=== Checking geoblock status ==="
GEOBLOCK=$(polymarket -o json clob geoblock 2>/dev/null || echo '{"blocked": true}')
BLOCKED=$(echo "$GEOBLOCK" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('blocked', True))")

if [ "$BLOCKED" = "True" ]; then
    echo "GEOBLOCK: This VPS IP is blocked from trading on Polymarket."
    echo "   Result: $GEOBLOCK"
    echo "   Change your VPS provider/region and re-run setup."
    exit 1
else
    echo "Geoblock check passed — trading is available from this IP."
fi

# Project setup
mkdir -p /opt/polymarket-system
mkdir -p /var/log/polymarket
mkdir -p /opt/polymarket-system/data

# Python venv
python3.11 -m venv /opt/polymarket-system/venv
source /opt/polymarket-system/venv/bin/activate
pip install --upgrade pip

# Python dependencies
pip install -r /opt/polymarket-system/requirements.txt

# Create polymarket user (don't run as root)
useradd -r -s /bin/false -d /opt/polymarket-system polymarket 2>/dev/null || true
chown -R polymarket:polymarket /opt/polymarket-system
chown -R polymarket:polymarket /var/log/polymarket

# Configure supervisord
cp /opt/polymarket-system/supervisord.conf /etc/supervisor/conf.d/polymarket.conf

# Load .env (never commit this file)
if [ ! -f /opt/polymarket-system/.env ]; then
    cp /opt/polymarket-system/.env.example /opt/polymarket-system/.env
    echo "Edit /opt/polymarket-system/.env before starting"
fi

# Wallet setup (Section 23.5)
echo ""
echo "=== Wallet setup ==="
echo "Your Polymarket wallet address:"
polymarket wallet address 2>/dev/null || echo "(wallet not yet configured — run 'polymarket setup' first)"

echo ""
echo "IMPORTANT: Before continuing, you must fund this wallet with MATIC for gas."
echo "   Required: minimum 0.5 MATIC (1 MATIC recommended for headroom)"
echo "   Network: Polygon Mainnet (Chain ID 137)"
echo "   How to get MATIC: buy on Coinbase/Kraken, send to the address above on Polygon network"
echo ""
read -p "Press ENTER once you have sent MATIC to the wallet and the transaction has confirmed..."

echo "Running approval flow (6 on-chain transactions)..."
polymarket approve set
echo "Approvals complete"

# Initialize database
python3 -c "
import sys; sys.path.insert(0, '/opt/polymarket-system')
from lib.state import init_db
init_db()
print('Database initialized')
"

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "1. Edit .env with your API keys"
echo "2. Run: python scripts/run_scout.py --domain <your_domain>"
echo "3. Review config/wallets.yaml"
echo "4. Edit config/markets.yaml with your tracked markets"
echo "5. Run tests: pytest tests/ -v"
echo "6. Start (paper mode): supervisorctl start all"
echo "7. Set paper_trade: false in settings.yaml when ready for live"
