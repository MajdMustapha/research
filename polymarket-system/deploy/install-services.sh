#!/bin/bash
# Install systemd services for Poly//Watch bot + Claude Code scanner
# Run as: sudo bash install-services.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Installing Poly//Watch systemd services ==="

# Copy service files
cp "$SCRIPT_DIR/polybot.service" /etc/systemd/system/polybot.service
cp "$SCRIPT_DIR/claude-scanner.service" /etc/systemd/system/claude-scanner.service

# Reload systemd
systemctl daemon-reload

# Enable both services (start on boot)
systemctl enable polybot
systemctl enable claude-scanner

# Start both services now
systemctl start polybot
echo "polybot.service started (FastAPI backend on :8000)"

systemctl start claude-scanner
echo "claude-scanner.service started (Claude Code autonomous loop)"

echo ""
echo "=== Both services installed and enabled ==="
echo ""
echo "Commands:"
echo "  systemctl status polybot           # bot status"
echo "  systemctl status claude-scanner    # scanner status"
echo "  journalctl -u polybot -f           # bot logs"
echo "  journalctl -u claude-scanner -f    # scanner logs"
echo "  sudo systemctl restart polybot     # restart bot"
echo "  sudo systemctl restart claude-scanner  # restart scanner"
