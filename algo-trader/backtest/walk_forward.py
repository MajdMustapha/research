"""Walk-forward validation with rolling train/test windows."""

import logging
from dataclasses import dataclass

import pandas as pd

from backtest.engine import BacktestEngine, BacktestResult
from backtest.metrics import sharpe_ratio, summary

logger = logging.getLogger(__name__)


@dataclass
class WindowResult:
    window_index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    in_sample_sharpe: float
    out_of_sample_sharpe: float
    passed: bool
    test_metrics: dict


@dataclass
class WalkForwardResult:
    windows: list[WindowResult]
    pass_rate: float
    passed: bool


class WalkForwardValidator:
    """
    Rolling walk-forward validation.
    Train on N months, test on M months, slide forward by M months.
    Gate: OOS Sharpe > threshold in >= pass_rate of windows.
    """

    def __init__(self, config: dict):
        self.train_months = config["backtest"]["train_months"]
        self.test_months = config["backtest"]["test_months"]
        self.sharpe_gate = config["backtest"]["sharpe_gate"]
        self.min_pass_rate = config["backtest"]["pass_rate"]
        self.config = config

    def run(
        self,
        df: pd.DataFrame,
        strategy_fn_factory,
        symbol: str = "BTC/USDT",
        initial_capital: float = 10000.0,
    ) -> WalkForwardResult:
        """
        Run walk-forward validation.

        strategy_fn_factory: Callable that takes (train_df) and returns
                            a strategy_fn for use with BacktestEngine.
        """
        windows: list[WindowResult] = []

        # Calculate window boundaries
        train_td = pd.DateOffset(months=self.train_months)
        test_td = pd.DateOffset(months=self.test_months)

        start = df["timestamp"].min()
        end = df["timestamp"].max()

        window_start = start
        window_idx = 0

        while True:
            train_start = window_start
            train_end = train_start + train_td
            test_start = train_end
            test_end = test_start + test_td

            if test_end > end:
                break

            # Split data
            train_mask = (df["timestamp"] >= train_start) & (df["timestamp"] < train_end)
            test_mask = (df["timestamp"] >= test_start) & (df["timestamp"] < test_end)

            train_df = df[train_mask].reset_index(drop=True)
            test_df = df[test_mask].reset_index(drop=True)

            if len(train_df) < 100 or len(test_df) < 50:
                window_start = window_start + test_td
                continue

            # Build strategy from training data
            strategy_fn = strategy_fn_factory(train_df)

            # In-sample backtest
            engine = BacktestEngine(self.config, strategy_fn, initial_capital)
            train_result = engine.run(train_df, symbol)
            is_sharpe = sharpe_ratio(train_result.equity_curve)

            # Out-of-sample backtest
            test_result = engine.run(test_df, symbol)
            oos_sharpe = sharpe_ratio(test_result.equity_curve)
            test_metrics = summary(test_result)

            passed = oos_sharpe >= self.sharpe_gate

            window = WindowResult(
                window_index=window_idx,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                in_sample_sharpe=is_sharpe,
                out_of_sample_sharpe=oos_sharpe,
                passed=passed,
                test_metrics=test_metrics,
            )
            windows.append(window)

            logger.info(
                f"Window {window_idx}: IS Sharpe={is_sharpe:.2f}, "
                f"OOS Sharpe={oos_sharpe:.2f}, Passed={passed}"
            )

            window_start = window_start + test_td
            window_idx += 1

        if not windows:
            return WalkForwardResult(windows=[], pass_rate=0.0, passed=False)

        pass_count = sum(1 for w in windows if w.passed)
        pass_rate = pass_count / len(windows)
        overall_passed = pass_rate >= self.min_pass_rate

        logger.info(
            f"Walk-forward complete: {pass_count}/{len(windows)} windows passed "
            f"({pass_rate:.1%}), gate={'PASSED' if overall_passed else 'FAILED'}"
        )

        return WalkForwardResult(
            windows=windows,
            pass_rate=pass_rate,
            passed=overall_passed,
        )
