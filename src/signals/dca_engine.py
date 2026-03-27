"""
DCA Signal Engine — combines sentiment, technicals, and portfolio state
to generate actionable buy/DCA/hold/trim signals.

This is NOT financial advice. All signals are for informational purposes.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import yfinance as yf
import pandas as pd

from src.ibkr.client import PortfolioSnapshot, Position
from src.sentiment.aggregator import AggregatedSentiment, SentimentLevel

logger = logging.getLogger(__name__)


class SignalAction(Enum):
    STRONG_BUY = "STRONG BUY"
    BUY = "BUY"
    DCA = "DCA"
    HOLD = "HOLD"
    TRIM = "TRIM"
    SELL = "SELL"


@dataclass
class TechnicalIndicators:
    ticker: str
    rsi_14: float = 50.0
    sma_50: float = 0.0
    sma_200: float = 0.0
    price_vs_52w_high: float = 0.0  # Drawdown from 52-week high (negative %)
    price_vs_sma_200: float = 0.0  # % above/below 200 SMA
    volume_ratio: float = 1.0  # Current vol / avg vol


@dataclass
class PositionSignal:
    ticker: str
    name: str
    action: SignalAction
    confidence: float  # 0-100
    reasons: list[str] = field(default_factory=list)
    current_weight: float = 0.0
    target_weight: float = 0.0
    suggested_amount_eur: float = 0.0
    technicals: TechnicalIndicators = None


@dataclass
class PortfolioSignals:
    timestamp: datetime
    overall_action: SignalAction
    sentiment: AggregatedSentiment
    position_signals: list[PositionSignal] = field(default_factory=list)
    new_ideas: list[str] = field(default_factory=list)
    dca_budget_eur: float = 0.0
    summary: str = ""


def compute_rsi(closes: pd.Series, period: int = 14) -> float:
    """Calculate RSI from a price series."""
    delta = closes.diff()
    gains = delta.where(delta > 0, 0)
    losses = -delta.where(delta < 0, 0)
    avg_gain = gains.rolling(window=period).mean()
    avg_loss = losses.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not rsi.empty else 50.0


def get_technicals(ticker: str) -> TechnicalIndicators:
    """Fetch and compute technical indicators for a single ticker."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty or len(hist) < 50:
            return TechnicalIndicators(ticker=ticker)

        closes = hist["Close"]
        volumes = hist["Volume"]

        rsi = compute_rsi(closes)
        sma_50 = closes.rolling(50).mean().iloc[-1]
        sma_200 = closes.rolling(200).mean().iloc[-1] if len(closes) >= 200 else closes.mean()
        high_52w = closes.max()
        current = closes.iloc[-1]
        avg_volume = volumes.rolling(20).mean().iloc[-1]
        current_volume = volumes.iloc[-1]

        return TechnicalIndicators(
            ticker=ticker,
            rsi_14=round(rsi, 1),
            sma_50=round(sma_50, 2),
            sma_200=round(sma_200, 2),
            price_vs_52w_high=round((current - high_52w) / high_52w * 100, 1),
            price_vs_sma_200=round((current - sma_200) / sma_200 * 100, 1),
            volume_ratio=round(current_volume / avg_volume, 2) if avg_volume > 0 else 1.0,
        )
    except Exception as e:
        logger.error(f"Failed to get technicals for {ticker}: {e}")
        return TechnicalIndicators(ticker=ticker)


