"""
Algo Trader — Main entry point and orchestrator.

Usage:
    python main.py --mode backtest    # Run backtest on historical data
    python main.py --mode paper       # Paper trade on testnet
    python main.py --mode live        # Live trading (requires confirmation)
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from backtest.engine import BacktestEngine
from backtest.metrics import summary
from backtest.walk_forward import WalkForwardValidator
from data.data_feed import DataFeed
from data.historical_loader import HistoricalLoader
from execution.exchange_client import ExchangeClient
from execution.order_manager import OrderManager
from execution.position_tracker import PositionTracker
from monitoring.alerting import TelegramAlerter
from monitoring.health_check import HealthCheck
from monitoring.log_config import setup_logging
from reporting.dashboard import set_state, start_dashboard
from reporting.trade_logger import TradeLogger
from risk.circuit_breaker import CircuitBreaker
from risk.position_sizer import PositionSizer
from risk.risk_manager import RiskManager
from strategy.signal_aggregator import SignalAggregator

logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def run_backtest(config: dict):
    """Run backtest on historical data and print metrics."""
    logger.info("Starting backtest mode")

    loader = HistoricalLoader(config)
    aggregator = SignalAggregator(config)
    strategy_fn = aggregator.as_strategy_fn()

    symbols = config["trading"]["symbols"]
    capital = config["trading"]["portfolio_value_usd"]

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * 2)  # 2 years

    for symbol in symbols:
        logger.info(f"Backtesting {symbol}...")
        try:
            df = loader.load_or_fetch(symbol, config["trading"]["timeframe"], start, end)
        except Exception as e:
            logger.error(f"Failed to load data for {symbol}: {e}")
            continue

        if df.empty or len(df) < 100:
            logger.warning(f"Insufficient data for {symbol}: {len(df)} candles")
            continue

        engine = BacktestEngine(config, strategy_fn, initial_capital=capital)
        result = engine.run(df, symbol)
        metrics = summary(result)

        logger.info(f"\n{'='*50}")
        logger.info(f"Backtest Results: {symbol}")
        logger.info(f"{'='*50}")
        for key, value in metrics.items():
            if isinstance(value, float):
                logger.info(f"  {key}: {value:.4f}")
            else:
                logger.info(f"  {key}: {value}")

    # Walk-forward validation
    logger.info("\nRunning walk-forward validation...")
    validator = WalkForwardValidator(config)

    for symbol in symbols:
        try:
            df = loader.load_or_fetch(symbol, config["trading"]["timeframe"], start, end)
        except Exception:
            continue

        if df.empty or len(df) < 100:
            continue

        def strategy_factory(train_df):
            agg = SignalAggregator(config)
            return agg.as_strategy_fn()

        wf_result = validator.run(df, strategy_factory, symbol, capital)
        logger.info(f"{symbol} walk-forward: {'PASSED' if wf_result.passed else 'FAILED'} "
                    f"({wf_result.pass_rate:.1%})")


def run_paper(config: dict):
    """Paper trade on exchange testnet."""
    if not config["exchange"].get("testnet", True):
        logger.error("Paper mode requires testnet=true in config.yaml")
        sys.exit(1)

    logger.info("Starting paper trading mode")

    # Initialize components
    exchange_client = ExchangeClient(config)
    loader = HistoricalLoader(config)
    aggregator = SignalAggregator(config)
    position_sizer = PositionSizer(config)
    risk_manager = RiskManager(config)
    circuit_breaker = CircuitBreaker(config)
    order_manager = OrderManager(exchange_client, position_sizer, circuit_breaker, config)
    position_tracker = PositionTracker()
    trade_logger = TradeLogger(config)
    alerter = TelegramAlerter(config)
    health_check = HealthCheck(config, alerter)
    health_check.set_exchange_client(exchange_client)

    # Dashboard
    set_state(
        circuit_breaker=circuit_breaker,
        position_tracker=position_tracker,
        risk_manager=risk_manager,
        trade_logger=trade_logger,
        config=config,
    )
    start_dashboard(config)

    # Data feed
    data_feed = DataFeed(config, loader)

    symbols = config["trading"]["symbols"]
    capital = config["trading"]["portfolio_value_usd"]
    peak_equity = capital

    risk_manager.reset_daily(capital)
    health_check.start()
    data_feed.start()

    logger.info(f"Trading symbols: {symbols}")
    logger.info(f"Initial capital: {capital} USDT")

    try:
        while True:
            health_check.heartbeat()

            for symbol in symbols:
                try:
                    df = data_feed.get_latest(symbol, limit=100)
                    if df.empty or len(df) < 60:
                        continue

                    # Get signal
                    signal = aggregator.get_signal(df)

                    # Calculate current equity
                    current_price = df["close"].iloc[-1]
                    position_tracker.update_prices({symbol: current_price})
                    equity = capital + sum(
                        t.pnl for t in []  # trades tracked in logger
                    ) + position_tracker.total_unrealized_pnl()

                    peak_equity = max(peak_equity, equity)

                    # Update risk state
                    daily_pnl_pct = risk_manager.daily_pnl_pct(equity)
                    circuit_breaker.update(equity, peak_equity, daily_pnl_pct)

                    # Check stops on open positions
                    pos = position_tracker.get_position(symbol)
                    if pos and pos.is_stopped(df["low"].iloc[-1], df["high"].iloc[-1]):
                        trade = position_tracker.close_position(
                            symbol, pos.stop_price, datetime.now(timezone.utc)
                        )
                        if trade:
                            trade_logger.log_trade(trade, regime=signal.regime)
                            alerter.trade_alert(symbol, trade.side, trade.size, trade.exit_price, trade.pnl)

                    # Submit new orders
                    if signal.direction != "HOLD" and not position_tracker.has_position(symbol):
                        managed_order = order_manager.submit_order_for_symbol(
                            symbol, signal, current_price, equity, position_tracker.count()
                        )
                        if managed_order:
                            position_tracker.open_position(
                                symbol=managed_order.symbol,
                                side=managed_order.side,
                                size=managed_order.size,
                                entry_price=managed_order.entry_price,
                                stop_price=managed_order.stop_price,
                                entry_time=managed_order.timestamp,
                            )
                            alerter.trade_alert(symbol, managed_order.side, managed_order.size, managed_order.entry_price)

                except Exception as e:
                    logger.error(f"Error processing {symbol}: {e}")

            # Sleep until next candle check (poll every 55 seconds)
            time.sleep(55)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        data_feed.stop()
        health_check.stop()


def run_live(config: dict):
    """Live trading — requires explicit confirmation."""
    if config["exchange"].get("testnet", True):
        logger.warning("Live mode but testnet=true. Set testnet=false for real trading.")

    confirm = input("WARNING: You are about to start LIVE trading. Type 'YES' to confirm: ")
    if confirm != "YES":
        logger.info("Live trading cancelled.")
        sys.exit(0)

    # Live mode uses the same logic as paper mode
    run_paper(config)


def main():
    load_dotenv()
    config = load_config()
    setup_logging()

    parser = argparse.ArgumentParser(description="Algo Trader")
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "live"],
        default="backtest",
        help="Trading mode (default: backtest)",
    )
    args = parser.parse_args()

    logger.info(f"Algo Trader starting in {args.mode} mode")

    if args.mode == "backtest":
        run_backtest(config)
    elif args.mode == "paper":
        run_paper(config)
    elif args.mode == "live":
        run_live(config)


if __name__ == "__main__":
    main()
