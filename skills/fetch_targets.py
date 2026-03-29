#!/usr/bin/env python3
"""Fetch analyst price targets from Finnhub.

Usage: python skills/fetch_targets.py NVDA
Output: workspace/{DATE}/{TICKER}/targets.json
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.utils import (
    get_finnhub_client,
    merge_into_raw_data,
    write_json,
    error_exit,
)


def main():
    parser = argparse.ArgumentParser(description="Fetch analyst price targets")
    parser.add_argument("ticker", help="Stock ticker symbol")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    client = get_finnhub_client()

    try:
        targets = client.price_target(ticker)
    except Exception as e:
        error_exit(f"Finnhub API error for {ticker}: {e}")

    merge_into_raw_data(targets, "price_target", ticker)
    write_json(targets, "targets.json", ticker)


if __name__ == "__main__":
    main()
