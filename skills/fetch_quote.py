#!/usr/bin/env python3
"""Fetch current quote and company profile from Finnhub.

Usage: python skills/fetch_quote.py NVDA
Output: workspace/{DATE}/{TICKER}/raw_data.json (merges "quote" and "profile")
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
    parser = argparse.ArgumentParser(description="Fetch stock quote and profile")
    parser.add_argument("ticker", help="Stock ticker symbol")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    client = get_finnhub_client()

    try:
        quote = client.quote(ticker)
        rate_limit_sleep()
        profile = client.company_profile2(symbol=ticker)
    except Exception as e:
        error_exit(f"Finnhub API error for {ticker}: {e}")

    if not quote or quote.get("c", 0) == 0:
        error_exit(f"No quote data returned for {ticker}")

    merge_into_raw_data(quote, "quote", ticker)
    merge_into_raw_data(profile, "profile", ticker)

    result = {"quote": quote, "profile": profile}
    write_json(result, "quote.json", ticker)


if __name__ == "__main__":
    main()
