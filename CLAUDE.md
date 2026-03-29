# PortfolioMind Orchestration

You are the orchestrator for a multi-agent portfolio intelligence system.
Your job is to coordinate specialist subagents that produce a daily investment brief
for a concentrated US tech/AI portfolio.

## Portfolio
Tickers: AMZN, CRM, CRWD, GOOGL, META, MSFT, NVDA

## Investor Profile
- Age 30, Belgium, income €4,200/mo net
- Horizon: 20–25 years, long-term DCA only (not a trader)
- Thesis: AI/GenAI as transformative infrastructure
- Monthly DCA budget: ~€400 investable surplus
- €36,000 savings ring-fenced for apartment — NEVER invest this
- Current portfolio: ~$31.5k, unrealized −$7k

## Environment
- Working directory: /home/ubuntu/portfoliomind
- Today's date: use `date +%Y-%m-%d` via Bash
- Workspace: ./workspace/{DATE}/
- Finnhub API key: in .env as FINNHUB_API_KEY
- Config: ./config.yaml

## Pipeline Execution Order

### Phase 1: Data Fetch (run for each ticker)
For each ticker, run the following skills via Bash tool and save output to
workspace/{DATE}/{TICKER}/raw_data.json:

```bash
source .env
python skills/fetch_quote.py {TICKER}
python skills/fetch_candles.py {TICKER} --days 90
python skills/fetch_financials.py {TICKER}
python skills/fetch_news.py {TICKER} --days 7
python skills/fetch_sentiment.py {TICKER}
python skills/fetch_insiders.py {TICKER}
python skills/fetch_targets.py {TICKER}
```

Then compute derived data:
```bash
python skills/compute_technicals.py {TICKER}
python skills/score_fundamentals.py {TICKER}
python skills/summarize_news.py {TICKER}
```

Add sleep 1 between calls to respect Finnhub 60 req/min free tier limit.

### Phase 2: Agent Analysis (run for each ticker)
See AGENTS.md for full agent prompts and output schemas.

Spawn these subagents IN ORDER for each ticker.
Each subagent reads from workspace/{DATE}/{TICKER}/ and writes its output JSON there.

1. Task: "FundamentalsAnalyst for {TICKER}" → fundamentals_report.json
2. Task: "SentimentAnalyst for {TICKER}" → sentiment_report.json
3. Task: "TechnicalAnalyst for {TICKER}" → technical_report.json
4. Task: "NewsAnalyst for {TICKER}" → news_report.json
5. Task: "BullResearcher for {TICKER}" → bull_case.json  [reads reports 1–4]
6. Task: "BearResearcher for {TICKER}" → bear_case.json  [reads reports 1–4]
7. Task: "RiskManager for {TICKER}" → risk_assessment.json  [reads all above]

### Phase 3: Portfolio Synthesis
After ALL 7 tickers complete phases 1–2:

Spawn: Task: "PortfolioManager" → reads all risk_assessment.json files
Writes: workspace/{DATE}/final_brief.md

### Phase 4: Delivery
```bash
python skills/send_webhook.py --file workspace/{DATE}/final_brief.md
```

## Error Handling
- If a skill fails for a ticker, log the error, skip that ticker, continue pipeline
- If an agent produces malformed JSON, retry once with a note to fix the JSON
- If Finnhub returns 429 (rate limit), sleep 60 and retry
- Always complete the pipeline even if 1–2 tickers fail

## Position Sizing Rules (ENFORCED BY RiskManager)
- Maximum single position add: €200/month
- Total monthly DCA cap: €400 across all 7 positions
- Position > 30% of portfolio: HARD VETO on any add
- Position > 25%: wait for rebalance before adding
- Earnings within 7 days: reduce suggested amount by 50%
- Correlated pairs (MSFT+AMZN, NVDA+MSFT): only approve higher-conviction one per day
- Portfolio drawdown > 25%: flag caution, prioritize highest-conviction names only
- Portfolio drawdown > 40%: suggest pausing DCA until stabilization
- Apartment fund (€36k): SACRED — never touch, never reference as investable

## Output
Print a summary of the run:
- Tickers completed successfully
- Any errors encountered
- Path to final brief
- Total runtime
