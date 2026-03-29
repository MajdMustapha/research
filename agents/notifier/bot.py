"""
Telegram bot — handles all human communication.
The only agent allowed to communicate with the operator.
"""

import os
import asyncio
from datetime import datetime, timezone

from lib.state import (
    get_signal,
    update_signal_action,
    get_open_positions,
    get_bankroll,
    get_active_lp_orders,
    log_event,
)
from lib.logger import get_logger

logger = get_logger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPERATOR_CHAT_ID = os.getenv("TELEGRAM_OPERATOR_CHAT_ID", "")
_OPERATOR_CHAT_ID_INT = int(OPERATOR_CHAT_ID) if OPERATOR_CHAT_ID.isdigit() else None

# Cached Telegram app — built once, reused across sends
_cached_app = None


def _get_app():
    """Return cached Telegram Application (builds once)."""
    global _cached_app
    if _cached_app is None:
        from telegram.ext import Application
        _cached_app = Application.builder().token(TELEGRAM_TOKEN).build()
    return _cached_app


# ─── Signal Alerts ────────────────────────────────────────────────────────────

async def send_signal_alert(
    signal_id: int, analyst_result: dict, market: dict
) -> str:
    """
    Send formatted signal alert with Approve/Reject buttons.
    Returns telegram message_id for tracking.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    edge_pct = analyst_result["edge"] * 100
    conf_pct = analyst_result["confidence"] * 100
    current_prob = analyst_result["current_market_prob"] * 100
    est_prob = analyst_result["estimated_true_prob"] * 100
    side = analyst_result["recommended_side"].upper()

    text = (
        f"*SIGNAL #{signal_id}*\n\n"
        f"*Market:* {market.get('question', 'Unknown')[:80]}\n\n"
        f"*Side:* {side}\n"
        f"*Market says:* {current_prob:.1f}%\n"
        f"*We estimate:* {est_prob:.1f}%\n"
        f"*Edge:* +{edge_pct:.1f}pp\n"
        f"*Confidence:* {conf_pct:.0f}%\n"
        f"*Hold:* ~{analyst_result.get('hold_duration_days', '?')} days\n\n"
        f"*Reasoning:* {analyst_result.get('reasoning', '')}\n\n"
        f"*Risks:* {', '.join(analyst_result.get('key_risks', []))}\n\n"
        "Expires in 30 minutes"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"APPROVE {side}", callback_data=f"approve_{signal_id}"
            ),
            InlineKeyboardButton("REJECT", callback_data=f"reject_{signal_id}"),
        ]
    ])

    app = _get_app()
    async with app:
        msg = await app.bot.send_message(
            chat_id=OPERATOR_CHAT_ID,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    return str(msg.message_id)


async def handle_callback(update, context) -> None:
    """Handle approve/reject button presses."""
    query = update.callback_query

    # Validate operator
    if _OPERATOR_CHAT_ID_INT is None or query.from_user.id != _OPERATOR_CHAT_ID_INT:
        await query.answer("Unauthorized")
        return

    await query.answer()
    data = query.data

    if data.startswith("approve_"):
        signal_id = int(data.split("_")[1])

        # Check expiry at approval time — don't let stale signals through
        signal = get_signal(signal_id)
        if signal:
            created = datetime.fromisoformat(signal["created_at"])
            age = (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).total_seconds()
            if age > 1800:
                update_signal_action(signal_id, "expired", datetime.now(timezone.utc))
                await query.edit_message_text(
                    f"Signal #{signal_id} EXPIRED — {int(age / 60)} min old"
                )
                return

        try:
            from agents.executor.orders import execute_approved_signal
            await execute_approved_signal(signal_id)
            await query.edit_message_text(
                f"Signal #{signal_id} APPROVED — executing..."
            )
        except Exception as e:
            await query.edit_message_text(
                f"Signal #{signal_id} APPROVE FAILED: {str(e)[:100]}"
            )

    elif data.startswith("reject_"):
        signal_id = int(data.split("_")[1])
        update_signal_action(signal_id, "rejected", datetime.now(timezone.utc))
        await query.edit_message_text(f"Signal #{signal_id} REJECTED")


# ─── Operator Commands ────────────────────────────────────────────────────────

async def handle_status(update, context) -> None:
    """Show current positions, bankroll, LP orders."""
    if _OPERATOR_CHAT_ID_INT is None or update.effective_user.id != _OPERATOR_CHAT_ID_INT:
        return

    bankroll = get_bankroll()
    positions = get_open_positions()
    lp_orders = get_active_lp_orders()

    text = (
        f"*STATUS*\n\n"
        f"Bankroll: ${bankroll:.2f}\n"
        f"Open positions: {len(positions)}\n"
        f"Active LP orders: {len(lp_orders)}\n"
    )

    if positions:
        text += "\n*Positions:*\n"
        for p in positions:
            text += (
                f"  {p['side'].upper()} @ {p['entry_price']:.3f} "
                f"(${p['size_usdc']:.2f}) — {(p.get('market_question') or '')[:40]}\n"
            )

    app = _get_app()
    async with app:
        await app.bot.send_message(
            chat_id=OPERATOR_CHAT_ID, text=text, parse_mode="Markdown"
        )


async def handle_cancel_all(update, context) -> None:
    """Emergency stop — cancel all orders."""
    if _OPERATOR_CHAT_ID_INT is None or update.effective_user.id != _OPERATOR_CHAT_ID_INT:
        return

    from lib.cli import cancel_all_orders

    try:
        cancel_all_orders()
        log_event("emergency", "notifier", "Cancel all orders triggered by operator")
        await update.message.reply_text("All orders cancelled.")
    except Exception as e:
        await update.message.reply_text(f"Cancel failed: {str(e)[:100]}")


# ─── Notifications ────────────────────────────────────────────────────────────

async def send_abort_notification(signal_id: int, reason: str) -> None:
    """Notify operator that a signal was aborted."""
    text = f"Signal #{signal_id} ABORTED\nReason: {reason}"
    app = _get_app()
    async with app:
        await app.bot.send_message(chat_id=OPERATOR_CHAT_ID, text=text)


async def send_paper_trade_confirmation(
    signal_id: int, size: float, price: float, market: dict
) -> None:
    """Notify operator of a paper trade execution."""
    text = (
        f"[PAPER] Signal #{signal_id} executed\n"
        f"Market: {market.get('question', '')[:60]}\n"
        f"Size: ${size:.2f} @ {price:.3f}"
    )
    app = _get_app()
    async with app:
        await app.bot.send_message(chat_id=OPERATOR_CHAT_ID, text=text)


async def send_daily_report(report: dict) -> None:
    """Send midnight UTC daily summary."""
    change_pct = 0
    if report.get("starting_balance") and report["starting_balance"] > 0:
        change_pct = (
            (report["ending_balance"] - report["starting_balance"])
            / report["starting_balance"]
            * 100
        )

    net = (
        report.get("realized_pnl", 0)
        + report.get("lp_rewards", 0)
        - report.get("api_costs", 0)
    )

    text = (
        f"*Daily Report — {report['date']}*\n\n"
        f"Bankroll: ${report['ending_balance']:.2f} ({change_pct:+.1f}%)\n"
        f"Realized P&L: ${report.get('realized_pnl', 0):+.2f}\n"
        f"LP Rewards: ${report.get('lp_rewards', 0):+.2f}\n"
        f"API Costs: -${report.get('api_costs', 0):.2f}\n"
        f"Net: ${net:+.2f}\n\n"
        f"Trades opened: {report.get('trades_opened', 0)}\n"
        f"Trades closed: {report.get('trades_closed', 0)}\n"
        f"Signals sent: {report.get('signals_sent', 0)} | "
        f"Approved: {report.get('approved', 0)} | "
        f"Rejected: {report.get('rejected', 0)}"
    )
    app = _get_app()
    async with app:
        await app.bot.send_message(
            chat_id=OPERATOR_CHAT_ID, text=text, parse_mode="Markdown"
        )


async def send_scout_report(summary: dict) -> None:
    """Send Scout agent results."""
    text = (
        f"*Scout Report — {summary['domain']}*\n\n"
        f"Wallets found: {summary['wallets_found']}\n"
        f"Calibration edge: {'YES' if summary['calibration_has_edge'] else 'NO'}\n"
        f"Top wallet score: {summary['top_wallet_score']:.3f}\n\n"
        f"{summary['verdict']}"
    )
    app = _get_app()
    async with app:
        await app.bot.send_message(
            chat_id=OPERATOR_CHAT_ID, text=text, parse_mode="Markdown"
        )


async def send_drift_alert(report: dict) -> None:
    """Send wallet drift alert."""
    text = "*Wallet Drift Alert*\n\n"
    for domain, data in report.get("domains", {}).items():
        if data.get("degraded", 0) > 0:
            text += f"{domain}: {data['degraded']} degraded wallet(s)\n"
    text += "\nRun scout with --drift-check for details."
    app = _get_app()
    async with app:
        await app.bot.send_message(
            chat_id=OPERATOR_CHAT_ID, text=text, parse_mode="Markdown"
        )
