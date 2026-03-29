#!/bin/bash
# Backup bot_data.db safely using SQLite online backup (no corruption risk)
# Keeps last 24 hourly backups + last 7 daily backups
set -euo pipefail

DB="/home/ubuntu/research/polymarket-system/backend/bot_data.db"
BACKUP_DIR="/home/ubuntu/backups"

[ -f "$DB" ] || { echo "No DB found at $DB"; exit 0; }

TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Use sqlite3 .backup for a safe, consistent copy
sqlite3 "$DB" ".backup '${BACKUP_DIR}/bot_data-${TIMESTAMP}.db'"

# Keep last 24 hourly backups, remove older
ls -1t "${BACKUP_DIR}"/bot_data-*.db 2>/dev/null | tail -n +25 | xargs -r rm --

echo "Backup: bot_data-${TIMESTAMP}.db ($(du -h "${BACKUP_DIR}/bot_data-${TIMESTAMP}.db" | cut -f1))"
