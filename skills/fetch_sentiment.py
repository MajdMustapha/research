#!/usr/bin/env python3
"""Fetch news sentiment data from Finnhub.

Usage: python skills/fetch_sentiment.py NVDA
Output: workspace/{DATE}/{TICKER}/sentiment.json
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
    parser = argparse.ArgumentParser(description="Fetch news sentiment")
    parser.add_argument("ticker", help="Stock ticker symbol")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    client = get_finnhub_client()

    try:
        sentiment = client.news_sentiment(ticker)
    except Exception as e:
        error_exit(f"Finnhub API error for {ticker}: {e}")

    if not sentiment:
        error_exit(f"No sentiment data for {ticker}")

    merge_into_raw_data(sentiment, "sentiment", ticker)
    write_json(sentiment, "sentiment.json", ticker)


if __name__ == "__main__":
    main()
