# IBKR Investment Dashboard

Real-time portfolio monitoring, sentiment analysis, and DCA signal generation for Interactive Brokers — tailored for Belgian investors.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Streamlit Dashboard                          │
│  ┌──────────┐ ┌──────────────┐ ┌───────────┐ ┌──────────────┐ │
│  │Portfolio  │ │  Sentiment   │ │DCA/Buy    │ │ Belgian Tax  │ │
│  │Overview   │ │  Gauge       │ │Signals    │ │ Calculator   │ │
│  └─────┬────┘ └──────┬───────┘ └─────┬─────┘ └──────────────┘ │
└────────┼─────────────┼───────────────┼──────────────────────────┘
         │             │               │
    ┌────▼────┐  ┌─────▼──────┐  ┌─────▼──────┐
    │  IBKR   │  │ Sentiment  │  │   Signal   │
    │  Client  │  │ Aggregator │  │   Engine   │
    │(ib_async)│  │            │  │            │
    └────┬────┘  └─────┬──────┘  └────────────┘
         │             │
    ┌────▼────┐  ┌─────▼──────────────────────┐
    │TWS / IB │  │ Fear&Greed │ VIX │ Reddit  │
    │ Gateway │  │ Put/Call   │News │ Finnhub │
    └─────────┘  └────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone & install
pip install -r requirements.txt

# 2. Configure (optional — works with mock data out of the box)
cp config/.env.example .env
# Edit .env with your API keys

# 3. Launch dashboard
streamlit run src/dashboard/app.py
```

The dashboard runs immediately with your portfolio data built in. For live IBKR data, ensure TWS or IB Gateway is running on `localhost:7497`.

## Modules

| Module | Path | Description |
|--------|------|-------------|
| **IBKR Client** | `src/ibkr/client.py` | Real-time portfolio via `ib_async` (TWS API) + mock client |
| **Sentiment** | `src/sentiment/aggregator.py` | CNN Fear & Greed, VIX, Reddit (PRAW+VADER), Finnhub News, Put/Call Ratio |
| **Signals** | `src/signals/dca_engine.py` | Multi-factor DCA/buy engine (sentiment + RSI + drawdown + weight) |
| **Tax** | `src/tax/belgian.py` | TOB, CGT (2026+), dividend tax, Reynders tax calculator |
| **Dashboard** | `src/dashboard/app.py` | Streamlit UI with all panels |

## Sentiment Sources

| Source | API | Cost | Weight |
|--------|-----|------|--------|
| CNN Fear & Greed | `fear-greed` PyPI / CNN direct | Free | 2.0x |
| VIX | yfinance (`^VIX`) | Free | 2.0x |
| Put/Call Ratio | CBOE CSV | Free | 1.5x |
| Reddit | PRAW + VADER | Free (needs Reddit app) | 1.0x |
| Finnhub News | finnhub-python | Free (60 req/min) | 1.5x |

## Signal Logic

The DCA engine scores each position (0-100) using:

1. **Sentiment** — Extreme fear = +20, Extreme greed = -15
2. **RSI(14)** — Oversold (<30) = +20, Overbought (>70) = -15
3. **52-week drawdown** — >30% down = +25, >20% = +15
4. **Portfolio weight** — Overweight = -10, Underweight = +5
5. **200 SMA distance** — >10% below = +10, >20% above = -5

Signals: **STRONG BUY** (80+) → **BUY** (65+) → **DCA** (55+) → **HOLD** (40+) → **TRIM** (25+) → **SELL**

DCA budget adjusts with sentiment: 2x in extreme fear, 0.25x in extreme greed.

## Belgian Tax Rules (2026)

| Tax | Rate | Notes |
|-----|------|-------|
| **TOB** | 0.35% stocks, 0.12% ETFs | Self-declare via MyMinfin/DivTax |
| **CGT** | 10% on gains | EUR 10,000/year exemption. Use `tobcalc` for automation |
| **Dividends** | 30% | Use accumulating ETFs to avoid |
| **Reynders** | 30% | ETFs with >10% bond allocation |

IBKR does **not** withhold Belgian taxes. You must self-declare.

## IBKR Setup

1. Open TWS or IB Gateway
2. Enable API: Edit → Global Config → API → Settings
3. Check "Enable ActiveX and Socket Clients"
4. Set port to `7497` (TWS) or `4001` (Gateway)
5. Add `127.0.0.1` to Trusted IPs
6. Allocate >= 4GB Java memory

Use `ib_async` (not `ib_insync` which is deprecated).

## Roadmap

- [x] Phase 1: Portfolio viewer + mock data
- [x] Phase 2: Sentiment aggregation engine
- [x] Phase 3: DCA signal generator
- [x] Phase 4: Belgian tax calculator
- [ ] Phase 5: Live IBKR connection with auto-reconnect
- [ ] Phase 6: Historical tracking & performance charts
- [ ] Phase 7: Alert system (Telegram/email on signal changes)
- [ ] Phase 8: Portfolio rebalancing suggestions with tax optimization
