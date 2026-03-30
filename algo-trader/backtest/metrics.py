"""Performance metrics for backtest results."""

import numpy as np
import pandas as pd

from backtest.engine import BacktestResult, Trade

# Annualization factor for hourly data (8760 hours/year)
HOURS_PER_YEAR = 8760


def sharpe_ratio(equity_curve: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Annualized Sharpe ratio from hourly equity curve."""
    returns = equity_curve.pct_change().dropna()
    if returns.std() == 0 or len(returns) < 2:
        return 0.0
    hourly_rf = risk_free_rate / HOURS_PER_YEAR
    excess = returns.mean() - hourly_rf
    return float(excess / returns.std() * np.sqrt(HOURS_PER_YEAR))


def sortino_ratio(equity_curve: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    returns = equity_curve.pct_change().dropna()
    if len(returns) < 2:
        return 0.0
    hourly_rf = risk_free_rate / HOURS_PER_YEAR
    excess = returns.mean() - hourly_rf
    downside = returns[returns < 0]
    if downside.empty or downside.std() == 0:
        return float("inf") if excess > 0 else 0.0
    return float(excess / downside.std() * np.sqrt(HOURS_PER_YEAR))


def max_drawdown(equity_curve: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as a fraction (0 to 1)."""
    peak = equity_curve.cummax()
    drawdown = (peak - equity_curve) / peak
    return float(drawdown.max()) if not drawdown.empty else 0.0


def profit_factor(trades: list[Trade]) -> float:
    """Gross profit / gross loss. Returns inf if no losses."""
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def win_rate(trades: list[Trade]) -> float:
    """Fraction of trades that are profitable."""
    if not trades:
        return 0.0
    winners = sum(1 for t in trades if t.pnl > 0)
    return winners / len(trades)


def avg_trade_pnl(trades: list[Trade]) -> float:
    """Average PnL per trade."""
    if not trades:
        return 0.0
    return sum(t.pnl for t in trades) / len(trades)


def summary(result: BacktestResult) -> dict:
    """Compute all metrics and return as a dict."""
    return {
        "total_trades": len(result.trades),
        "final_equity": result.final_equity,
        "total_pnl": result.final_equity - result.equity_curve.iloc[0] if len(result.equity_curve) > 0 else 0,
        "sharpe_ratio": sharpe_ratio(result.equity_curve),
        "sortino_ratio": sortino_ratio(result.equity_curve),
        "max_drawdown": max_drawdown(result.equity_curve),
        "profit_factor": profit_factor(result.trades),
        "win_rate": win_rate(result.trades),
        "avg_trade_pnl": avg_trade_pnl(result.trades),
        "total_fees": sum(t.fees for t in result.trades),
    }
