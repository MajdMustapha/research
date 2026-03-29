#!/bin/bash
# Full portfolio pipeline
# Called by cron or systemd

cd /home/ubuntu/portfoliomind
source .env

DATE=$(date +%Y-%m-%d)
LOG_FILE="logs/${DATE}.log"
mkdir -p "workspace/${DATE}" "logs"

echo "[$(date)] Starting PortfolioMind pipeline" >> "$LOG_FILE"

claude --print \
  --allowedTools "Bash,Task,Read,Write" \
  "Run the full PortfolioMind daily pipeline for all 7 portfolio tickers.
   Today's date is ${DATE}.
   Follow all instructions in CLAUDE.md exactly.
   Log progress to ${LOG_FILE}.
   Report completion status when done." \
  2>> "$LOG_FILE"

echo "[$(date)] Pipeline complete" >> "$LOG_FILE"
