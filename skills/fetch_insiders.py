#!/usr/bin/env python3
"""Fetch insider transactions from Finnhub.

Usage: python skills/fetch_insiders.py NVDA
Output: workspace/{DATE}/{TICKER}/insiders.json
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
    parser = argparse.ArgumentParser(description="Fetch insider transactions")
    parser.add_argument("ticker", help="Stock ticker symbol")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    client = get_finnhub_client()

    try:
        data = client.stock_insider_transactions(ticker, count=50)
    except Exception as e:
        error_exit(f"Finnhub API error for {ticker}: {e}")

    merge_into_raw_data(data, "insider_transactions", ticker)
    write_json(data, "insiders.json", ticker)


if __name__ == "__main__":
    main()
