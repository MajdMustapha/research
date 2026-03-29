#!/bin/bash
# For ad-hoc questions during market hours
# Usage: ./run_interactive.sh

cd /home/ubuntu/portfoliomind
source .env

claude \
  --allowedTools "Bash,Task,Read,Write" \
  --system "You are PortfolioMind. The investor profile and portfolio are in config.yaml.
            Today's reports (if generated) are in workspace/$(date +%Y-%m-%d)/.
            Answer questions about the portfolio, generate analysis on demand,
            and follow all investment principles in CLAUDE.md."
