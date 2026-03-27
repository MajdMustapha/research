"""
Sentiment Aggregator — combines multiple sentiment sources into a unified score.
Sources: Fear & Greed Index, VIX, Reddit, Finnhub News, Put/Call Ratio.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)


class SentimentLevel(Enum):
    EXTREME_FEAR = "Extreme Fear"
    FEAR = "Fear"
    NEUTRAL = "Neutral"
    GREED = "Greed"
    EXTREME_GREED = "Extreme Greed"


@dataclass
class SentimentSource:
    name: str
    score: float  # Normalized 0-100 (0=extreme fear, 100=extreme greed)
    raw_value: float
    label: str
    timestamp: datetime
    weight: float = 1.0


@dataclass
class AggregatedSentiment:
    timestamp: datetime
    composite_score: float  # 0-100
    level: SentimentLevel
    sources: list[SentimentSource] = field(default_factory=list)
    signal: str = ""  # "STRONG_BUY", "BUY", "HOLD", "TRIM", "STRONG_SELL"


class FearGreedFetcher:
    """CNN Fear & Greed Index via the fear-greed package or direct API."""

    def fetch(self) -> Optional[SentimentSource]:
        try:
            import fear_greed
            data = fear_greed.get()
            score = data.value
            label = data.description
            return SentimentSource(
                name="CNN Fear & Greed",
                score=score,
                raw_value=score,
                label=label,
                timestamp=datetime.now(),
                weight=2.0,  # Higher weight — proven market indicator
            )
        except ImportError:
            return self._fetch_direct()
        except Exception as e:
            logger.warning(f"fear-greed package failed: {e}, trying direct API")
            return self._fetch_direct()

    def _fetch_direct(self) -> Optional[SentimentSource]:
        try:
            url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            score = data["fear_and_greed"]["score"]
            label = data["fear_and_greed"]["rating"]
            return SentimentSource(
                name="CNN Fear & Greed",
                score=score,
                raw_value=score,
                label=label,
                timestamp=datetime.now(),
                weight=2.0,
            )
        except Exception as e:
            logger.error(f"Failed to fetch Fear & Greed Index: {e}")
            return None


class VIXFetcher:
    """VIX (CBOE Volatility Index) via yfinance."""

    def fetch(self) -> Optional[SentimentSource]:
        try:
            vix = yf.Ticker("^VIX")
            hist = vix.history(period="5d")
            if hist.empty:
                return None

            current_vix = hist["Close"].iloc[-1]
            # Normalize: VIX 10 → score 90 (greed), VIX 40+ → score 10 (fear)
            score = max(0, min(100, 100 - (current_vix - 10) * (90 / 30)))

            if current_vix >= 40:
                label = "Extreme Fear (VIX ≥ 40)"
            elif current_vix >= 30:
                label = "Fear (VIX ≥ 30)"
            elif current_vix >= 20:
                label = "Neutral (VIX 20-30)"
            elif current_vix >= 15:
                label = "Greed (VIX 15-20)"
            else:
                label = "Extreme Greed (VIX < 15)"

            return SentimentSource(
                name="VIX",
                score=score,
                raw_value=current_vix,
                label=label,
                timestamp=datetime.now(),
                weight=2.0,
            )
        except Exception as e:
            logger.error(f"Failed to fetch VIX: {e}")
            return None


class RedditSentimentFetcher:
    """
    Reddit sentiment via PRAW + VADER.
    Monitors r/wallstreetbets, r/investing, r/stocks for ticker mentions.
    """

    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent

    def fetch(self, tickers: list[str], subreddits: list[str] = None) -> Optional[SentimentSource]:
        if not self.client_id or not self.client_secret:
            logger.info("Reddit credentials not configured, skipping")
            return None

        subreddits = subreddits or ["wallstreetbets", "investing", "stocks"]

        try:
            import praw
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

            reddit = praw.Reddit(
                client_id=self.client_id,
                client_secret=self.client_secret,
                user_agent=self.user_agent,
            )
            analyzer = SentimentIntensityAnalyzer()

            scores = []
            for sub_name in subreddits:
                subreddit = reddit.subreddit(sub_name)
                for post in subreddit.hot(limit=50):
                    text = f"{post.title} {post.selftext}"
                    # Check if any of our tickers are mentioned
                    mentioned = any(
                        t.upper() in text.upper() for t in tickers
                    )
                    if mentioned:
                        vs = analyzer.polarity_scores(text)
                        scores.append(vs["compound"])

            if not scores:
                return SentimentSource(
                    name="Reddit",
                    score=50.0,
                    raw_value=0.0,
                    label="No mentions found",
                    timestamp=datetime.now(),
                    weight=1.0,
                )

            avg_compound = sum(scores) / len(scores)
            # Normalize: compound -1..+1 → score 0..100
            normalized = (avg_compound + 1) * 50

            if normalized >= 70:
                label = f"Bullish ({len(scores)} mentions)"
            elif normalized <= 30:
                label = f"Bearish ({len(scores)} mentions)"
            else:
                label = f"Mixed ({len(scores)} mentions)"

            return SentimentSource(
                name="Reddit",
                score=normalized,
                raw_value=avg_compound,
                label=label,
                timestamp=datetime.now(),
                weight=1.0,
            )
        except ImportError:
            logger.warning("praw or vaderSentiment not installed")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch Reddit sentiment: {e}")
            return None


class NewsSentimentFetcher:
    """Financial news sentiment via Finnhub."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch(self, tickers: list[str]) -> Optional[SentimentSource]:
        if not self.api_key:
            logger.info("Finnhub API key not configured, skipping")
            return None

        try:
            import finnhub
            client = finnhub.Client(api_key=self.api_key)

            all_scores = []
            for ticker in tickers[:5]:  # Limit to avoid rate limits
                sentiment = client.news_sentiment(ticker)
                if sentiment and "sentiment" in sentiment:
                    s = sentiment["sentiment"]
                    # Finnhub: bearishPercent / bullishPercent
                    bullish = s.get("bullishPercent", 0.5)
                    all_scores.append(bullish * 100)

            if not all_scores:
                return None

            avg_score = sum(all_scores) / len(all_scores)

            if avg_score >= 65:
                label = "Bullish News"
            elif avg_score <= 35:
                label = "Bearish News"
            else:
                label = "Neutral News"

            return SentimentSource(
                name="News (Finnhub)",
                score=avg_score,
                raw_value=avg_score,
                label=label,
                timestamp=datetime.now(),
                weight=1.5,
            )
        except ImportError:
            logger.warning("finnhub-python not installed")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch news sentiment: {e}")
            return None


