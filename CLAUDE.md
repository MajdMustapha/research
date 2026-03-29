# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repo contains a Polymarket weather market trading bot ("Poly//Watch"). It scans Polymarket for niche temperature-range markets, compares market prices against weather ensemble forecasts, and places trades when sufficient edge is detected.

## Running the System

```bash
# Quick start (installs deps, creates .env, starts backend)
cd polymarket-system && bash start.sh

# Or manually:
pip install -r polymarket-system/requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Dashboard is served at `http://localhost:8000/` directly from the backend.

### Autonomous Mode via Claude Code

```bash
# Hourly scan loop using the dedicated subagent
/loop 1h run the polymarket niche scan using the polymarket-trader agent

# One-shot run
claude --agent polymarket-trader "Run one scan cycle and report"
```

### Key API Endpoints

- `GET /api/status` — bot status, mode, config
- `GET /api/trades`, `/api/signals`, `/api/scans`, `/api/stats` — data queries
- `POST /api/scan/trigger` — manually start a scan cycle
- `POST /api/trades/resolve` — mark trade outcome with P&L
- `POST /api/backtest/run` — run historical backtest (body: `{city, days_back, threshold}`)

## Architecture

**Single-file backend** (`polymarket-system/backend/main.py`): FastAPI app that combines:
- Scan engine: fetches weather markets from Gamma API, gets ensemble forecasts from Open-Meteo, computes edge, and executes trades via py-clob-client
- SQLite persistence (`bot_data.db`): tables for `trades`, `signals`, `scans`, `backtest_runs`
- Background scheduler: runs `scan_loop()` on startup at configurable interval
- WebSocket price feed: connects to Polymarket CLOB WebSocket for real-time prices, falls back to REST
- HTTP helper (`curl_get`): uses curl subprocess first (to bypass proxy issues), falls back to `requests`

**Dashboard** (`polymarket-system/dashboard/index.html`): Single-file React app (loaded via CDN Babel transpilation). Polls `/api/*` endpoints every 5 seconds. Tabs: Overview, Signals, Trades, Activity Log, Config.

**Subagent** (`polymarket-system/.claude/agents/polymarket-trader.md`): Claude Code agent definition for autonomous scan-analyse-trade-report cycles.

**Deploy** (`polymarket-system/deploy/`): EC2 setup script and guide. Creates a `polybot` systemd service on Ubuntu.

## Trading Strategy

"Lummox/gopfan2" niche weather strategy:
- Targets temperature range markets under $50K volume
- Uses Open-Meteo GFS 31-member ensemble forecast to build probability distributions
- Trades when edge > `MIN_EDGE_POINTS` (default 15) between forecast probability and market price
- Sizes bets using fractional Kelly criterion (default quarter-Kelly, capped at `MAX_BET_USDC`)
- One trade per market per scan, max 8 markets evaluated per cycle

## Configuration

All config is via environment variables in `polymarket-system/.env`:
- `DRY_RUN` (default `true`) — **never set to `false` without explicit user confirmation**
- `MAX_BET_USDC` (default `5`) — **never increase above configured value**
- `MIN_EDGE_POINTS` (default `15`), `KELLY_FRACTION` (default `0.25`), `BANKROLL_USDC` (default `100`)
- `SCAN_INTERVAL_MINUTES` (default `60`)
- `POLY_PRIVATE_KEY`, `POLY_FUNDER_ADDRESS` — wallet credentials for live trading

## Safety Rules

1. Never set `DRY_RUN=false` without explicit user confirmation
2. Never increase `MAX_BET_USDC` above the user-configured value
3. Never place more than 3 trades in a single scan cycle
4. Always log every signal and trade to the database
5. If API errors occur 3+ times, pause and alert the user
