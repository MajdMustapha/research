#!/bin/bash
# Polymarket System — Quick Start
# Run this once to set everything up

set -e

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   POLYMARKET NICHE BOT — SETUP       ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Check Python
python3 --version 2>/dev/null || { echo "❌ Python 3 required"; exit 1; }
echo "✅ Python found"

# Install deps
echo "📦 Installing dependencies..."
pip install -q fastapi uvicorn requests python-dotenv

# Create .env if missing
if [ ! -f .env ]; then
  cp .env.example .env 2>/dev/null || cat > .env << 'EOF'
POLY_PRIVATE_KEY=0xYOUR_KEY_HERE
POLY_FUNDER_ADDRESS=0xYOUR_SAFE_ADDRESS_HERE
MAX_BET_USDC=5
MIN_EDGE_POINTS=15
DRY_RUN=true
SCAN_INTERVAL_MINUTES=60
EOF
  echo "⚠️  Created .env — please fill in your credentials before going live"
fi

echo ""
echo "🚀 Starting backend on http://localhost:8000"
echo "📊 Open dashboard/index.html in your browser"
echo ""
echo "To set up the autonomous loop in Claude Code:"
echo "  1. Open a new terminal and run: claude"
echo "  2. Inside Claude Code: /loop 1h run the polymarket niche scan"
echo ""
echo "Press Ctrl+C to stop the backend"
echo ""

uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
