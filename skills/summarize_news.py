#!/usr/bin/env python3
"""Summarize news articles using rule-based analysis.

Usage: python skills/summarize_news.py NVDA
Reads: workspace/{DATE}/{TICKER}/raw_data.json (news array)
Writes: workspace/{DATE}/{TICKER}/news_summary.json

Rule-based (no LLM):
  - Count articles, extract top headlines
  - Detect event risk keywords
  - Compute naive keyword sentiment
"""

import argparse
import re
import sys
import os
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.utils import read_json, write_json, error_exit, timestamp_now

BULLISH_KEYWORDS = [
    "beat", "exceeds", "upgrade", "outperform", "buy", "strong",
    "growth", "record", "breakthrough", "partnership", "acquisition",
    "innovation", "expansion", "bullish", "momentum", "rally",
    "demand", "revenue growth", "profit", "positive", "upside",
    "ai", "artificial intelligence", "machine learning", "cloud",
]

BEARISH_KEYWORDS = [
    "miss", "downgrade", "underperform", "sell", "weak",
    "decline", "loss", "investigation", "sec", "doj", "antitrust",
    "lawsuit", "recall", "data breach", "layoff", "restructuring",
    "bearish", "crash", "bubble", "overvalued", "risk",
    "tariff", "regulation", "slowdown", "warning", "cut",
]

EVENT_RISK_KEYWORDS = [
    "earnings", "quarterly results", "guidance",
    "doj", "antitrust", "sec", "investigation",
    "downgrade", "upgrade",
    "ceo", "cfo", "leadership change",
    "data breach", "security incident",
    "recall", "product issue",
    "partnership", "acquisition", "merger",
    "fda", "regulatory",
    "dividend", "buyback", "split",
]


def count_keyword_hits(text: str, keywords: list) -> int:
    """Count keyword matches in text (case-insensitive)."""
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw in text_lower)


def main():
    parser = argparse.ArgumentParser(description="Summarize news")
    parser.add_argument("ticker", help="Stock ticker symbol")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    try:
        raw = read_json("raw_data.json", ticker)
    except FileNotFoundError:
        error_exit(f"raw_data.json not found for {ticker}. Run fetch skills first.")

    news = raw.get("news", [])
    if not news:
        result = {
            "ticker": ticker,
            "timestamp": timestamp_now(),
            "article_count": 0,
            "top_headlines": [],
            "sources": {},
            "sentiment": {"bullish_hits": 0, "bearish_hits": 0, "ratio": 0.5, "label": "neutral"},
            "event_risks": [],
            "date_distribution": {},
        }
        write_json(result, "news_summary.json", ticker)
        return

    # Extract headlines and sources
    headlines = []
    sources = Counter()
    date_dist = Counter()
    all_text = ""

    for article in news:
        headline = article.get("headline", "")
        summary = article.get("summary", "")
        source = article.get("source", "unknown")
        dt = article.get("datetime")

        headlines.append({
            "headline": headline,
            "source": source,
            "datetime": dt,
        })
        sources[source] += 1
        all_text += f" {headline} {summary}"

        if dt:
            try:
                day = datetime.fromtimestamp(dt).strftime("%Y-%m-%d")
                date_dist[day] += 1
            except (ValueError, OSError):
                pass

    # Top 5 headlines (most recent)
    top_headlines = headlines[:5]

    # Keyword sentiment
    bullish_hits = count_keyword_hits(all_text, BULLISH_KEYWORDS)
    bearish_hits = count_keyword_hits(all_text, BEARISH_KEYWORDS)
    total_hits = bullish_hits + bearish_hits
    sentiment_ratio = bullish_hits / total_hits if total_hits > 0 else 0.5

    if sentiment_ratio > 0.65:
        sentiment_label = "bullish"
    elif sentiment_ratio > 0.55:
        sentiment_label = "slightly bullish"
    elif sentiment_ratio > 0.45:
        sentiment_label = "neutral"
    elif sentiment_ratio > 0.35:
        sentiment_label = "slightly bearish"
    else:
        sentiment_label = "bearish"

    # Detect event risks
    event_risks = []
    for kw in EVENT_RISK_KEYWORDS:
        if kw in all_text.lower():
            # Find the headline mentioning it
            for h in headlines:
                if kw in h["headline"].lower():
                    event_risks.append({"keyword": kw, "headline": h["headline"]})
                    break
            else:
                event_risks.append({"keyword": kw, "headline": None})

    # Deduplicate event risks by keyword
    seen = set()
    unique_risks = []
    for risk in event_risks:
        if risk["keyword"] not in seen:
            seen.add(risk["keyword"])
            unique_risks.append(risk)

    result = {
        "ticker": ticker,
        "timestamp": timestamp_now(),
        "article_count": len(news),
        "top_headlines": top_headlines,
        "sources": dict(sources.most_common(10)),
        "sentiment": {
            "bullish_hits": bullish_hits,
            "bearish_hits": bearish_hits,
            "ratio": round(sentiment_ratio, 3),
            "label": sentiment_label,
        },
        "event_risks": unique_risks,
        "date_distribution": dict(sorted(date_dist.items())),
    }

    write_json(result, "news_summary.json", ticker)


if __name__ == "__main__":
    main()
