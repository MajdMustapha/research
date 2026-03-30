# CLAUDE.md — Algo Trader Project Memory

## What this project is
Regime-aware crypto trading bot. Uses ADX to detect market regime, then runs
momentum (EMA crossover) in trending conditions or mean reversion (RSI + Bollinger)
in ranging conditions. Crypto first: BTC/ETH/SOL on 1h candles.

## Non-negotiable rules
- ALWAYS use Binance testnet (testnet: true in config.yaml) until walk-forward passes
- NEVER hardcode API keys — .env file only, never commit to git
- ALL indicator functions must be pure (no side effects, no global state)
- ALL order placement must check circuit_breaker.state == ACTIVE first
- Model fees (0.1%) and slippage (0.05%) in every backtest
- Parameters use round numbers only — no curve-fitted values like EMA(47)

## Stack
- Python 3.11, pandas, numpy, ccxt, FastAPI, SQLite, python-dotenv, pytest

## How to run
- Backtest: `python main.py --mode backtest`
- Paper trade: `python main.py --mode paper`
- Tests: `pytest tests/ -v`

## Data format
OHLCV DataFrame columns: [timestamp, open, high, low, close, volume]
timestamp: UTC, timezone-aware. Prices in USDT.

## Signal layers
- Layer 1 (ACTIVE): Price/volume OHLCV from Binance only
- Layer 2 (INACTIVE): Funding rate, open interest — enable in config when ready
- Layer 3 (INACTIVE): Fear & Greed, news sentiment — enable in config when ready

## Build progress
[ ] Phase 1 — Foundation (indicators, historical loader, backtest engine)
[ ] Phase 2 — Strategy (regime detector, momentum, mean reversion, walk-forward)
[ ] Phase 3 — Execution (exchange client, order manager, risk/circuit breaker)
[ ] Phase 4 — Operations (trade logger, Telegram alerts, dashboard, VPS deploy)
[ ] Phase 5 — Live (paper trade 2 weeks minimum, then 10% of capital)

Update checkboxes as phases complete.