class DCAEngine:
    """
    Multi-factor signal generator combining:
    1. Market sentiment (Fear & Greed, VIX, Reddit, News)
    2. Technical indicators (RSI, SMA, drawdown from 52w high)
    3. Portfolio state (concentration, cash availability)
    4. Belgian tax considerations (TOB impact on trade frequency)
    """

    # Target allocation for a diversified portfolio
    TARGET_WEIGHTS = {
        "MSFT": 0.12,
        "GOOGL": 0.12,
        "AMZN": 0.12,
        "META": 0.10,
        "NVDA": 0.10,
        "CRWD": 0.08,
        "CRM": 0.06,
        # Room for diversification
        "_DIVERSIFY": 0.30,  # Healthcare, Financials, etc.
    }

    MAX_POSITION_WEIGHT = 0.15  # No single stock > 15%

    def __init__(self, config=None):
        from config.settings import config as app_config
        self.config = config or app_config.signals

    def generate_signals(
        self,
        portfolio: PortfolioSnapshot,
        sentiment: AggregatedSentiment,
        monthly_dca_budget: float = 500.0,
    ) -> PortfolioSignals:
        """Generate signals for the entire portfolio."""

        # Total portfolio value for weight calculations
        total_value = portfolio.net_liquidation
        if total_value <= 0:
            total_value = sum(p.market_value for p in portfolio.positions)

        # Fetch technicals for all positions
        tickers = [p.ticker for p in portfolio.positions]
        technicals_map = {}
        for ticker in tickers:
            technicals_map[ticker] = get_technicals(ticker)

        # Generate per-position signals
        position_signals = []
        for pos in portfolio.positions:
            signal = self._analyze_position(
                pos, total_value, sentiment, technicals_map.get(pos.ticker)
            )
            position_signals.append(signal)

        # Sort by confidence (strongest signals first)
        position_signals.sort(key=lambda s: s.confidence, reverse=True)

        # Overall portfolio action
        overall = self._overall_action(sentiment, position_signals)

        # DCA allocation
        dca_budget = self._allocate_dca(
            monthly_dca_budget, sentiment, position_signals
        )

        # Generate new diversification ideas
        new_ideas = self._diversification_ideas(portfolio)

        summary = self._build_summary(
            overall, sentiment, position_signals, dca_budget
        )

        return PortfolioSignals(
            timestamp=datetime.now(),
            overall_action=overall,
            sentiment=sentiment,
            position_signals=position_signals,
            new_ideas=new_ideas,
            dca_budget_eur=dca_budget,
            summary=summary,
        )

    def _analyze_position(
        self,
        pos: Position,
        total_value: float,
        sentiment: AggregatedSentiment,
        tech: TechnicalIndicators,
    ) -> PositionSignal:
        """Generate signal for a single position."""
        reasons = []
        score = 50  # Start neutral

        current_weight = pos.market_value / total_value if total_value > 0 else 0
        target_weight = self.TARGET_WEIGHTS.get(pos.ticker, 0.05)

        # 1. Sentiment factor
        if sentiment.composite_score <= 20:
            score += 20
            reasons.append(f"Extreme fear ({sentiment.composite_score:.0f}/100) — contrarian buy")
        elif sentiment.composite_score <= 40:
            score += 10
            reasons.append(f"Fear zone ({sentiment.composite_score:.0f}/100) — favorable for buying")
        elif sentiment.composite_score >= 80:
            score -= 15
            reasons.append(f"Extreme greed ({sentiment.composite_score:.0f}/100) — caution")
        elif sentiment.composite_score >= 60:
            score -= 5
            reasons.append(f"Greed zone ({sentiment.composite_score:.0f}/100) — hold/trim bias")

        # 2. RSI factor
        if tech and tech.rsi_14 > 0:
            if tech.rsi_14 <= 30:
                score += 20
                reasons.append(f"RSI oversold ({tech.rsi_14:.0f})")
            elif tech.rsi_14 <= 40:
                score += 10
                reasons.append(f"RSI near oversold ({tech.rsi_14:.0f})")
            elif tech.rsi_14 >= 70:
                score -= 15
                reasons.append(f"RSI overbought ({tech.rsi_14:.0f})")
            elif tech.rsi_14 >= 60:
                score -= 5
                reasons.append(f"RSI elevated ({tech.rsi_14:.0f})")

        # 3. Drawdown from 52-week high
        if tech and tech.price_vs_52w_high < 0:
            dd = tech.price_vs_52w_high
            if dd <= -30:
                score += 25
                reasons.append(f"Deep drawdown from 52w high ({dd:.0f}%) — major opportunity")
            elif dd <= -20:
                score += 15
                reasons.append(f"Significant drawdown ({dd:.0f}%) from 52w high")
            elif dd <= -10:
                score += 5
                reasons.append(f"Moderate pullback ({dd:.0f}%) from 52w high")

        # 4. Position weight factor
        if current_weight > self.MAX_POSITION_WEIGHT:
            overweight = (current_weight - target_weight) / target_weight * 100
            score -= 10
            reasons.append(
                f"Overweight ({current_weight:.1%} vs {target_weight:.0%} target)"
            )
        elif current_weight < target_weight * 0.7:
            score += 5
            reasons.append(
                f"Underweight ({current_weight:.1%} vs {target_weight:.0%} target)"
            )

        # 5. Price vs 200 SMA
        if tech and tech.sma_200 > 0:
            if tech.price_vs_sma_200 < -10:
                score += 10
                reasons.append(f"Trading {tech.price_vs_sma_200:.0f}% below 200 SMA")
            elif tech.price_vs_sma_200 > 20:
                score -= 5
                reasons.append(f"Extended {tech.price_vs_sma_200:.0f}% above 200 SMA")

        # Determine action
        if score >= 80:
            action = SignalAction.STRONG_BUY
        elif score >= 65:
            action = SignalAction.BUY
        elif score >= 55:
            action = SignalAction.DCA
        elif score >= 40:
            action = SignalAction.HOLD
        elif score >= 25:
            action = SignalAction.TRIM
        else:
            action = SignalAction.SELL

        return PositionSignal(
            ticker=pos.ticker,
            name=pos.name,
            action=action,
            confidence=min(100, max(0, score)),
            reasons=reasons,
            current_weight=current_weight,
            target_weight=target_weight,
            technicals=tech,
        )

    def _overall_action(
        self,
        sentiment: AggregatedSentiment,
        signals: list[PositionSignal],
    ) -> SignalAction:
        """Determine overall portfolio action."""
        if sentiment.level == SentimentLevel.EXTREME_FEAR:
            return SignalAction.STRONG_BUY
        elif sentiment.level == SentimentLevel.FEAR:
            return SignalAction.BUY

        buy_count = sum(
            1 for s in signals
            if s.action in (SignalAction.STRONG_BUY, SignalAction.BUY, SignalAction.DCA)
        )
        if buy_count > len(signals) * 0.6:
            return SignalAction.DCA

        if sentiment.level == SentimentLevel.EXTREME_GREED:
            return SignalAction.TRIM

        return SignalAction.HOLD

    def _allocate_dca(
        self,
        budget: float,
        sentiment: AggregatedSentiment,
        signals: list[PositionSignal],
    ) -> float:
        """Adjust DCA budget based on sentiment — buy more in fear, less in greed."""
        multiplier = 1.0

        if sentiment.composite_score <= 20:
            multiplier = 2.0  # Double down in extreme fear
        elif sentiment.composite_score <= 40:
            multiplier = 1.5  # Increase in fear
        elif sentiment.composite_score >= 80:
            multiplier = 0.25  # Minimal in extreme greed
        elif sentiment.composite_score >= 60:
            multiplier = 0.5  # Reduce in greed

        adjusted = budget * multiplier

        # Allocate to positions with buy signals
        buy_signals = [
            s for s in signals
            if s.action in (SignalAction.STRONG_BUY, SignalAction.BUY, SignalAction.DCA)
        ]
        if buy_signals:
            total_confidence = sum(s.confidence for s in buy_signals)
            for s in buy_signals:
                s.suggested_amount_eur = round(
                    adjusted * (s.confidence / total_confidence), 2
                )

        return round(adjusted, 2)

    def _diversification_ideas(self, portfolio: PortfolioSnapshot) -> list[str]:
        """Suggest sectors/stocks missing from the portfolio."""
        held = {p.ticker for p in portfolio.positions}
        ideas = []

        sector_picks = {
            "Healthcare": ["UNH", "LLY", "JNJ"],
            "Financials": ["JPM", "V", "BRK-B"],
            "Consumer Staples": ["PG", "COST", "KO"],
            "Industrials": ["CAT", "HON", "UNP"],
            "International (ETF)": ["VXUS", "EFA"],
            "Bonds (ETF)": ["BND", "AGG"],
            "Gold / Commodities": ["GLD", "IAU"],
        }

        for sector, tickers in sector_picks.items():
            if not any(t in held for t in tickers):
                ideas.append(
                    f"{sector}: Consider {', '.join(tickers)} for diversification"
                )

        return ideas

    def _build_summary(
        self,
        overall: SignalAction,
        sentiment: AggregatedSentiment,
        signals: list[PositionSignal],
        dca_budget: float,
    ) -> str:
        """Build a human-readable summary."""
        buy_signals = [s for s in signals if s.action in (SignalAction.STRONG_BUY, SignalAction.BUY)]
        dca_signals = [s for s in signals if s.action == SignalAction.DCA]
        trim_signals = [s for s in signals if s.action in (SignalAction.TRIM, SignalAction.SELL)]

        lines = [
            f"Market Sentiment: {sentiment.level.value} ({sentiment.composite_score:.0f}/100)",
            f"Overall Signal: {overall.value}",
            f"Adjusted DCA Budget: EUR {dca_budget:.0f}",
            "",
        ]

        if buy_signals:
            tickers = ", ".join(s.ticker for s in buy_signals)
            lines.append(f"BUY signals: {tickers}")
        if dca_signals:
            tickers = ", ".join(s.ticker for s in dca_signals)
            lines.append(f"DCA signals: {tickers}")
        if trim_signals:
            tickers = ", ".join(s.ticker for s in trim_signals)
            lines.append(f"TRIM signals: {tickers}")

        lines.append("")
        lines.append(
            "REMINDER: 0.35% TOB applies to each transaction (Belgium). "
            "Factor into sizing."
        )

        return "\n".join(lines)
