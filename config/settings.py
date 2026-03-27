"""
Configuration for the IBKR Investment Dashboard.
All secrets are loaded from environment variables.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class IBKRConfig:
    host: str = "127.0.0.1"
    port: int = 7497  # 7497 for TWS, 4001 for IB Gateway
    client_id: int = 1
    account_id: str = os.getenv("IBKR_ACCOUNT_ID", "")
    readonly: bool = True  # Safety: read-only by default


@dataclass
class SentimentConfig:
    # Reddit (PRAW)
    reddit_client_id: str = os.getenv("REDDIT_CLIENT_ID", "")
    reddit_client_secret: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    reddit_user_agent: str = "IBKR-Dashboard/1.0"
    subreddits: list = field(
        default_factory=lambda: ["wallstreetbets", "investing", "stocks"]
    )

    # Finnhub
    finnhub_api_key: str = os.getenv("FINNHUB_API_KEY", "")

    # Fear & Greed (no key needed)
    fear_greed_enabled: bool = True

    # Refresh intervals (seconds)
    reddit_refresh: int = 900  # 15 min
    news_refresh: int = 600  # 10 min
    fear_greed_refresh: int = 3600  # 1 hour


@dataclass
class TaxConfig:
    """Belgian tax rates as of 2026."""
    country: str = "BE"

    # TOB (Tax on Stock Exchange Transactions)
    tob_stocks: float = 0.0035  # 0.35%
    tob_etf_low: float = 0.0012  # 0.12%
    tob_etf_high: float = 0.0132  # 1.32%

    # Capital Gains Tax (new Jan 2026)
    cgt_rate: float = 0.10  # 10%
    cgt_annual_exemption: float = 10_000.0  # EUR
    cgt_baseline_date: str = "2025-12-31"
    cgt_transition_end: str = "2030-12-31"

    # Dividend Withholding
    dividend_tax_rate: float = 0.30  # 30%

    # Reynders Tax (ETFs with >10% bonds)
    reynders_tax_rate: float = 0.30  # 30%


@dataclass
class DashboardConfig:
    title: str = "IBKR Investment Dashboard"
    refresh_interval: int = 5  # seconds for portfolio refresh
    port: int = 8501


@dataclass
class SignalConfig:
    """DCA / Buy signal thresholds."""
    # Fear & Greed thresholds
    extreme_fear: int = 20  # Strong buy signal
    fear: int = 40  # Moderate buy signal
    greed: int = 60  # Hold / trim signal
    extreme_greed: int = 80  # Strong trim signal

    # VIX thresholds
    vix_high: float = 30.0  # Elevated fear
    vix_extreme: float = 40.0  # Panic — strong buy

    # RSI thresholds (per stock)
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0

    # Drawdown thresholds (from 52-week high)
    drawdown_moderate: float = -0.10  # -10%
    drawdown_deep: float = -0.20  # -20%
    drawdown_extreme: float = -0.30  # -30%

    # DCA sizing (% of available cash per signal strength)
    dca_light: float = 0.10  # 10% of cash
    dca_moderate: float = 0.20  # 20% of cash
    dca_heavy: float = 0.35  # 35% of cash


@dataclass
class AppConfig:
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    tax: TaxConfig = field(default_factory=TaxConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    signals: SignalConfig = field(default_factory=SignalConfig)


config = AppConfig()
