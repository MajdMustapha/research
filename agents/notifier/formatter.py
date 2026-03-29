"""
Signal → human-readable message formatting.
Used by the notifier bot for consistent message formatting.
"""


def format_signal_summary(signal: dict, market: dict) -> str:
    """Format a signal for logging or display."""
    return (
        f"[{signal.get('source', '?')}] "
        f"{market.get('question', 'Unknown')[:60]} | "
        f"Edge: {signal.get('edge', 0):.2f} | "
        f"Confidence: {signal.get('confidence', 0):.2f} | "
        f"Side: {signal.get('recommended_side', '?')}"
    )


def format_position_summary(position: dict) -> str:
    """Format a position for display."""
    pnl_str = ""
    if position.get("pnl_usdc") is not None:
        pnl_str = f" | PnL: ${position['pnl_usdc']:+.2f}"
    return (
        f"{position['side'].upper()} @ {position['entry_price']:.3f} "
        f"(${position['size_usdc']:.2f}){pnl_str} — "
        f"{(position.get('market_question') or '')[:40]}"
    )


def format_daily_report(data: dict) -> dict:
    """Prepare daily report data for the notifier."""
    starting = data.get("starting_balance", 0)
    ending = data.get("ending_balance", 0)
    change_pct = ((ending - starting) / starting * 100) if starting > 0 else 0

    return {
        "date": data.get("date", "unknown"),
        "starting_balance": starting,
        "ending_balance": ending,
        "change_pct": change_pct,
        "realized_pnl": data.get("realized_pnl", 0),
        "lp_rewards": data.get("lp_rewards_usdc", 0),
        "api_costs": data.get("api_costs_usd", 0),
        "trades_opened": data.get("num_trades_opened", 0),
        "trades_closed": data.get("num_trades_closed", 0),
        "signals_sent": data.get("signals_sent", 0),
        "approved": data.get("approved", 0),
        "rejected": data.get("rejected", 0),
    }
