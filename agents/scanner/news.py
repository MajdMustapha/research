"""
RSS + NewsAPI feed parser.
Polls all feeds every 60 seconds, deduplicates, and forwards to analyst.
"""

import os
import time
from typing import Optional

import yaml

from lib.state import (
    is_article_seen,
    mark_article_seen,
    cleanup_old_articles,
    log_event,
)
from lib.logger import get_logger

logger = get_logger(__name__)

# ─── RSS Feeds (no API key required) ─────────────────────────────────────────

RSS_FEEDS = [
    # General news
    "https://feeds.reuters.com/reuters/topNews",
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://feeds.washingtonpost.com/rss/world",
    # Crypto
    "https://coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    # Politics / policy
    "https://www.politico.com/rss/politics08.xml",
    "https://thehill.com/feed/",
    # Finance / macro
    "https://feeds.ft.com/rss/markets",
    "https://www.wsj.com/xml/rss/3_7031.xml",
    # Geopolitics (Section 23.9)
    "https://feeds.reuters.com/Reuters/worldNews",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://www.theguardian.com/world/rss",
    "https://foreignpolicy.com/feed/",
]

# Rate limit: max articles forwarded to analyst per hour
_articles_forwarded_this_hour: list[float] = []


def _load_settings() -> dict:
    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        "settings.yaml",
    )
    with open(settings_path) as f:
        return yaml.safe_load(f)


def _load_tracked_markets() -> list:
    markets_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        "markets.yaml",
    )
    with open(markets_path) as f:
        config = yaml.safe_load(f)
    return config.get("tracked_markets") or []


def _can_forward_to_analyst() -> bool:
    """Check if we're under the hourly rate limit for analyst calls."""
    global _articles_forwarded_this_hour
    settings = _load_settings()
    max_per_hour = settings.get("scanner", {}).get("max_analyst_calls_per_hour", 20)

    now = time.time()
    _articles_forwarded_this_hour = [
        t for t in _articles_forwarded_this_hour if now - t < 3600
    ]
    return len(_articles_forwarded_this_hour) < max_per_hour


def _record_forwarded():
    """Record that an article was forwarded to analyst."""
    _articles_forwarded_this_hour.append(time.time())


def poll_rss_feeds() -> list[dict]:
    """
    Poll all RSS feeds and return new, unseen articles.
    Each article: {guid, title, description, link, feed_url}
    """
    try:
        import feedparser
    except ImportError:
        logger.error("feedparser not installed")
        return []

    new_articles = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:10]:  # limit per feed
                guid = entry.get("id") or entry.get("link") or entry.get("title", "")
                if not guid or is_article_seen(guid):
                    continue

                title = entry.get("title", "")
                description = (entry.get("summary") or entry.get("description") or "")[:500]

                mark_article_seen(guid, title)
                new_articles.append({
                    "guid": guid,
                    "title": title,
                    "description": description,
                    "link": entry.get("link", ""),
                    "feed_url": feed_url,
                })
        except Exception as e:
            logger.warning(f"Failed to parse feed {feed_url}: {e}")

    if new_articles:
        logger.info(f"Found {len(new_articles)} new articles across {len(RSS_FEEDS)} feeds")

    return new_articles


def check_articles_for_markets(articles: list[dict]) -> list[dict]:
    """
    Check if any new articles are relevant to tracked markets.
    Uses Haiku for cheap screening, then returns relevant (article, market) pairs.
    """
    tracked_markets = _load_tracked_markets()
    if not tracked_markets or not articles:
        return []

    relevant = []

    for article in articles:
        if not _can_forward_to_analyst():
            logger.warning("Analyst rate limit reached — skipping remaining articles")
            break

        # First pass: keyword matching
        title_lower = article["title"].lower()
        desc_lower = article["description"].lower()

        keyword_matches = []
        for market in tracked_markets:
            keywords = market.get("keywords", [])
            question_words = market.get("question", "").lower().split()[:5]
            all_keywords = keywords + question_words

            if any(kw.lower() in title_lower or kw.lower() in desc_lower for kw in all_keywords if kw):
                keyword_matches.append(market)

        if not keyword_matches:
            continue

        # Second pass: Haiku screening for confirmed matches
        try:
            from agents.analyst.claude_client import screen_news_relevance

            affected_ids = screen_news_relevance(article["title"], keyword_matches)
            for market in keyword_matches:
                if market.get("id") in affected_ids:
                    _record_forwarded()
                    relevant.append({
                        "article": article,
                        "market": market,
                        "source": "news",
                    })
                    logger.info(
                        f"News match: '{article['title'][:60]}' -> {market.get('question', '')[:40]}"
                    )
        except Exception as e:
            logger.warning(f"Haiku screening failed: {e}")
            # Fall back to keyword match only
            for market in keyword_matches:
                _record_forwarded()
                relevant.append({
                    "article": article,
                    "market": market,
                    "source": "news",
                })

    return relevant


def poll_newsapi(query: str, page_size: int = 10) -> list[dict]:
    """
    Optional NewsAPI integration (paid, $50/mo).
    Only use for domain-specific searches when RSS proves insufficient.
    """
    import requests

    api_key = os.getenv("NEWSAPI_KEY")
    if not api_key:
        return []

    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "pageSize": page_size,
                "sortBy": "publishedAt",
                "apiKey": api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for a in data.get("articles", []):
            guid = a.get("url", "")
            if guid and not is_article_seen(guid):
                mark_article_seen(guid, a.get("title"))
                articles.append({
                    "guid": guid,
                    "title": a.get("title", ""),
                    "description": (a.get("description") or "")[:500],
                    "link": guid,
                    "feed_url": "newsapi",
                })
        return articles
    except Exception as e:
        logger.warning(f"NewsAPI request failed: {e}")
        return []
