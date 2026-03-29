# Polymarket Trading System

Multi-agent system for automated Polymarket trading with human-in-the-loop approval.

## Architecture

- **Scanner** — Monitors RSS news feeds, tracked wallets, and price anomalies 24/7
- **Analyst** — Claude Sonnet scores signal edge and confidence; Haiku screens news relevance
- **Notifier** — Telegram bot sends alerts with approve/reject buttons
- **Executor** — Places orders via `polymarket-cli` after mandatory risk checks
- **LP Manager** — Posts two-sided limit orders for passive LP rebate farming
- **Scout** — One-time setup tool for operator calibration and wallet basket curation

## Strategy Layers

1. **Directional trading** — Trade on genuine knowledge advantage (>=12pp edge)
2. **Wallet basket consensus** — Copy signals when >=70% of vetted wallets agree
3. **LP rebate farming** — Earn spread + USDC rebates on idle capital
4. **AI news scanner** — Claude catches opportunities while operator is offline

## Quick Start

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your API keys

# 3. Run Scout to find your edge and build wallet basket
python scripts/run_scout.py --domain geopolitics

# 4. Configure tracked markets
# Edit config/markets.yaml

# 5. Run tests
pytest tests/ -v

# 6. Start in paper mode
python -m agents.scanner   # or use supervisord
```

## Risk Controls

All trading goes through `lib/risk.py` — the non-negotiable safety layer:
- Max 2% of bankroll per trade
- Max 5 open positions
- No duplicate market exposure
- Min $100 bankroll floor
- Price sanity: never buy above 95c or below 5c
- Min $5 per trade (no dust)
- LP exposure capped at 15% per market

## Fee Structure (March 30, 2026)

| Category | Peak Taker Fee | Min Edge Threshold |
|----------|---------------|--------------------|
| Geopolitics | 0% (free) | 8pp |
| Sports | 0.75% | 11pp |
| Politics/Finance | 1.00% | 13pp |
| Economics | 1.50% | 16pp |
| Crypto | 1.80% | 17pp |

Geopolitics is the best category — zero taker fees means full edge retention.

## Gas Requirements

Before trading, fund your wallet with MATIC for Polygon gas fees:
- Required: 0.5 MATIC minimum (1 MATIC recommended)
- Network: **Polygon Mainnet (Chain ID 137)** — NOT Ethereum mainnet
- One-time cost: ~0.01-0.05 MATIC for the 6 approval transactions

## Paper Trade Mode

Set `paper_trade: true` in `config/settings.yaml` (default). The system runs the full pipeline but saves to `paper_trades` table instead of placing real orders. Test for >=7 days before going live.

## Referral Program

Once your wallet crosses $10,000 in cumulative volume:
1. Go to polymarket.com > Settings > Referrals
2. Earn 30% of fees from direct referrals for 180 days

## POLY Token Airdrop

Polymarket has confirmed a POLY token launch with retroactive airdrop for genuine users.
The system's organic trading activity qualifies naturally. Do NOT add farming logic — anti-Sybil detection will disqualify bots.

## Security Checklist

- [ ] `POLYMARKET_PRIVATE_KEY` stored only as env var, never in config/git
- [ ] `.env` in `.gitignore`
- [ ] VPS firewall: only ports 22 + 443 open
- [ ] SSH key-only auth
- [ ] Paper trade mode tested >=7 days
- [ ] All risk tests passing before deploying updates
