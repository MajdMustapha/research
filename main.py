#!/usr/bin/env python3
"""
Polymarket Weather Arbitrage Bot — entry point.

Usage:
    python main.py backtest --days 180 --split-date 30
    python main.py live
    python main.py crowd-rank --days 90
"""
from __future__ import annotations

import argparse
import sys

from config import Config
from connectors.cli import setup_logging


def cmd_backtest(args: argparse.Namespace, config: Config) -> None:
    from backtest.simulator import run_split_backtest, run_full_backtest
    from backtest.reporter import generate_backtest_report

    cities = [c.strip() for c in args.cities.split(",")] if args.cities else None

    if args.coverage_check:
        from backtest.data_collector import coverage_check
        coverage_check(config, cities=cities, days=args.days)
        return

    if args.split_date and args.split_date > 0:
        results = run_split_backtest(
            config, days=args.days, holdout_days=args.split_date,
            cities=cities, horizon=args.horizon,
        )
        print(f"\n{'='*60}")
        print(f"  Train P&L : ${results['train']['net_pnl']:+.2f}  "
              f"(win rate {results['train']['win_rate']:.0%})")
        print(f"  Test  P&L : ${results['test']['net_pnl']:+.2f}  "
              f"(win rate {results['test']['win_rate']:.0%})")
        print(f"  Gate passed: {results['gate_passed']}")
        print(f"{'='*60}\n")
    else:
        results = run_full_backtest(
            config, days=args.days, cities=cities, horizon=args.horizon,
        )

    generate_backtest_report(results, output_path="data/backtest_report.html")
    print("Report written to data/backtest_report.html")


def cmd_live(args: argparse.Namespace, config: Config) -> None:
    from agent import step
    step(config)


def cmd_crowd_rank(args: argparse.Namespace, config: Config) -> None:
    from analysis.crowding_detector import CrowdingDetector
    detector = CrowdingDetector(db_path="data/markets.db")
    reports = detector.run(days=args.days)
    print(f"\nCity rankings ({len(reports)} cities analysed):")
    for r in reports:
        print(f"  {r.city:20s}  score={r.opportunity_score:5.1f}  "
              f"window={r.entry_window_minutes}min  trend={r.trend}")
    print("\nOutputs:")
    print("  data/crowding.db")
    print("  data/crowding_report.html")
    print("  data/config_city_overrides.json")


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Polymarket Weather Arbitrage Bot",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backtest
    bt = sub.add_parser("backtest", help="Run historical backtest")
    bt.add_argument("--days", type=int, default=180)
    bt.add_argument("--split-date", type=int, default=30,
                    help="Holdout days for out-of-sample validation (0=disable)")
    bt.add_argument("--cities", type=str, default=None,
                    help='Comma-separated city names (default: all)')
    bt.add_argument("--horizon", type=int, default=24, choices=[24, 48],
                    help="Entry horizon in hours (24 or 48)")
    bt.add_argument("--coverage-check", action="store_true",
                    help="Print IEM station coverage stats and exit")

    # live
    lv = sub.add_parser("live", help="Run one live agent cycle")

    # crowd-rank
    cr = sub.add_parser("crowd-rank", help="Run city crowding analysis")
    cr.add_argument("--days", type=int, default=90)

    args = parser.parse_args()
    config = Config()

    if args.command == "backtest":
        cmd_backtest(args, config)
    elif args.command == "live":
        cmd_live(args, config)
    elif args.command == "crowd-rank":
        cmd_crowd_rank(args, config)


if __name__ == "__main__":
    main()
