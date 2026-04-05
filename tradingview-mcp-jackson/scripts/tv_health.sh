#!/bin/bash
# /tv-health — Full health check for TradingView bot stack
# Checks: Chrome, CDP, TradingView session, MCP server, Telegram, cron jobs

echo "=== TradingView Bot Health Check ==="
echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

FAIL=0

# 1. Headless Chrome process
echo -n "[Chrome]     "
if pgrep -f 'google-chrome.*headless' > /dev/null 2>&1; then
  PID=$(pgrep -f 'google-chrome.*headless' | head -1)
  echo "OK (PID $PID)"
else
  echo "DOWN — not running"
  FAIL=1
fi

# 2. CDP responding
echo -n "[CDP]        "
CDP_RESP=$(curl -sf http://localhost:9222/json/version 2>/dev/null)
if [ -n "$CDP_RESP" ]; then
  BROWSER=$(echo "$CDP_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('Browser','?'))" 2>/dev/null)
  echo "OK ($BROWSER)"
else
  echo "DOWN — port 9222 not responding"
  FAIL=1
fi

# 3. TradingView page loaded + session
echo -n "[TV Session] "
TV_URL=$(curl -sf http://localhost:9222/json/list 2>/dev/null | python3 -c "
import sys, json
targets = json.load(sys.stdin)
for t in targets:
    if t.get('type') == 'page' and 'tradingview.com' in t.get('url', ''):
        print(t['url'])
        break
" 2>/dev/null)

if [ -z "$TV_URL" ]; then
  echo "DOWN — no TradingView page found"
  FAIL=1
elif echo "$TV_URL" | grep -qP '/chart/[a-zA-Z0-9]+'; then
  echo "OK — logged in ($TV_URL)"
else
  echo "WARN — not logged in ($TV_URL)"
fi

# 4. Strategy indicator loaded
echo -n "[Strategy]   "
STRATEGY=$(curl -sf http://localhost:9222/json/list 2>/dev/null | python3 -c "
import sys, json
targets = json.load(sys.stdin)
tv = next((t for t in targets if t.get('type') == 'page' and 'tradingview.com' in t.get('url', '')), None)
if tv: print(tv.get('title', ''))
" 2>/dev/null)
if [ -n "$STRATEGY" ]; then
  echo "OK — $STRATEGY"
else
  echo "UNKNOWN — could not read chart title"
fi

# 5. Telegram bot
echo -n "[Telegram]   "
if [ -f /home/ubuntu/tradingview-mcp-jackson/.env ]; then
  BOT_TOKEN=$(grep TELEGRAM_BOT_TOKEN /home/ubuntu/tradingview-mcp-jackson/.env | cut -d= -f2)
  CHAT_ID=$(grep TELEGRAM_CHAT_ID /home/ubuntu/tradingview-mcp-jackson/.env | cut -d= -f2)
  if [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
    BOT_INFO=$(curl -sf "https://api.telegram.org/bot${BOT_TOKEN}/getMe" 2>/dev/null)
    if echo "$BOT_INFO" | grep -q '"ok":true'; then
      BOT_NAME=$(echo "$BOT_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['username'])" 2>/dev/null)
      echo "OK (@${BOT_NAME}, chat $CHAT_ID)"
    else
      echo "FAIL — bot token invalid"
      FAIL=1
    fi
  else
    echo "FAIL — missing credentials in .env"
    FAIL=1
  fi
else
  echo "FAIL — .env not found"
  FAIL=1
fi

# 6. Cron jobs
echo -n "[Cron]       "
CRON_COUNT=$(crontab -l 2>/dev/null | grep -c 'tradingview-mcp-jackson')
if [ "$CRON_COUNT" -ge 4 ]; then
  echo "OK ($CRON_COUNT jobs)"
else
  echo "WARN — only $CRON_COUNT/4 expected jobs found"
fi

# 7. Last alert scan
echo -n "[Last Scan]  "
if [ -f /tmp/tv-telegram-alert.log ]; then
  LAST_LINE=$(tail -1 /tmp/tv-telegram-alert.log)
  echo "$LAST_LINE"
else
  echo "No scans yet"
fi

# 8. Last brief
echo -n "[Last Brief] "
if [ -f /tmp/tv-telegram-brief.log ]; then
  LAST_BRIEF=$(tail -1 /tmp/tv-telegram-brief.log)
  echo "$LAST_BRIEF"
else
  echo "No briefs sent yet"
fi

echo ""
if [ $FAIL -eq 0 ]; then
  echo "Status: ALL SYSTEMS GO"
else
  echo "Status: ISSUES DETECTED"
fi

exit $FAIL
