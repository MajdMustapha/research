#!/usr/bin/env python3
"""Fetch financial data from multiple Finnhub endpoints.

Usage: python skills/fetch_financials.py NVDA
Fetches: basic financials, earnings, price targets, recommendations, insider transactions
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.utils import (
    get_finnhub_client,
    merge_into_raw_data,
    rate_limit_sleep,
    write_json,
    error_exit,
)


def main():
    parser = argparse.ArgumentParser(description="Fetch financial data")
    parser.add_argument("ticker", help="Stock ticker symbol")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    client = get_finnhub_client()
    result = {}

    calls = [
        ("financials", lambda: client.company_basic_financials(ticker, "all")),
        ("earnings", lambda: client.company_earnings(ticker, limit=4)),
        ("price_target", lambda: client.price_target(ticker)),
        ("recommendations", lambda: client.recommendation_trends(ticker)),
        ("insider_transactions", lambda: client.stock_insider_transactions(ticker, count=50)),
    ]

    for key, fetch_fn in calls:
        try:
            data = fetch_fn()
            result[key] = data
            merge_into_raw_data(data, key, ticker)
        except Exception as e:
            print(f"WARNING: Failed to fetch {key} for {ticker}: {e}", file=sys.stderr)
            result[key] = {"error": str(e)}
        rate_limit_sleep()

    write_json(result, "financials.json", ticker)


if __name__ == "__main__":
    main()
