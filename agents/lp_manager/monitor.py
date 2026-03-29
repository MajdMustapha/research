"""
WebSocket order book monitor with exponential backoff reconnection.
The ONLY place in the system that uses WebSocket.
"""

import asyncio
import json

from lib.logger import get_logger

logger = get_logger(__name__)

WS_ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_BASE_DELAY = 2  # seconds, doubles each attempt


def _calculate_mid(data: dict) -> float | None:
    """Calculate mid price from order book data."""
    try:
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if bids and asks:
            best_bid = float(bids[0].get("price", 0))
            best_ask = float(asks[0].get("price", 0))
            if best_bid > 0 and best_ask > 0:
                return (best_bid + best_ask) / 2
    except (ValueError, IndexError, KeyError):
        pass
    return None


async def monitor_order_book_with_reconnect(
    token_id: str, on_price_move_callback
) -> None:
    """
    Persistent WebSocket monitor with exponential backoff reconnection.
    Runs indefinitely — only stops on orchestrator shutdown signal.
    Calls callback if mid-price moves >1c from last quote (trigger requote).
    """
    try:
        import websockets
    except ImportError:
        logger.error("websockets package not installed — LP WS monitor disabled")
        return

    attempt = 0
    while True:
        try:
            logger.info(f"WS connecting to order book: {token_id[:12]}...")
            async with websockets.connect(
                WS_ENDPOINT,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                attempt = 0  # reset on successful connect
                await ws.send(
                    json.dumps({
                        "auth": {},
                        "type": "Market",
                        "markets": [token_id],
                    })
                )
                logger.info(f"WS subscribed: {token_id[:12]}...")

                last_mid = None
                async for message in ws:
                    data = json.loads(message)
                    if data.get("event_type") == "book":
                        new_mid = _calculate_mid(data)
                        if new_mid and last_mid and abs(new_mid - last_mid) > 0.01:
                            # Fire-and-forget: don't block WS reads on requote
                            asyncio.create_task(
                                on_price_move_callback(token_id, last_mid, new_mid)
                            )
                        if new_mid:
                            last_mid = new_mid

        except Exception as e:
            attempt += 1
            delay = min(RECONNECT_BASE_DELAY * (2**attempt), 300)  # cap at 5 min
            logger.warning(
                f"WS disconnected ({e}). Reconnecting in {delay}s (attempt {attempt})"
            )
            if attempt >= MAX_RECONNECT_ATTEMPTS:
                logger.error(
                    f"WS max reconnect attempts reached for {token_id[:12]}. Giving up."
                )
                break
            await asyncio.sleep(delay)


async def on_price_move(token_id: str, old_mid: float, new_mid: float) -> None:
    """Default callback: triggers LP requote on significant price move."""
    logger.info(
        f"WS price move detected: {token_id[:12]} {old_mid:.4f} -> {new_mid:.4f}"
    )
    from agents.lp_manager.quoter import refresh_all_lp_markets

    try:
        # Run synchronous requote in thread pool to avoid blocking event loop
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, refresh_all_lp_markets)
    except Exception as e:
        logger.error(f"Failed to refresh LP orders after price move: {e}")
