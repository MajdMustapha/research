#!/bin/bash
# On-demand single ticker analysis
# Usage: ./run_single.sh NVDA

TICKER=${1:-NVDA}
DATE=$(date +%Y-%m-%d)

cd /home/ubuntu/portfoliomind
source .env

claude --print \
  --allowedTools "Bash,Task,Read,Write" \
  "Run a full single-ticker deep-dive for ${TICKER} (date: ${DATE}).
   Follow CLAUDE.md pipeline phases 1 and 2 for ${TICKER} only.
   Skip phase 3 (portfolio synthesis) and phase 4 (webhook).
   Print a summary of the risk assessment and final recommendation."
