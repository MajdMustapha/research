"""Event-driven backtesting engine. Replays candles one by one with no lookahead."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    symbol: str
    side: str  # "BUY" or "SELL"
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    size: float
    pnl: float
    fees: float


@dataclass
class Position:
    symbol: str
    side: str
    size: float
    entry_price: float
    stop_price: float
    entry_time: datetime
    entry_fee: float = 0.0
    stop_distance: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    final_equity: float


class BacktestEngine:
    """
    Candle-by-candle backtester.

    strategy_fn: Callable that takes (df_up_to_current_bar, position_side=None)
                 and returns dict with keys: signal ("BUY"/"SELL"/"HOLD"/"EXIT"),
                 stop_distance (float).
    """

    def __init__(self, config: dict, strategy_fn: Callable, initial_capital: float = 10000.0):
        self.fee_rate = config["backtest"]["fee_rate"]
        self.slippage_rate = config["backtest"]["slippage_rate"]
        self.max_position_pct = config["risk"]["max_position_pct"]
        self.strategy_fn = strategy_fn
        self.initial_capital = initial_capital

    def _close_position(self, position: Position, exit_price: float,
                        exit_time: datetime, symbol: str) -> Trade:
        """Close a position and return the completed Trade with correct fee handling."""
        exit_fee = exit_price * position.size * self.fee_rate
        total_fees = position.entry_fee + exit_fee

        if position.side == "BUY":
            pnl = (exit_price - position.entry_price) * position.size - total_fees
        else:
            pnl = (position.entry_price - exit_price) * position.size - total_fees

        return Trade(
            symbol=symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            entry_time=position.entry_time,
            exit_time=exit_time,
            size=position.size,
            pnl=pnl,
            fees=total_fees,
        )

    def run(self, df: pd.DataFrame, symbol: str = "BTC/USDT") -> BacktestResult:
        """Run backtest on OHLCV DataFrame. Returns BacktestResult."""
        realized_pnl = 0.0
        position: Position | None = None
        trades: list[Trade] = []
        equity_values: list[float] = []

        for i in range(len(df)):
            row = df.iloc[i]
            current_price = row["close"]

            # 1. Compute equity = initial_capital + realized_pnl + unrealized
            unrealized = 0.0
            if position is not None:
                if position.side == "BUY":
                    unrealized = (current_price - position.entry_price) * position.size
                else:
                    unrealized = (position.entry_price - current_price) * position.size

            equity = self.initial_capital + realized_pnl + unrealized
            equity_values.append(equity)

            # 2. Update trailing stop (if position exists and profitable)
            if position is not None and position.stop_distance > 0:
                if position.side == "BUY":
                    trail_stop = row["high"] - position.stop_distance
                    if trail_stop > position.stop_price:
                        position.stop_price = trail_stop
                else:
                    trail_stop = row["low"] + position.stop_distance
                    if trail_stop < position.stop_price:
                        position.stop_price = trail_stop

            # 3. Check stop-loss
            if position is not None:
                stopped = False
                exit_price = 0.0
                if position.side == "BUY" and row["low"] <= position.stop_price:
                    exit_price = position.stop_price * (1 - self.slippage_rate)
                    stopped = True
                elif position.side == "SELL" and row["high"] >= position.stop_price:
                    exit_price = position.stop_price * (1 + self.slippage_rate)
                    stopped = True

                if stopped:
                    trade = self._close_position(position, exit_price, row["timestamp"], symbol)
                    trades.append(trade)
                    realized_pnl += trade.pnl
                    position = None

            # 4. Get strategy signal (pass position side for exit logic)
            df_slice = df.iloc[: i + 1]
            position_side = position.side if position is not None else None
            signal_result = self.strategy_fn(df_slice, position_side=position_side)
            signal = signal_result.get("signal", "HOLD")
            stop_distance = signal_result.get("stop_distance", 0.0)

            # 5. Handle EXIT signal — close without opening new position
            if signal == "EXIT" and position is not None:
                exit_price = current_price * (
                    (1 - self.slippage_rate) if position.side == "BUY" else (1 + self.slippage_rate)
                )
                trade = self._close_position(position, exit_price, row["timestamp"], symbol)
                trades.append(trade)
                realized_pnl += trade.pnl
                position = None
                signal = "HOLD"  # Don't open a new position

            # 6. Close opposing position if signal reverses
            if position is not None and signal in ("BUY", "SELL") and signal != position.side:
                exit_price = current_price * (
                    (1 - self.slippage_rate) if position.side == "BUY" else (1 + self.slippage_rate)
                )
                trade = self._close_position(position, exit_price, row["timestamp"], symbol)
                trades.append(trade)
                realized_pnl += trade.pnl
                position = None

            # 7. Open new position
            if position is None and signal in ("BUY", "SELL"):
                equity_now = self.initial_capital + realized_pnl

                if equity_now <= 0:
                    continue

                risk_amount = equity_now * self.max_position_pct

                entry_price = current_price * (
                    (1 + self.slippage_rate) if signal == "BUY" else (1 - self.slippage_rate)
                )

                if stop_distance > 0:
                    size = risk_amount / stop_distance
                else:
                    size = risk_amount / entry_price

                # Cap position so notional doesn't exceed equity
                max_size = equity_now / entry_price
                size = min(size, max_size)

                if size <= 0:
                    continue

                entry_fee = entry_price * size * self.fee_rate

                if signal == "BUY":
                    stop_price = entry_price - stop_distance
                else:
                    stop_price = entry_price + stop_distance

                position = Position(
                    symbol=symbol,
                    side=signal,
                    size=size,
                    entry_price=entry_price,
                    stop_price=stop_price,
                    entry_time=row["timestamp"],
                    entry_fee=entry_fee,
                    stop_distance=stop_distance,
                )

        # Close any remaining position at end
        if position is not None:
            final_price = df.iloc[-1]["close"]
            exit_price = final_price * (
                (1 - self.slippage_rate) if position.side == "BUY" else (1 + self.slippage_rate)
            )
            trade = self._close_position(position, exit_price, df.iloc[-1]["timestamp"], symbol)
            trades.append(trade)
            realized_pnl += trade.pnl

        final_equity = self.initial_capital + realized_pnl
        equity_series = pd.Series(equity_values, index=df.index)

        return BacktestResult(
            trades=trades,
            equity_curve=equity_series,
            final_equity=final_equity,
        )
