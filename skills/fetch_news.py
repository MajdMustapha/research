#!/usr/bin/env python3
"""Fetch company news from Finnhub.

Usage: python skills/fetch_news.py NVDA --days 7
Output: workspace/{DATE}/{TICKER}/news.json
"""

import argparse
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.utils import (
    get_finnhub_client,
    merge_into_raw_data,
    write_json,
    error_exit,
)


def main():
    parser = argparse.ArgumentParser(description="Fetch company news")
    parser.add_argument("ticker", help="Stock ticker symbol")
    parser.add_argument("--days", type=int, default=7, help="Days of news (default: 7)")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    client = get_finnhub_client()
    to_date = date.today().isoformat()
    from_date = (date.today() - timedelta(days=args.days)).isoformat()

    try:
        news = client.company_news(ticker, _from=from_date, to=to_date)
    except Exception as e:
        error_exit(f"Finnhub API error for {ticker}: {e}")

    if news is None:
        news = []

    # Limit to 50 most recent articles
    news = news[:50]

    merge_into_raw_data(news, "news", ticker)
    write_json({"ticker": ticker, "count": len(news), "articles": news}, "news.json", ticker)


if __name__ == "__main__":
    main()
