# Architecture & Roadmap

## System Overview

A real-time investment dashboard for a Belgium-based IBKR investor, combining portfolio monitoring, multi-source sentiment analysis, and DCA signal generation with Belgian tax compliance.

## Key Technical Decisions

### 1. IBKR Library: `ib_async` (NOT `ib_insync`)

The original `ib_insync` is frozen at v0.9.86 and effectively deprecated. The successor [`ib_async`](https://github.com/ib-api-reloaded/ib_async) (v2.1.0+) is the actively maintained fork with:
- Same clean async API with event-driven portfolio updates
- `updatePortfolioEvent`, `pnlEvent`, `pnlSingleEvent` for real-time data
- Implements IBKR binary protocol directly — no need for official `ibapi`
- 30% faster WebSocket polling in recent releases

### 2. Connection Architecture

```
┌─────────────────────────────────────────────────────────┐
│ TWS / IB Gateway (Java, runs separately)                │
│   Port 7497 (TWS) or 4001 (Gateway)                    │
│   Must add 127.0.0.1 to Trusted IPs                    │
│   Allocate >= 4GB Java memory                           │
└──────────────┬──────────────────────────────────────────┘
               │ TCP Socket (binary protocol)
               │
┌──────────────▼──────────────────────────────────────────┐
│ ibkr-worker (single ib_async connection)                │
│   - Portfolio event subscriptions                       │
│   - Scheduled snapshot persistence (every 5 min)        │
│   - Sentiment collection (every 15 min)                 │
│   - Signal recalculation (every 30 min)                 │
└──────────────┬──────────────────────────────────────────┘
               │ Database + WebSocket
               │
┌──────────────▼──────────────────────────────────────────┐
│ Streamlit Dashboard / FastAPI                           │
│   Reads from DB, does NOT connect to IBKR directly      │
└─────────────────────────────────────────────────────────┘
```

**Critical**: Only one `clientId` can be active at a time. The IBKR connection lives in a dedicated long-running process. Dashboard reads from the database. TWS disconnects daily at ~23:45 ET for server reset — the connection layer handles graceful reconnection.

### 3. IB Gateway vs TWS

| Feature | TWS | IB Gateway |
|---------|-----|------------|
| Headless | No (requires GUI) | Yes |
| Stability | Moderate | Better for always-on |
| Resource usage | Heavy | Lighter |
| Port | 7497 (paper), 7496 (live) | 4001 (paper), 4002 (live) |
| Auto-restart | No | Configurable |

**Recommendation**: IB Gateway for production, TWS for development.

## Sentiment Pipeline

### Source Weights & Rationale

| Source | Weight | Refresh | Rationale |
|--------|--------|---------|-----------|
| CNN Fear & Greed | 30% | 15 min | Proven composite of 7 market indicators |
| VIX Percentile | 15% | 15 min | Direct measure of institutional hedging demand |
| Put/Call Ratio | 15% | 15 min | Contrarian — high put buying = fear |
| Financial News (Finnhub) | 25% | 10 min | Most timely source of fundamental shifts |
| Reddit (WSB + r/investing) | 15% | 15 min | Retail sentiment, contrarian signal |

### NLP Approach (Tiered)

1. **VADER** — for Reddit/Twitter text. Fast, no GPU, handles social media slang well.
2. **FinBERT** (optional Phase 2 upgrade) — HuggingFace `ProsusAI/finbert` for news headlines. ~440MB model, runs on CPU, ~300ms per batch of 32 headlines.
3. **Pre-built APIs** — ApeWisdom (pre-aggregated WSB mentions), Tradestie (15-min sentiment refresh), fear-greed PyPI package.

### Reddit Rate Limits

PRAW: 100 requests/minute for OAuth. Adequate for hourly scraping of 3 subreddits. Do NOT attempt real-time streaming. Supplement with:
- **ApeWisdom API**: `https://apewisdom.io/api/v1.0/filter/all-stocks/` — pre-aggregated WSB mention counts
- **Tradestie API**: `https://tradestie.com/api/v1/apps/reddit` — 15-min sentiment refresh

## DCA Signal Engine

### Scoring Model (0-12 points per ticker)

| Factor | Condition | Points |
|--------|-----------|--------|
| RSI(14) | < 30 (oversold) | +2 |
| RSI(14) | 30-40 (near oversold) | +1 |
| Price vs SMA(200) | Below | +2 |
| Bollinger Band | Below lower band | +1 |
| MACD | Bullish crossover | +1 |
| Composite Sentiment | < -0.3 | +2 |
| Fear & Greed | < 25 (Extreme Fear) | +2 |
| VIX | > 80th percentile | +1 |

### Signal Buckets

- **0-2**: NO_ACTION
- **3-5**: CONSIDER
- **6-8**: BUY (DCA this month)
- **9-12**: STRONG_BUY (double DCA allocation)

### Position Weight Dampening

If a position already exceeds 15% of portfolio, its buy score is dampened by 50%. This prevents further concentration (e.g., META at 24.9% gets signal dampened to discourage adding more before diversifying).

### DCA Budget Adjustment

Monthly budget multiplied by sentiment factor:
- Extreme Fear (0-20): **2.0x** — "be greedy when others are fearful"
- Fear (20-40): **1.5x**
- Neutral (40-60): **1.0x**
- Greed (60-80): **0.5x**
- Extreme Greed (80-100): **0.25x**

## Belgian Tax Module

### Why This Matters

IBKR is a foreign broker. Belgian brokers withhold TOB automatically. **IBKR does not.** You must:
1. Self-calculate TOB for every transaction
2. File monthly via MyMinfin > Diverse taksen (DivTax)
3. Pay via bank transfer to BE39 6792 0022 9319
4. Declare the foreign account to the NBB
5. Calculate and declare CGT annually (new 2026)

### TOB Rates (current portfolio = all US stocks → 0.35%)

| Instrument | Rate | Cap per transaction |
|------------|------|---------------------|
| Individual stocks | 0.35% | EUR 1,600 |
| Accumulating ETFs (Belgian reg.) | 1.32% | EUR 4,000 |
| Other ETFs | 0.12% | EUR 1,300 |
| Bonds | 0.12% | EUR 1,600 |

### Capital Gains Tax (effective Jan 1, 2026)

- **Rate**: 10% flat
- **Exemption**: EUR 10,000/year (unused EUR 1,000 carries forward)
- **Grandfathering**: Until Dec 31, 2030, cost basis = max(purchase price, market value on 31/12/2025)
- **After 2030**: Cost basis = market value on 31/12/2025
- **Action required**: Save all purchase documentation NOW

### Currency Risk

Portfolio is 100% USD, tax obligations in EUR. Every tax calculation needs EUR/USD conversion at the ECB reference rate on the relevant date. This is a common source of errors.

### Recommended Tax Tools

- **[tobcalc](https://github.com/samjmck/tobcalc)** — Auto-calculates TOB from IBKR exports
- **[tob.tax](https://tob.tax/en)** — TOB + CGT calculator with IBKR import
- **IBKR Flex Query** — Programmatic trade/dividend report download

## Phased Delivery

### Phase 1: Foundation (Done)
- [x] Portfolio viewer with mock data
- [x] Sentiment aggregation engine (5 sources)
- [x] DCA signal generator (5-factor scoring)
- [x] Belgian tax calculator (TOB, CGT, dividends, Reynders)
- [x] Streamlit dashboard

### Phase 2: Live IBKR Connection
- [ ] `ib_async` persistent connection with auto-reconnect
- [ ] Event-driven portfolio updates (`updatePortfolioEvent`)
- [ ] Historical bar fetching for technical indicators
- [ ] VIX direct from IBKR (no yfinance dependency)
- [ ] Database persistence (PostgreSQL with SQLAlchemy)

### Phase 3: Enhanced Sentiment
- [ ] FinBERT for higher-quality news sentiment
- [ ] ApeWisdom + Tradestie API integration
- [ ] Twitter/X sentiment (Adanos API or direct)
- [ ] Sentiment history charting with correlation to portfolio P&L

### Phase 4: Alerts & Automation
- [ ] Telegram bot for signal notifications
- [ ] Email alerts as fallback
- [ ] APScheduler for all periodic jobs
- [ ] Morning briefing: overnight sentiment shift + action items

### Phase 5: Tax Automation
- [ ] IBKR Flex Query trade import
- [ ] Automated TOB calculation from trade history
- [ ] EUR/USD conversion at ECB reference rates
- [ ] Monthly DivTax filing summary generation
- [ ] Annual CGT report with grandfathering calculations
- [ ] "What-if" tax calculator (sell simulation)

### Phase 6: Advanced
- [ ] React frontend (replace Streamlit for production)
- [ ] Docker Compose deployment (PostgreSQL + worker + API + dashboard)
- [ ] Portfolio rebalancing optimizer with tax-loss harvesting
- [ ] IBKR options chain integration for covered call analysis
- [ ] Multi-account support

## References

- [ib_async GitHub](https://github.com/ib-api-reloaded/ib_async)
- [ib_async Documentation](https://ib-api-reloaded.github.io/ib_async/)
- [TWS API Documentation](https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/)
- [Belgian Tax Guide 2026 (Curvo)](https://curvo.eu/article/taxes-belgian-investors)
- [Belgian CGT Changes (EY)](https://www.ey.com/en_be/insights/tax/the-new-belgian-capital-gains-tax-what-changes-in-2026)
- [Belgian CGT (PwC)](https://news.pwc.be/belgiums-comprehensive-capital-gains-tax-changes-key-updates-and-implications-starting-january-2026/)
- [TOB Declaration Guide (Curvo)](https://curvo.eu/article/tob-declaration)
- [tobcalc (GitHub)](https://github.com/samjmck/tobcalc)
- [tob.tax](https://tob.tax/en)
- [fear-greed PyPI](https://pypi.org/project/fear-greed/)
- [ApeWisdom API](https://apewisdom.io/api/)
- [Finnhub API](https://finnhub.io/docs/api/news-sentiment)
