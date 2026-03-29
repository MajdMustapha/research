#!/usr/bin/env python3
"""Fetch OHLCV candle data from Finnhub.

Usage: python skills/fetch_candles.py NVDA --days 90
Output: workspace/{DATE}/{TICKER}/candles.json
"""

import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.utils import (
    get_finnhub_client,
    merge_into_raw_data,
    write_json,
    error_exit,
)


def main():
    parser = argparse.ArgumentParser(description="Fetch stock candles")
    parser.add_argument("ticker", help="Stock ticker symbol")
    parser.add_argument("--days", type=int, default=90, help="Number of days (default: 90)")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    client = get_finnhub_client()
    to_ts = int(time.time())
    from_ts = to_ts - (args.days * 86400)

    try:
        candles = client.stock_candles(ticker, "D", from_ts, to_ts)
    except Exception as e:
        error_exit(f"Finnhub API error for {ticker}: {e}")

    if not candles or candles.get("s") != "ok":
        error_exit(f"No candle data for {ticker}: {candles.get('s', 'unknown')}")

    merge_into_raw_data(candles, "candles", ticker)
    write_json(candles, "candles.json", ticker)


if __name__ == "__main__":
    main()
