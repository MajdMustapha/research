#!/bin/bash
# Check if headless Chrome is running and TradingView session is alive.
# Sends Telegram alert on failures. Runs via cron every 30 min.

CDP_PORT="${CDP_PORT:-9222}"
STATE_FILE="/tmp/tv-session-state"
ENV_FILE="/home/ubuntu/tradingview-mcp-jackson/.env"

# Load Telegram creds
BOT_TOKEN=$(grep TELEGRAM_BOT_TOKEN "$ENV_FILE" 2>/dev/null | cut -d= -f2)
CHAT_ID=$(grep TELEGRAM_CHAT_ID "$ENV_FILE" 2>/dev/null | cut -d= -f2)

send_telegram() {
  if [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
    curl -sf -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
      -H "Content-Type: application/json" \
      -d "{\"chat_id\":\"${CHAT_ID}\",\"text\":\"$1\",\"parse_mode\":\"Markdown\"}" > /dev/null 2>&1
  fi
}

# Track previous state to avoid repeated alerts
PREV_STATE=$(cat "$STATE_FILE" 2>/dev/null || echo "ok")

# Check CDP is responding
VERSION=$(curl -sf http://localhost:$CDP_PORT/json/version 2>/dev/null)
if [ -z "$VERSION" ]; then
  echo "CRITICAL: Headless Chrome not responding on port $CDP_PORT"
  sudo systemctl restart tv-chrome.service 2>/dev/null
  sleep 8
  VERSION=$(curl -sf http://localhost:$CDP_PORT/json/version 2>/dev/null)
  if [ -z "$VERSION" ]; then
    echo "FAILED: Could not restart Chrome"
    if [ "$PREV_STATE" != "chrome_down" ]; then
      send_telegram "🔴 *TradingView Bot Alert*%0A%0AHeadless Chrome is DOWN and failed to restart.%0AThe bot cannot scan charts until this is fixed."
      echo "chrome_down" > "$STATE_FILE"
    fi
    exit 1
  fi
  echo "Chrome restarted successfully"
  send_telegram "🟡 *TradingView Bot Alert*%0A%0AHeadless Chrome was down but auto-restarted successfully."
fi

# Check TradingView page + session
TV_URL=$(curl -sf http://localhost:$CDP_PORT/json/list 2>/dev/null | python3 -c "
import sys, json
targets = json.load(sys.stdin)
for t in targets:
    if t.get('type') == 'page' and 'tradingview.com' in t.get('url', ''):
        print(t['url'])
        break
" 2>/dev/null)

if [ -z "$TV_URL" ]; then
  echo "WARNING: No TradingView page found in Chrome"
  if [ "$PREV_STATE" != "no_page" ]; then
    send_telegram "🟡 *TradingView Bot Alert*%0A%0ANo TradingView page found in Chrome. May need restart."
    echo "no_page" > "$STATE_FILE"
  fi
  exit 2
fi

if echo "$TV_URL" | grep -qP '/chart/[a-zA-Z0-9]+'; then
  echo "OK: Session active — $TV_URL"
  if [ "$PREV_STATE" != "ok" ]; then
    send_telegram "🟢 *TradingView Bot*%0A%0ASession restored. All systems go."
  fi
  echo "ok" > "$STATE_FILE"
  exit 0
else
  echo "WARNING: Session expired (URL: $TV_URL)"
  if [ "$PREV_STATE" != "session_expired" ]; then
    send_telegram "🔴 *TradingView Bot Alert — Session Expired*%0A%0AYour TradingView session has expired. Buy zone alerts will not work until you re-login.%0A%0A*To fix:*%0A1. Export cookies from your laptop browser (Cookie-Editor extension)%0A2. Copy to VPS%0A3. Run: \`node ~/tradingview-mcp-jackson/scripts/inject_cookies.js ~/cookies.json\`"
    echo "session_expired" > "$STATE_FILE"
  fi
  exit 2
fi
