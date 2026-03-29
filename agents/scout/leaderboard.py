"""
Mines the Polymarket leaderboard and clusters wallets by actual trading domain.
Uses CLI for all data — no direct API calls.
"""

import time

from lib.cli import get_leaderboard, get_wallet_trades
from lib.logger import get_logger

logger = get_logger(__name__)

DOMAIN_KEYWORDS = {
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto",
        "price", "market cap", "halving", "defi", "nft",
    ],
    "politics": [
        "election", "president", "senate", "congress", "vote", "party",
        "democrat", "republican", "prime minister", "parliament",
    ],
    "sports": [
        "nba", "nfl", "mlb", "nhl", "super bowl", "champion", "win",
        "playoff", "tournament", "world cup", "league",
    ],
    "macro": [
        "fed", "inflation", "gdp", "rate", "recession", "unemployment",
        "cpi", "fomc", "treasury", "yield",
    ],
    "geopolitics": [
        "war", "ceasefire", "invasion", "sanctions", "ukraine", "russia",
        "china", "taiwan", "nato", "troops", "missile", "coup", "maduro",
        "iran", "middle east", "conflict", "military",
    ],
}


def mine_leaderboard(
    period: str = "month",
    top_n: int = 100,
    domain_filter: str = None,
) -> list[dict]:
    """
    Pull top N wallets from leaderboard, classify by domain, return enriched list.
    domain_filter: if set, only return wallets primarily trading that domain.
    """
    logger.info(f"Mining leaderboard: top {top_n}, period={period}")
    leaderboard = get_leaderboard(period=period, order_by="pnl", limit=top_n)

    if not leaderboard:
        logger.warning("Leaderboard returned no results")
        return []

    enriched = []
    for i, entry in enumerate(leaderboard):
        address = entry.get("proxyWalletAddress") or entry.get("address")
        if not address:
            continue

        logger.info(f"[{i+1}/{len(leaderboard)}] Analysing wallet: {address[:10]}...")

        try:
            trades = get_wallet_trades(address, limit=100)
            time.sleep(0.5)  # Rate limit courtesy pause
        except Exception as e:
            logger.warning(f"Failed to fetch trades for {address}: {e}")
            continue

        if not trades:
            continue

        domain_scores = classify_wallet_domain(trades)
        primary_domain = (
            max(domain_scores, key=domain_scores.get) if domain_scores else "unknown"
        )
        total_score = sum(domain_scores.values())
        domain_confidence = (
            domain_scores.get(primary_domain, 0) / max(total_score, 1)
        )

        wallet_data = {
            "address": address,
            "pnl_usd": entry.get("pnl", 0),
            "primary_domain": primary_domain,
            "domain_confidence": round(domain_confidence, 2),
            "domain_scores": domain_scores,
            "total_trades_sampled": len(trades),
            "leaderboard_rank": i + 1,
        }

        if domain_filter is None or primary_domain == domain_filter:
            enriched.append(wallet_data)

    logger.info(f"Leaderboard mining complete: {len(enriched)} wallets enriched")
    return enriched


def classify_wallet_domain(trades: list) -> dict:
    """
    Score a wallet's domain affinity from their trade question history.
    Returns dict of {domain: score}.
    """
    scores = {domain: 0 for domain in DOMAIN_KEYWORDS}

    for trade in trades:
        question = (trade.get("market", {}).get("question") or "").lower()
        for domain, keywords in DOMAIN_KEYWORDS.items():
            for kw in keywords:
                if kw in question:
                    scores[domain] += 1
                    break  # count market once per domain

    return scores
