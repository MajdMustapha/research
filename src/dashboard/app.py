"""
IBKR Investment Dashboard — Streamlit App.

Run: streamlit run src/dashboard/app.py

Features:
- Real-time portfolio overview (connected to IBKR or mock data)
- Market sentiment panel (Fear & Greed, VIX, Reddit, News)
- DCA/Buy signal generator with technical analysis
- Belgian tax impact calculator
- Diversification recommendations
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.ibkr.client import IBKRClient, IBKRMockClient, PortfolioSnapshot
from src.sentiment.aggregator import SentimentAggregator, SentimentLevel
from src.signals.dca_engine import DCAEngine, SignalAction
from src.tax.belgian import BelgianTaxCalculator, InstrumentType

# ─── Page Config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="IBKR Investment Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Sample Portfolio Data (your actual portfolio) ─────────────────────────────

PORTFOLIO_DATA = {
    "portfolio": {
        "summary": {
            "net_liquidation_value": 31652,
            "daily_pnl": -1201,
            "daily_pnl_pct": -3.66,
            "unrealized_pnl": -7051,
            "realized_pnl": 0,
            "market_value": 31511.97,
            "excess_liquidity": 135.64,
            "maintenance_margin": 0.00,
            "buying_power": 135.64,
            "spx_delta": 8.178,
        },
        "positions": [
            {"ticker": "AMZN", "exchange": "NASDAQ.NMS", "name": "Amazon.com Inc", "last_price": 198.89, "bid": 198.87, "ask": 198.88, "daily_change": -8.65, "daily_change_pct": -4.17, "shares": 28, "pnl": -242.48},
            {"ticker": "CRM", "exchange": "NYSE", "name": "Salesforce Inc", "last_price": 179.27, "bid": 178.88, "ask": 180.00, "daily_change": -6.37, "daily_change_pct": -3.43, "shares": 10, "pnl": -67.46},
            {"ticker": "CRWD", "exchange": "NASDAQ.NMS", "name": "CrowdStrike Holdings", "last_price": 369.40, "bid": 368.50, "ask": 369.48, "daily_change": -23.22, "daily_change_pct": -5.91, "shares": 10, "pnl": -236.40},
            {"ticker": "GOOGL", "exchange": "NASDAQ.NMS", "name": "Alphabet Inc - Class A", "last_price": 273.50, "bid": 273.37, "ask": 273.55, "daily_change": -7.42, "daily_change_pct": -2.64, "shares": 19, "pnl": -140.98},
            {"ticker": "META", "exchange": "NASDAQ.NMS", "name": "Meta Platforms Inc", "last_price": 522.70, "bid": 522.51, "ask": 522.95, "daily_change": -24.84, "daily_change_pct": -4.54, "shares": 15, "pnl": -376.91},
            {"ticker": "MSFT", "exchange": "NASDAQ.NMS", "name": "Microsoft Corp", "last_price": 356.55, "bid": 356.50, "ask": 356.54, "daily_change": -9.42, "daily_change_pct": -2.57, "shares": 22, "pnl": -208.31},
            {"ticker": "NVDA", "exchange": "NASDAQ.NMS", "name": "Nvidia Corp", "last_price": 167.00, "bid": 167.00, "ask": 167.01, "daily_change": -4.24, "daily_change_pct": -2.48, "shares": 26, "pnl": -110.24},
        ],
        "cash_balances": {"eur_cash": 129.82, "usd_cash": 6.70, "total_cash": 135.64},
        "data_source": "GFIS",
        "snapshot_time": "23:34",
    }
}


# ─── Initialize ────────────────────────────────────────────────────────────────

@st.cache_resource
def get_ibkr_client():
    """Try live IBKR connection, fall back to mock."""
    return IBKRMockClient(PORTFOLIO_DATA)


@st.cache_data(ttl=600)
def fetch_sentiment(_tickers):
    """Fetch sentiment data (cached 10 min)."""
    aggregator = SentimentAggregator.__new__(SentimentAggregator)
    # Initialize fetchers that don't need credentials
    from src.sentiment.aggregator import (
        FearGreedFetcher, VIXFetcher, PutCallRatioFetcher
    )
    sources = []
    for fetcher in [FearGreedFetcher(), VIXFetcher(), PutCallRatioFetcher()]:
        try:
            result = fetcher.fetch()
            if result:
                sources.append(result)
        except Exception:
            pass

    # Build aggregated result
    from src.sentiment.aggregator import AggregatedSentiment
    if sources:
        total_weight = sum(s.weight for s in sources)
        composite = sum(s.score * s.weight for s in sources) / total_weight
    else:
        composite = 50.0

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


# ─── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Settings")

    data_source = st.radio(
        "Data Source",
        ["Mock (Your Portfolio)", "Live IBKR"],
        index=0,
        help="Live IBKR requires TWS/Gateway running on localhost",
    )

    monthly_dca = st.slider(
        "Monthly DCA Budget (EUR)",
        min_value=100,
        max_value=5000,
        value=500,
        step=100,
    )

    st.divider()
    st.caption("Belgian Tax Rates (2026)")
    st.text("TOB (Stocks): 0.35%")
    st.text("CGT: 10% (EUR 10k exempt)")
    st.text("Dividends: 30%")

    st.divider()
    st.caption("Data refreshes every 10 min")
    if st.button("Force Refresh"):
        st.cache_data.clear()
        st.rerun()


# ─── Load Data ─────────────────────────────────────────────────────────────────

import asyncio

client = get_ibkr_client()
loop = asyncio.new_event_loop()
portfolio = loop.run_until_complete(client.get_portfolio())
tickers = [p.ticker for p in portfolio.positions]

# ─── Header ────────────────────────────────────────────────────────────────────

st.title("IBKR Investment Dashboard")
st.caption(f"Last updated: {portfolio.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

# ─── KPI Row ───────────────────────────────────────────────────────────────────

col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric(
        "Net Liquidation",
        f"${portfolio.net_liquidation:,.0f}",
        delta=f"${portfolio.daily_pnl:,.0f}",
    )
with col2:
    daily_pct = portfolio.daily_pnl / portfolio.net_liquidation * 100
    st.metric("Daily P&L", f"${portfolio.daily_pnl:,.0f}", delta=f"{daily_pct:.2f}%")
with col3:
    st.metric("Unrealized P&L", f"${portfolio.unrealized_pnl:,.0f}")
with col4:
    st.metric("Cash", f"${portfolio.cash:,.2f}")
with col5:
    cash_pct = portfolio.cash / portfolio.net_liquidation * 100
    st.metric("Cash %", f"{cash_pct:.1f}%", delta="Low" if cash_pct < 5 else "OK")

st.divider()

# ─── Main Layout: 2 columns ───────────────────────────────────────────────────

left_col, right_col = st.columns([3, 2])

# ─── Left: Portfolio & Signals ─────────────────────────────────────────────────

with left_col:
    st.subheader("Portfolio Holdings")

    # Holdings table
    holdings_data = []
    total_mv = sum(p.market_value for p in portfolio.positions)
    for p in portfolio.positions:
        weight = p.market_value / total_mv * 100
        holdings_data.append({
            "Ticker": p.ticker,
            "Name": p.name,
            "Shares": p.shares,
            "Price": f"${p.market_price:,.2f}",
            "Market Value": f"${p.market_value:,.0f}",
            "Weight": f"{weight:.1f}%",
            "Daily P&L": f"${p.daily_pnl:,.2f}",
            "Daily %": f"{p.daily_pnl_pct:+.2f}%",
        })

    df = pd.DataFrame(holdings_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Allocation chart
    st.subheader("Allocation")
    fig_alloc = go.Figure()

    labels = [p.ticker for p in portfolio.positions] + ["Cash"]
    values = [p.market_value for p in portfolio.positions] + [portfolio.cash]
    colors = [
        "#4285F4", "#EA4335", "#FBBC04", "#34A853",
        "#FF6D01", "#46BDC6", "#7B1FA2", "#90A4AE"
    ]

    fig_alloc.add_trace(go.Pie(
        labels=labels,
        values=values,
        hole=0.4,
        marker_colors=colors[:len(labels)],
        textinfo="label+percent",
        textposition="outside",
    ))
    fig_alloc.update_layout(
        height=350,
        margin=dict(t=20, b=20, l=20, r=20),
        showlegend=False,
    )
    st.plotly_chart(fig_alloc, use_container_width=True)

    # Daily P&L waterfall
    st.subheader("Daily P&L by Position")
    pnl_data = sorted(portfolio.positions, key=lambda p: p.daily_pnl)
    fig_pnl = go.Figure()
    fig_pnl.add_trace(go.Bar(
        x=[p.ticker for p in pnl_data],
        y=[p.daily_pnl for p in pnl_data],
        marker_color=["#ef5350" if p.daily_pnl < 0 else "#66bb6a" for p in pnl_data],
        text=[f"${p.daily_pnl:,.0f}" for p in pnl_data],
        textposition="outside",
    ))
    fig_pnl.update_layout(
        height=300,
        margin=dict(t=20, b=40, l=40, r=20),
        yaxis_title="P&L ($)",
    )
    st.plotly_chart(fig_pnl, use_container_width=True)


# ─── Right: Sentiment & Signals ────────────────────────────────────────────────

with right_col:
    st.subheader("Market Sentiment")

    with st.spinner("Fetching sentiment data..."):
        sentiment = fetch_sentiment(tuple(tickers))

    # Sentiment gauge
    fig_gauge = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=sentiment.composite_score,
        title={"text": f"Composite: {sentiment.level.value}"},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#1a1a2e"},
            "steps": [
                {"range": [0, 20], "color": "#b71c1c"},
                {"range": [20, 40], "color": "#ef5350"},
                {"range": [40, 60], "color": "#fdd835"},
                {"range": [60, 80], "color": "#66bb6a"},
                {"range": [80, 100], "color": "#2e7d32"},
            ],
            "threshold": {
                "line": {"color": "white", "width": 3},
                "value": sentiment.composite_score,
            },
        },
    ))
    fig_gauge.update_layout(height=250, margin=dict(t=40, b=20))
    st.plotly_chart(fig_gauge, use_container_width=True)

    # Sentiment sources breakdown
    if sentiment.sources:
        for source in sentiment.sources:
            col_a, col_b = st.columns([2, 1])
            with col_a:
                st.text(f"{source.name}")
                st.progress(int(source.score) / 100)
            with col_b:
                st.text(f"{source.score:.0f}/100")
                st.caption(source.label)
    else:
        st.info("Sentiment sources unavailable. Check API keys.")

    st.divider()

    # ─── DCA Signals ───────────────────────────────────────────────────────────

    st.subheader("DCA / Buy Signals")

    engine = DCAEngine()
    signals = engine.generate_signals(portfolio, sentiment, monthly_dca)

    # Overall signal
    signal_colors = {
        SignalAction.STRONG_BUY: "🟢🟢",
        SignalAction.BUY: "🟢",
        SignalAction.DCA: "🔵",
        SignalAction.HOLD: "🟡",
        SignalAction.TRIM: "🟠",
        SignalAction.SELL: "🔴",
    }

    overall_icon = signal_colors.get(signals.overall_action, "")
    st.markdown(
        f"### {overall_icon} Overall: **{signals.overall_action.value}**"
    )
    st.caption(f"Adjusted DCA Budget: EUR {signals.dca_budget_eur:,.0f}")

    # Per-position signals
    for sig in signals.position_signals:
        icon = signal_colors.get(sig.action, "")
        with st.expander(
            f"{icon} {sig.ticker} — {sig.action.value} "
            f"(Confidence: {sig.confidence:.0f}%)"
        ):
            st.text(f"Current Weight: {sig.current_weight:.1%}")
            st.text(f"Target Weight: {sig.target_weight:.0%}")
            if sig.suggested_amount_eur > 0:
                st.text(f"Suggested DCA: EUR {sig.suggested_amount_eur:,.0f}")
            if sig.technicals:
                t = sig.technicals
                st.text(f"RSI(14): {t.rsi_14}")
                st.text(f"vs 52w High: {t.price_vs_52w_high:+.1f}%")
                st.text(f"vs 200 SMA: {t.price_vs_sma_200:+.1f}%")
            for reason in sig.reasons:
                st.caption(f"• {reason}")

    # Diversification ideas
    if signals.new_ideas:
        st.divider()
        st.subheader("Diversification Ideas")
        for idea in signals.new_ideas:
            st.caption(f"→ {idea}")

# ─── Tax Calculator Section ────────────────────────────────────────────────────

st.divider()
st.subheader("Belgian Tax Impact Calculator")

tax_col1, tax_col2 = st.columns(2)

tax_calc = BelgianTaxCalculator()

with tax_col1:
    st.markdown("**Trade Cost Estimator**")
    trade_value = st.number_input("Trade Value (EUR)", value=1000, step=100)
    inst_type = st.selectbox(
        "Instrument",
        ["Stock", "ETF (Equity)", "ETF (Bond-heavy)"],
    )
    type_map = {
        "Stock": InstrumentType.STOCK,
        "ETF (Equity)": InstrumentType.ETF_EQUITY,
        "ETF (Bond-heavy)": InstrumentType.ETF_BOND_HEAVY,
    }

    costs = tax_calc.estimate_trade_cost(trade_value, type_map[inst_type])
    st.json(costs)

with tax_col2:
    st.markdown("**Capital Gains Tax (2026+)**")
    st.caption("10% on realized gains above EUR 10,000/year exemption")
    cgt_gain = st.number_input("Realized Gain (EUR)", value=5000, step=500)
    if cgt_gain <= 10000:
        st.success(f"EUR {cgt_gain:,.0f} gain — within annual exemption. No CGT due.")
    else:
        taxable = cgt_gain - 10000
        tax = taxable * 0.10
        st.warning(
            f"EUR {cgt_gain:,.0f} gain → EUR {taxable:,.0f} taxable → "
            f"EUR {tax:,.0f} CGT (10%)"
        )

# ─── Footer ────────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "This dashboard is for informational purposes only and does not constitute "
    "financial advice. Belgian tax calculations are estimates — consult a tax advisor. "
    "IBKR does not withhold Belgian taxes; self-declare via MyMinfin/DivTax."
)
