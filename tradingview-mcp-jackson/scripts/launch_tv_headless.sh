#!/bin/bash
# Launch headless Chromium with TradingView for VPS/headless environments.
# Uses --headless=new (Chrome 112+) which supports full JS APIs including CDP.
#
# Usage:
#   ./launch_tv_headless.sh                    # default: port 9222
#   ./launch_tv_headless.sh --port 9223        # custom port
#   ./launch_tv_headless.sh --user-data-dir ~/tv-profile  # persist login session
#
# After first login, reuse the same --user-data-dir to stay logged in.

CDP_PORT="${CDP_PORT:-9222}"
USER_DATA_DIR="${USER_DATA_DIR:-$HOME/.config/tradingview-headless}"
TV_URL="https://www.tradingview.com/chart/"

while [[ $# -gt 0 ]]; do
  case $1 in
    --port) CDP_PORT="$2"; shift 2 ;;
    --user-data-dir) USER_DATA_DIR="$2"; shift 2 ;;
    --url) TV_URL="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

CHROMIUM=$(command -v google-chrome || command -v chromium-browser || command -v chromium)
if [ -z "$CHROMIUM" ]; then
  echo "Error: No Chromium/Chrome found. Install with: sudo apt install chromium-browser"
  exit 1
fi

mkdir -p "$USER_DATA_DIR"

echo "Starting headless Chromium on CDP port $CDP_PORT..."
echo "  URL: $TV_URL"
echo "  Profile: $USER_DATA_DIR"
echo "  Browser: $CHROMIUM"
echo ""
echo "To persist login: run once with --headless=false (see below), log in, then restart headless."
echo ""

exec "$CHROMIUM" \
  --headless=new \
  --no-sandbox \
  --disable-gpu \
  --disable-software-rasterizer \
  --remote-debugging-port="$CDP_PORT" \
  --remote-debugging-address=127.0.0.1 \
  --user-data-dir="$USER_DATA_DIR" \
  --window-size=1920,1080 \
  --disable-dev-shm-usage \
  "$TV_URL"
