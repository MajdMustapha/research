"""Event-driven backtesting engine. Replays candles one by one with no lookahead."""

import logging
from dataclasses import dataclass, field
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


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    final_equity: float


class BacktestEngine:
    """
    Candle-by-candle backtester.

    strategy_fn: Callable that takes (df_up_to_current_bar) and returns
                 a dict with keys: signal ("BUY"/"SELL"/"HOLD"), stop_distance (float).
    """

    def __init__(self, config: dict, strategy_fn: Callable, initial_capital: float = 10000.0):
        self.fee_rate = config["backtest"]["fee_rate"]
        self.slippage_rate = config["backtest"]["slippage_rate"]
        self.max_position_pct = config["risk"]["max_position_pct"]
        self.strategy_fn = strategy_fn
        self.initial_capital = initial_capital

    def run(self, df: pd.DataFrame, symbol: str = "BTC/USDT") -> BacktestResult:
        """Run backtest on OHLCV DataFrame. Returns BacktestResult."""
        cash = self.initial_capital
        position: Position | None = None
        trades: list[Trade] = []
        equity_values: list[float] = []

        for i in range(len(df)):
            row = df.iloc[i]
            current_price = row["close"]

            # Calculate current equity
            unrealized = 0.0
            if position is not None:
                if position.side == "BUY":
                    unrealized = (current_price - position.entry_price) * position.size
                else:
                    unrealized = (position.entry_price - current_price) * position.size

            equity = cash + unrealized
            equity_values.append(equity)

            # Check stop-loss on open position
            if position is not None:
                stopped = False
                if position.side == "BUY" and row["low"] <= position.stop_price:
                    exit_price = position.stop_price * (1 - self.slippage_rate)
                    stopped = True
                elif position.side == "SELL" and row["high"] >= position.stop_price:
                    exit_price = position.stop_price * (1 + self.slippage_rate)
                    stopped = True

                if stopped:
                    fee = exit_price * position.size * self.fee_rate
                    if position.side == "BUY":
                        pnl = (exit_price - position.entry_price) * position.size - fee
                    else:
                        pnl = (position.entry_price - exit_price) * position.size - fee

                    trade = Trade(
                        symbol=symbol,
                        side=position.side,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        entry_time=position.entry_time,
                        exit_time=row["timestamp"],
                        size=position.size,
                        pnl=pnl,
                        fees=fee + (position.entry_price * position.size * self.fee_rate),
                    )
                    trades.append(trade)
                    cash += pnl + (position.entry_price * position.size)
                    if position.side == "BUY":
                        cash = self.initial_capital + sum(t.pnl for t in trades)
                    else:
                        cash = self.initial_capital + sum(t.pnl for t in trades)
                    position = None

            # Get strategy signal (only pass data up to current bar — no lookahead)
            df_slice = df.iloc[: i + 1]
            signal_result = self.strategy_fn(df_slice)
            signal = signal_result.get("signal", "HOLD")
            stop_distance = signal_result.get("stop_distance", 0.0)

            # Close opposing position if signal reverses
            if position is not None and signal != "HOLD" and signal != position.side:
                exit_price = current_price * (
                    (1 - self.slippage_rate) if position.side == "BUY" else (1 + self.slippage_rate)
                )
                fee = exit_price * position.size * self.fee_rate
                if position.side == "BUY":
                    pnl = (exit_price - position.entry_price) * position.size - fee
                else:
                    pnl = (position.entry_price - exit_price) * position.size - fee

                trade = Trade(
                    symbol=symbol,
                    side=position.side,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    entry_time=position.entry_time,
                    exit_time=row["timestamp"],
                    size=position.size,
                    pnl=pnl,
                    fees=fee + (position.entry_price * position.size * self.fee_rate),
                )
                trades.append(trade)
                cash = self.initial_capital + sum(t.pnl for t in trades)
                position = None

            # Open new position
            if position is None and signal in ("BUY", "SELL"):
                equity_now = self.initial_capital + sum(t.pnl for t in trades)
                risk_amount = equity_now * self.max_position_pct

                if stop_distance > 0:
                    size = risk_amount / stop_distance
                else:
                    size = risk_amount / current_price

                entry_price = current_price * (
                    (1 + self.slippage_rate) if signal == "BUY" else (1 - self.slippage_rate)
                )
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
                )

        # Close any remaining position at end
        if position is not None:
            final_price = df.iloc[-1]["close"]
            exit_price = final_price * (
                (1 - self.slippage_rate) if position.side == "BUY" else (1 + self.slippage_rate)
            )
            fee = exit_price * position.size * self.fee_rate
            if position.side == "BUY":
                pnl = (exit_price - position.entry_price) * position.size - fee
            else:
                pnl = (position.entry_price - exit_price) * position.size - fee

            trades.append(Trade(
                symbol=symbol,
                side=position.side,
                entry_price=position.entry_price,
                exit_price=exit_price,
                entry_time=position.entry_time,
                exit_time=df.iloc[-1]["timestamp"],
                size=position.size,
                pnl=pnl,
                fees=fee + (position.entry_price * position.size * self.fee_rate),
            ))

        final_equity = self.initial_capital + sum(t.pnl for t in trades)
        equity_series = pd.Series(equity_values, index=df.index)

        return BacktestResult(
            trades=trades,
            equity_curve=equity_series,
            final_equity=final_equity,
        )