class PutCallRatioFetcher:
    """CBOE Put/Call Ratio — a contrarian indicator."""

    def fetch(self) -> Optional[SentimentSource]:
        try:
            # Equity put/call ratio from CBOE (via yfinance proxy)
            # High ratio (>1.0) = fear/bearish = contrarian buy
            # Low ratio (<0.7) = greed/bullish = contrarian sell
            url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/PCR.csv"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return None

            lines = resp.text.strip().split("\n")
            if len(lines) < 2:
                return None

            last_line = lines[-1]
            parts = last_line.split(",")
            pcr = float(parts[-1])

            # Normalize: PCR 0.5 → 80 (greed), PCR 1.2 → 15 (fear)
            score = max(0, min(100, 100 - (pcr - 0.5) * (85 / 0.7)))

            if pcr >= 1.0:
                label = f"High Put/Call ({pcr:.2f}) — Fear"
            elif pcr >= 0.7:
                label = f"Neutral Put/Call ({pcr:.2f})"
            else:
                label = f"Low Put/Call ({pcr:.2f}) — Greed"

            return SentimentSource(
                name="Put/Call Ratio",
                score=score,
                raw_value=pcr,
                label=label,
                timestamp=datetime.now(),
                weight=1.5,
            )
        except Exception as e:
            logger.error(f"Failed to fetch Put/Call ratio: {e}")
            return None


class SentimentAggregator:
    """
    Combines all sentiment sources into a single weighted composite score.
    Score: 0 = Extreme Fear, 100 = Extreme Greed.
    """

    def __init__(self, config=None):
        from config.settings import config as app_config
        self.config = config or app_config.sentiment

        self.fear_greed = FearGreedFetcher()
        self.vix = VIXFetcher()
        self.reddit = RedditSentimentFetcher(
            self.config.reddit_client_id,
            self.config.reddit_client_secret,
            self.config.reddit_user_agent,
        )
        self.news = NewsSentimentFetcher(self.config.finnhub_api_key)
        self.put_call = PutCallRatioFetcher()

    def fetch_all(self, tickers: list[str] = None) -> AggregatedSentiment:
        tickers = tickers or []
        sources = []

        # Fetch from all sources (order: fastest first)
        for fetcher, args in [
            (self.fear_greed.fetch, []),
            (self.vix.fetch, []),
            (self.put_call.fetch, []),
            (self.reddit.fetch, [tickers]),
            (self.news.fetch, [tickers]),
        ]:
            try:
                result = fetcher(*args) if args else fetcher()
                if result:
                    sources.append(result)
            except Exception as e:
                logger.warning(f"Sentiment source failed: {e}")

        # Weighted average
        if sources:
            total_weight = sum(s.weight for s in sources)
            composite = sum(s.score * s.weight for s in sources) / total_weight
        else:
            composite = 50.0  # Default neutral

        # Determine level
        if composite <= 20:
            level = SentimentLevel.EXTREME_FEAR
            signal = "STRONG_BUY"
        elif composite <= 40:
            level = SentimentLevel.FEAR
            signal = "BUY"
        elif composite <= 60:
            level = SentimentLevel.NEUTRAL
            signal = "HOLD"
        elif composite <= 80:
            level = SentimentLevel.GREED
            signal = "TRIM"
        else:
            level = SentimentLevel.EXTREME_GREED
            signal = "STRONG_SELL"

        return AggregatedSentiment(
            timestamp=datetime.now(),
            composite_score=round(composite, 1),
            level=level,
            sources=sources,
            signal=signal,
        )
