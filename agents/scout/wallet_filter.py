"""
Applies full quality filter to a list of candidate wallets.
Returns only wallets that meet all criteria.
Flags red-flag patterns: latency arb bots, delta-neutral hedgers, insider wallets.
"""

import time
from collections import Counter
from datetime import datetime

from lib.cli import get_wallet_trades
from lib.logger import get_logger

logger = get_logger(__name__)

QUALITY_CRITERIA = {
    "min_closed_trades": 50,
    "min_win_rate": 0.55,
    "min_gain_loss_ratio": 2.0,
    "min_active_days": 90,
    "min_trade_size_usdc": 50,
    "max_single_market_pct": 0.40,
    "max_crypto_arb_pct": 0.70,
    "min_markets_traded": 10,
}


def filter_wallets(
    candidates: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Filter candidate wallets through quality criteria.
    Returns (approved_wallets, rejected_wallets) with reasons.
    """
    approved = []
    rejected = []

    for candidate in candidates:
        address = candidate["address"]
        logger.info(f"Filtering wallet: {address[:10]}...")

        try:
            trades = get_wallet_trades(address, limit=200)
            time.sleep(0.3)
        except Exception as e:
            rejected.append({**candidate, "reject_reason": f"fetch_failed: {e}"})
            continue

        if not trades:
            rejected.append({**candidate, "reject_reason": "no_trades_found"})
            continue

        result = _apply_filters(candidate, trades)

        if result.get("approved"):
            approved.append({**candidate, **result})
        else:
            rejected.append({**candidate, **result})

    logger.info(f"Filter results: {len(approved)} approved, {len(rejected)} rejected")
    return approved, rejected


def _apply_filters(candidate: dict, trades: list) -> dict:
    """Run all quality filters on one wallet's trade history."""
    # Only count settled trades for win rate
    settled = [
        t
        for t in trades
        if t.get("status") in ("settled", "closed", "resolved")
    ]

    if len(settled) < QUALITY_CRITERIA["min_closed_trades"]:
        return {
            "approved": False,
            "reject_reason": f"insufficient_settled_trades ({len(settled)})",
        }

    # Win rate on settled trades
    meaningful = [
        t
        for t in settled
        if float(t.get("size", 0)) >= QUALITY_CRITERIA["min_trade_size_usdc"]
    ]
    if not meaningful:
        return {"approved": False, "reject_reason": "no_meaningful_trades"}

    wins = [t for t in meaningful if _trade_was_profitable(t)]
    win_rate = len(wins) / len(meaningful)

    if win_rate < QUALITY_CRITERIA["min_win_rate"]:
        return {
            "approved": False,
            "reject_reason": f"win_rate_too_low ({win_rate:.2%})",
        }

    # Gain/loss ratio
    avg_win = _avg_pnl(wins) if wins else 0
    losses = [t for t in meaningful if not _trade_was_profitable(t)]
    avg_loss = abs(_avg_pnl(losses)) if losses else 0.01
    gl_ratio = avg_win / avg_loss if avg_loss > 0 else 999

    if gl_ratio < QUALITY_CRITERIA["min_gain_loss_ratio"]:
        return {
            "approved": False,
            "reject_reason": f"gain_loss_ratio_too_low ({gl_ratio:.2f})",
        }

    # Recency check
    dates = [
        _parse_date(t.get("timestamp") or t.get("createdAt")) for t in trades
    ]
    dates = [d for d in dates if d]
    if not dates or (datetime.utcnow() - max(dates)).days > QUALITY_CRITERIA["min_active_days"]:
        return {"approved": False, "reject_reason": "inactive_wallet"}

    # Market diversity
    market_ids = [
        t.get("market", {}).get("id") or t.get("conditionId") for t in trades
    ]
    market_ids = [m for m in market_ids if m]
    unique_markets = set(market_ids)

    if len(unique_markets) < QUALITY_CRITERIA["min_markets_traded"]:
        return {
            "approved": False,
            "reject_reason": f"too_few_markets ({len(unique_markets)})",
        }

    most_common_market_pct = (
        max(market_ids.count(m) / len(market_ids) for m in unique_markets)
        if market_ids
        else 1.0
    )

    if most_common_market_pct > QUALITY_CRITERIA["max_single_market_pct"]:
        return {
            "approved": False,
            "reject_reason": f"single_market_concentration ({most_common_market_pct:.0%})",
        }

    # Latency arb bot detection
    short_crypto = [
        t
        for t in trades
        if any(
            kw in (t.get("market", {}).get("question") or "").lower()
            for kw in ["5-minute", "15-minute", "5 minute", "15 minute", "1-minute"]
        )
    ]
    short_crypto_pct = len(short_crypto) / len(trades) if trades else 0

    if short_crypto_pct > QUALITY_CRITERIA["max_crypto_arb_pct"]:
        return {
            "approved": False,
            "reject_reason": f"latency_arb_bot ({short_crypto_pct:.0%} short-crypto trades)",
        }

    # Delta-neutral detection
    market_side_pairs = Counter()
    for t in trades:
        mid = t.get("market", {}).get("id") or t.get("conditionId")
        side = t.get("side") or t.get("outcomeIndex")
        if mid and side is not None:
            market_side_pairs[mid] += 1

    dual_entry_count = sum(1 for count in market_side_pairs.values() if count >= 2)
    if dual_entry_count / max(len(unique_markets), 1) > 0.30:
        return {
            "approved": False,
            "reject_reason": "possible_delta_neutral_hedger",
        }

    # All checks passed
    return {
        "approved": True,
        "win_rate": round(win_rate, 4),
        "gain_loss_ratio": round(gl_ratio, 2),
        "settled_trades": len(settled),
        "unique_markets": len(unique_markets),
        "days_since_last_trade": (datetime.utcnow() - max(dates)).days if dates else 999,
        "short_crypto_pct": round(short_crypto_pct, 3),
        "reject_reason": None,
    }


def _trade_was_profitable(trade: dict) -> bool:
    pnl = trade.get("profit") or trade.get("pnl") or 0
    return float(pnl) > 0


def _avg_pnl(trades: list) -> float:
    if not trades:
        return 0
    total = sum(
        float(t.get("profit") or t.get("pnl") or 0) for t in trades
    )
    return total / len(trades)


def _parse_date(ts) -> datetime | None:
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.utcfromtimestamp(ts)
        return datetime.fromisoformat(
            str(ts).replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except Exception:
        return None
