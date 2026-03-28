# Polymarket Niche Bot — Claude Code Project

This is an autonomous Polymarket weather market trading system.

## Architecture

```
polymarket-system/
├── backend/main.py          ← FastAPI backend (scan engine + API)
├── dashboard/index.html     ← Browser dashboard (open directly)
├── .claude/agents/
│   └── polymarket-trader.md ← Autonomous subagent definition
├── bot_data.db              ← SQLite: trades, signals, scans
├── logs/                    ← Backend logs
└── .env                     ← Credentials (never share/commit)
```

## How to Run

### Option A — Full autonomous mode (recommended)
```bash
# Terminal 1: start backend
cd ~/polymarket-system
pip install fastapi uvicorn requests python-dotenv
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Terminal 2: open Claude Code and set up the loop
claude
/loop 1h run the polymarket niche scan using the polymarket-trader agent
```

### Option B — One-shot run
```bash
claude --agent polymarket-trader "Run one scan cycle and report"
```

### Dashboard
Open `dashboard/index.html` directly in your browser.
It auto-refreshes every 5 seconds from the backend at localhost:8000.

## Key Commands

| Command | Effect |
|---|---|
| `/loop 1h ...` | Scan every hour |
| `/loop 30m ...` | Scan every 30 min |
| `curl -X POST http://localhost:8000/api/scan/trigger` | Manual scan |
| `sqlite3 bot_data.db "SELECT * FROM trades"` | Query trades |

## Configuration (.env)

```
POLY_PRIVATE_KEY=0x...      # wallet private key
POLY_FUNDER_ADDRESS=0x...   # Polymarket safe address
MAX_BET_USDC=5              # max per trade
MIN_EDGE_POINTS=15          # minimum edge threshold
DRY_RUN=true                # set false for live trading
SCAN_INTERVAL_MINUTES=60    # how often to scan
```

## Safety Rules (for Claude Code agent)

1. NEVER set DRY_RUN=false without explicit user confirmation
2. NEVER increase MAX_BET_USDC above configured value
3. NEVER place more than 3 trades in a single scan cycle
4. ALWAYS log every signal and trade to the database
5. If API errors occur 3+ times, pause and alert user

## Strategy Summary

We implement the "Lummox/gopfan2" niche weather strategy:
- Target: temperature range markets under $50K volume
- Edge: Open-Meteo GFS+ECMWF forecast vs market-implied probability
- Signal: gap >15 percentage points between my prob and market price
- Sizing: gopfan2 method — small ($5) consistent bets, high frequency
- Resolution source: Weather Underground airport stations (LLBG, VHHH, EGLL...)
