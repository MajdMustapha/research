"""Order management: validates, sizes, and submits orders."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from data.indicators import atr
from execution.exchange_client import ExchangeClient
from risk.circuit_breaker import CircuitBreaker
from risk.position_sizer import PositionSizer
from strategy.signal_aggregator import Signal

logger = logging.getLogger(__name__)


@dataclass
class ManagedOrder:
    symbol: str
    side: str
    size: float
    entry_price: float
    stop_price: float
    timestamp: datetime


class OrderManager:
    """
    Manages order lifecycle:
    1. Check circuit breaker
    2. Calculate position size
    3. Submit to exchange
    """

    def __init__(
        self,
        exchange_client: ExchangeClient,
        position_sizer: PositionSizer,
        circuit_breaker: CircuitBreaker,
        config: dict,
    ):
        self.exchange = exchange_client
        self.sizer = position_sizer
        self.circuit_breaker = circuit_breaker
        self.config = config
        self.max_open_positions = config["trading"]["max_open_positions"]

    def submit_order(
        self,
        signal: Signal,
        current_price: float,
        equity: float,
        open_position_count: int,
    ) -> ManagedOrder | None:
        """
        Validate and submit an order based on a signal.
        Returns ManagedOrder if placed, None if blocked.
        """
        if signal.direction == "HOLD":
            return None

        if not self.circuit_breaker.can_trade():
            logger.warning(f"Order blocked: circuit breaker state={self.circuit_breaker.state.value}")
            return None

        if open_position_count >= self.max_open_positions:
            logger.warning(f"Order blocked: max open positions ({self.max_open_positions}) reached")
            return None

        # Calculate stop price
        if signal.stop_distance <= 0:
            logger.warning("Order blocked: no valid stop distance")
            return None

        if signal.direction == "BUY":
            stop_price = current_price - signal.stop_distance
        else:
            stop_price = current_price + signal.stop_distance

        # Calculate size
        size = self.sizer.calculate(equity, current_price, stop_price)
        if size <= 0:
            logger.warning("Order blocked: calculated size is zero")
            return None

        # Submit to exchange
        try:
            order = self.exchange.create_market_order(
                symbol=signal.timestamp is not None and "BTC/USDT" or "BTC/USDT",  # placeholder
                side=signal.direction.lower(),
                amount=size,
            )
            logger.info(
                f"Order submitted: {signal.direction} {size:.6f} @ ~{current_price:.2f}, "
                f"stop={stop_price:.2f}"
            )
            return ManagedOrder(
                symbol=order.symbol,
                side=signal.direction,
                size=size,
                entry_price=order.price,
                stop_price=stop_price,
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error(f"Order submission failed: {e}")
            return None

    def submit_order_for_symbol(
        self,
        symbol: str,
        signal: Signal,
        current_price: float,
        equity: float,
        open_position_count: int,
    ) -> ManagedOrder | None:
        """Submit an order for a specific symbol."""
        if signal.direction == "HOLD":
            return None

        if not self.circuit_breaker.can_trade():
            logger.warning(f"Order blocked: circuit breaker state={self.circuit_breaker.state.value}")
            return None

        if open_position_count >= self.max_open_positions:
            logger.warning(f"Order blocked: max open positions reached")
            return None

        if signal.stop_distance <= 0:
            logger.warning("Order blocked: no valid stop distance")
            return None

        if signal.direction == "BUY":
            stop_price = current_price - signal.stop_distance
        else:
            stop_price = current_price + signal.stop_distance

        size = self.sizer.calculate(equity, current_price, stop_price)
        if size <= 0:
            return None

        try:
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=signal.direction.lower(),
                amount=size,
            )
            return ManagedOrder(
                symbol=symbol,
                side=signal.direction,
                size=size,
                entry_price=order.price,
                stop_price=stop_price,
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error(f"Order submission failed for {symbol}: {e}")
            return None
