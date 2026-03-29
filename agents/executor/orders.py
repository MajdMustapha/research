"""
Order execution agent — the only agent that places directional orders.
Always calls risk_check() before any order.
Routes to paper or live execution based on settings.
"""

import os
from datetime import datetime, timezone

import yaml

from lib.risk import risk_check, RiskResult
from lib.cli import (
    create_limit_order,
    get_market,
    get_market_price,
    get_fee_rate,
    PolymarketCLIError,
)
from lib.state import (
    get_signal,
    save_position,
    save_paper_trade,
    update_signal_action,
    get_bankroll,
    log_event,
)
from lib.logger import get_logger

logger = get_logger(__name__)


def _load_settings() -> dict:
    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        "settings.yaml",
    )
    with open(settings_path) as f:
        return yaml.safe_load(f)


def _is_paper_trade_mode() -> bool:
    return _load_settings().get("system", {}).get("paper_trade", True)


async def execute_approved_signal(signal_id: int) -> None:
    """Entry point — routes to paper or live execution."""
    if _is_paper_trade_mode():
        return await _execute_paper(signal_id)
    return await _execute_live(signal_id)


async def _execute_paper(signal_id: int) -> None:
    """
    Paper trade: run full pipeline except the final CLI order call.
    Saves to paper_trades table. Sends Telegram confirmation.
    """
    signal = get_signal(signal_id)
    if not signal or signal["action_taken"] is not None:
        logger.warning(f"Signal {signal_id} already actioned or not found")
        return

    # Check expiry (30 min window)
    created = datetime.fromisoformat(signal["created_at"])
    if (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).total_seconds() > 1800:
        update_signal_action(signal_id, "expired", datetime.now(timezone.utc))
        logger.warning(f"Signal {signal_id} expired before execution")
        return

    market = get_market(signal["market_id"])
    price_data = get_market_price(signal["token_id"])
    current_price = float(price_data["mid"])

    bankroll = get_bankroll()
    requested_size = bankroll * 0.02

    risk = risk_check(
        market_id=signal["market_id"],
        side="buy",
        price=current_price,
        requested_size=requested_size,
        token_id=signal["token_id"],
    )
    if not risk.approved:
        await _abort(signal_id, f"[PAPER] Risk check failed: {risk.reason}")
        return

    final_size = risk.capped_size or requested_size

    save_paper_trade({
        "signal_id": signal_id,
        "market_id": signal["market_id"],
        "token_id": signal["token_id"],
        "market_question": market.get("question"),
        "side": signal["recommended_side"],
        "entry_price": current_price,
        "size_usdc": final_size,
        "shares": final_size / current_price,
        "layer": signal.get("source"),
        "signal_summary": signal.get("claude_reasoning", ""),
    })

    update_signal_action(signal_id, "approved_paper", datetime.now(timezone.utc))
    logger.info(
        f"[PAPER TRADE] Would have bought {final_size:.2f} USDC at "
        f"{current_price:.3f} — {market.get('question', '')[:60]}"
    )

    try:
        from agents.notifier.bot import send_paper_trade_confirmation
        await send_paper_trade_confirmation(signal_id, final_size, current_price, market)
    except Exception as e:
        logger.warning(f"Failed to send paper trade notification: {e}")


async def _execute_live(signal_id: int) -> None:
    """
    Live execution: risk check → price sanity → place order → save position.
    """
    signal = get_signal(signal_id)
    if not signal or signal["action_taken"] is not None:
        logger.warning(f"Signal {signal_id} already actioned or not found")
        return

    # Check expiry
    created = datetime.fromisoformat(signal["created_at"])
    if (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).total_seconds() > 1800:
        update_signal_action(signal_id, "expired", datetime.now(timezone.utc))
        logger.warning(f"Signal {signal_id} expired before execution")
        return

    market_id = signal["market_id"]
    market = get_market(market_id)

    # Get current price (may have moved since signal)
    price_data = get_market_price(signal["token_id"])
    current_price = float(price_data["mid"])

    # Sanity check: price hasn't moved too much against us
    signal_price = signal["current_price"]
    recommended_side = signal["recommended_side"]

    if recommended_side == "yes" and current_price > signal_price + 0.08:
        await _abort(
            signal_id,
            f"Price moved against us: was {signal_price:.2f}, now {current_price:.2f}",
        )
        return
    if recommended_side == "no" and current_price < signal_price - 0.08:
        await _abort(
            signal_id,
            f"Price moved against us: was {signal_price:.2f}, now {current_price:.2f}",
        )
        return

    # Fetch real-time fee rate before every order
    try:
        fee_data = get_fee_rate(signal["token_id"])
        maker_fee = float(fee_data.get("makerFeeRate", 0))
        logger.info(f"Fee rate for {signal['token_id'][:12]}: maker={maker_fee}")
    except Exception as e:
        logger.warning(f"Could not fetch fee rate: {e}")

    bankroll = get_bankroll()
    requested_size_usdc = bankroll * 0.02

    # MANDATORY RISK CHECK
    risk = risk_check(
        market_id=market_id,
        side="buy",
        price=current_price,
        requested_size=requested_size_usdc,
        token_id=signal["token_id"],
    )

    if not risk.approved:
        await _abort(signal_id, f"Risk check failed: {risk.reason}")
        return

    final_size_usdc = risk.capped_size or requested_size_usdc
    shares = final_size_usdc / current_price

    # Place order (post-only = maker, zero fees)
    try:
        order_result = create_limit_order(
            token_id=signal["token_id"],
            side="buy",
            price=current_price,
            size=round(shares, 2),
            post_only=True,
        )

        save_position({
            "market_id": market_id,
            "token_id": signal["token_id"],
            "market_question": market.get("question"),
            "side": recommended_side,
            "entry_price": current_price,
            "size_usdc": final_size_usdc,
            "shares": shares,
            "order_id": order_result.get("orderID"),
            "layer": signal.get("source", "unknown"),
            "signal_summary": signal.get("claude_reasoning", ""),
        })

        update_signal_action(signal_id, "approved", datetime.now(timezone.utc))
        logger.info(
            f"Order placed: {order_result.get('orderID')} | "
            f"{final_size_usdc:.2f} USDC | {market.get('question', '')[:60]}"
        )
        log_event(
            "trade",
            "executor",
            f"Order {order_result.get('orderID')} placed for signal {signal_id}",
        )

    except PolymarketCLIError as e:
        logger.error(f"Order placement failed for signal {signal_id}: {e}")
        await _abort(signal_id, f"CLI error: {str(e)[:100]}")


async def _abort(signal_id: int, reason: str) -> None:
    """Abort signal execution and notify operator."""
    update_signal_action(signal_id, "rejected", datetime.now(timezone.utc))
    logger.warning(f"Signal {signal_id} aborted: {reason}")
    log_event("risk_block", "executor", f"Signal {signal_id}: {reason}")

    try:
        from agents.notifier.bot import send_abort_notification
        await send_abort_notification(signal_id, reason)
    except Exception as e:
        logger.warning(f"Failed to send abort notification: {e}")
