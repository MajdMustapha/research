#!/bin/bash
# Cron health check — runs every 5 minutes
# Checks service status and dashboard availability

SERVICE="algo-trader"
DASHBOARD_URL="http://localhost:8080/status"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

send_alert() {
    local message="$1"
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=🚨 ${message}" > /dev/null 2>&1
    fi
    logger -t algo-trader-health "$message"
}

# Check systemd service
if ! systemctl is-active --quiet "$SERVICE"; then
    send_alert "Service $SERVICE is down! Attempting restart..."
    systemctl restart "$SERVICE"
    sleep 5
    if systemctl is-active --quiet "$SERVICE"; then
        send_alert "Service $SERVICE restarted successfully."
    else
        send_alert "Service $SERVICE failed to restart!"
    fi
fi

# Check dashboard
if ! curl -sf "$DASHBOARD_URL" > /dev/null 2>&1; then
    send_alert "Dashboard at $DASHBOARD_URL is not responding."
fi
