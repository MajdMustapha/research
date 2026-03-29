---
description: Polymarket CLI — browse markets, check prices, search events, view orderbooks, manage positions, and place trades. Uses the official `polymarket` CLI tool.
---

You have access to the Polymarket CLI (`polymarket`) installed at `/usr/local/bin/polymarket`. Use it to interact with Polymarket's prediction markets.

When the user provides arguments after `/poly`, treat them as a natural language query or direct CLI arguments. Examples:
- `/poly search bitcoin` → search for bitcoin markets
- `/poly weather` → list weather events
- `/poly price <TOKEN_ID>` → get a token price
- `/poly book <TOKEN_ID>` → show orderbook
- `/poly positions 0x...` → check wallet positions

## Available Commands

### Browsing (no wallet needed)

**Search markets:**
```bash
polymarket -o json markets search "<query>" --limit 10
```

**List events by tag:**
```bash
polymarket -o json events list --tag <tag> --active true --limit 10
```
Common tags: Weather, Sports, Crypto, Politics, Science, Culture, AI

**Get market details:**
```bash
polymarket -o json markets get <slug>
```

**Get orderbook:**
```bash
polymarket -o json clob book <TOKEN_ID>
```

**Get price / spread / midpoint:**
```bash
polymarket -o json clob price <TOKEN_ID> --side buy
polymarket -o json clob spread <TOKEN_ID>
polymarket -o json clob midpoint <TOKEN_ID>
```

**Price history:**
```bash
polymarket -o json clob price-history <TOKEN_ID> --interval 1d --fidelity 30
```

**Check a wallet's positions:**
```bash
polymarket -o json data positions <WALLET_ADDRESS>
polymarket -o json data value <WALLET_ADDRESS>
```

### Trading (requires wallet config)

**Place limit order:**
```bash
polymarket clob create-order --token <TOKEN_ID> --side buy --price 0.50 --size 10
```

**Place market order:**
```bash
polymarket clob market-order --token <TOKEN_ID> --side buy --amount 5
```

**Cancel orders:**
```bash
polymarket clob cancel <ORDER_ID>
polymarket clob cancel-all
```

**Check balance:**
```bash
polymarket clob balance --asset-type collateral
```

**View open orders and trades:**
```bash
polymarket clob orders
polymarket clob trades
```

## How to Use

When the user asks you to do something with Polymarket, use the CLI. Always use `-o json` for programmatic access and pipe through `python3 -m json.tool` for readability when showing results to the user.

**Examples of user requests and what to run:**

- "What's hot on Polymarket?" → `polymarket -o json events list --active true --limit 20` then summarize
- "Find crypto markets" → `polymarket -o json markets search "bitcoin" --limit 10`
- "What's the price on X?" → Search for the market, get token IDs, then `polymarket clob price <TOKEN_ID>`
- "Show me the orderbook" → `polymarket clob book <TOKEN_ID>`
- "What weather markets are active?" → `polymarket -o json events list --tag Weather --active true --limit 20`
- "Check wallet 0x..." → `polymarket -o json data positions 0x...`
- "Place a bet on X" → ALWAYS confirm with user before executing any trade. Check DRY_RUN mode first.

## Safety Rules

1. **NEVER place a trade without explicit user confirmation**
2. **NEVER use the user's private key in command output or logs**
3. Always use `--side buy` unless the user specifically asks to sell
4. Always show the user the market details and price before placing any order
5. For large orders (>$50), warn the user about slippage and suggest checking the orderbook first

## Integration with Bot

The bot backend runs at `http://localhost:8000`. You can combine CLI data with bot actions:
- Use CLI for market discovery and price checking
- Use bot API (`/api/scan/trigger`, `/api/trades`, `/api/signals`) for automated scanning
- Use CLI for manual trade execution when the bot finds signals in dry-run mode
- New CLI wrapper endpoints: `/api/poly/search`, `/api/poly/weather-events`, `/api/poly/price/{token_id}`

## Output Format

Always use `-o json` and parse the results. Present key info in a clean summary:
```
Market: [question]
Price:  YES $0.XX / NO $0.XX
Volume: $XXX,XXX
End:    YYYY-MM-DD
Spread: X.X%
```

User arguments: $ARGUMENTS
