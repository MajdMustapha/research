#!/usr/bin/env python3
"""Score fundamental health using rule-based analysis.

Usage: python skills/score_fundamentals.py NVDA
Reads: workspace/{DATE}/{TICKER}/raw_data.json
Writes: workspace/{DATE}/{TICKER}/fundamental_score.json

Rule-based scoring (no LLM):
  - earnings_beat_streak
  - analyst_upside_pct
  - forward_pe_signal
  - revenue_growth_tier
  - debt_signal
  - insider_net
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.utils import read_json, write_json, error_exit, timestamp_now


def score_earnings(earnings: list) -> dict:
    """Score earnings beat streak and surprise quality."""
    if not earnings:
        return {"beat_streak": 0, "avg_surprise_pct": 0, "score": 0, "detail": "no earnings data"}

    beat_streak = 0
    surprises = []
    for q in earnings:
        actual = q.get("actual")
        estimate = q.get("estimate")
        if actual is not None and estimate is not None:
            surprise_pct = ((actual - estimate) / abs(estimate) * 100) if estimate != 0 else 0
            surprises.append(surprise_pct)
            if actual >= estimate:
                beat_streak += 1
            else:
                break  # streak broken

    avg_surprise = sum(surprises) / len(surprises) if surprises else 0

    # Score: 0-20 points
    score = 0
    if beat_streak >= 4:
        score = 20
    elif beat_streak >= 3:
        score = 15
    elif beat_streak >= 2:
        score = 10
    elif beat_streak >= 1:
        score = 5

    # Bonus for large surprises (capped at 20)
    if avg_surprise > 10:
        score = min(20, score + 5)

    score = min(20, score)

    return {
        "beat_streak": beat_streak,
        "avg_surprise_pct": round(avg_surprise, 2),
        "score": score,
        "detail": f"Beat {beat_streak}/4 quarters, avg surprise {avg_surprise:.1f}%",
    }


def score_analyst_consensus(recommendations: list, price_target: dict, current_price: float) -> dict:
    """Score analyst consensus and price target upside."""
    result = {"consensus_score": 0, "upside_score": 0, "total_score": 0}

    # Consensus: 0-10 points
    if recommendations:
        latest = recommendations[0] if recommendations else {}
        buy = latest.get("buy", 0) + latest.get("strongBuy", 0)
        hold = latest.get("hold", 0)
        sell = latest.get("sell", 0) + latest.get("strongSell", 0)
        total = buy + hold + sell
        if total > 0:
            buy_pct = buy / total
            if buy_pct > 0.7:
                result["consensus_score"] = 10
                result["consensus"] = "strong buy"
            elif buy_pct > 0.5:
                result["consensus_score"] = 8
                result["consensus"] = "buy"
            elif buy_pct > 0.3:
                result["consensus_score"] = 5
                result["consensus"] = "hold"
            else:
                result["consensus_score"] = 2
                result["consensus"] = "sell-leaning"
            result["buy_pct"] = round(buy_pct * 100, 1)

    # Price target upside: 0-10 points
    if price_target and current_price > 0:
        target_mean = price_target.get("targetMean") or price_target.get("targetMedian")
        target_high = price_target.get("targetHigh")
        target_low = price_target.get("targetLow")

        if target_mean:
            upside_pct = (target_mean - current_price) / current_price * 100
            result["target_mean"] = round(target_mean, 2)
            result["upside_pct"] = round(upside_pct, 2)
            if target_high:
                result["target_high"] = round(target_high, 2)
            if target_low:
                result["target_low"] = round(target_low, 2)

            if upside_pct > 30:
                result["upside_score"] = 10
            elif upside_pct > 20:
                result["upside_score"] = 8
            elif upside_pct > 10:
                result["upside_score"] = 5
            elif upside_pct > 0:
                result["upside_score"] = 3
            else:
                result["upside_score"] = 0

    result["total_score"] = result["consensus_score"] + result["upside_score"]
    return result


def score_valuation(financials: dict) -> dict:
    """Score PE ratio and valuation metrics."""
    metric = financials.get("metric", {}) if financials else {}
    pe = metric.get("peNormalizedAnnual") or metric.get("peTTM")
    pb = metric.get("pbAnnual") or metric.get("pbQuarterly")
    ps = metric.get("psAnnual") or metric.get("psTTM")

    score = 10  # neutral start
    signals = []

    if pe is not None:
        if pe < 15:
            score = 20
            pe_signal = "cheap"
        elif pe < 25:
            score = 15
            pe_signal = "fair"
        elif pe < 40:
            score = 10
            pe_signal = "elevated"
        elif pe < 60:
            score = 5
            pe_signal = "expensive"
        else:
            score = 0
            pe_signal = "very expensive"
        signals.append(f"PE {pe:.1f} ({pe_signal})")
    else:
        pe_signal = "unavailable"

    return {
        "pe_ratio": round(pe, 2) if pe else None,
        "pe_signal": pe_signal,
        "pb_ratio": round(pb, 2) if pb else None,
        "ps_ratio": round(ps, 2) if ps else None,
        "score": score,
        "signals": signals,
    }


def score_growth(financials: dict) -> dict:
    """Score revenue growth."""
    metric = financials.get("metric", {}) if financials else {}

    rev_growth = metric.get("revenueGrowthQuarterlyYoy") or metric.get("revenueGrowth3Y")
    eps_growth = metric.get("epsGrowthQuarterlyYoy") or metric.get("epsGrowth3Y")

    score = 10  # neutral
    tier = "unknown"

    if rev_growth is not None:
        if rev_growth > 50:
            score = 20
            tier = "hypergrowth"
        elif rev_growth > 20:
            score = 15
            tier = "growth"
        elif rev_growth > 5:
            score = 10
            tier = "mature"
        elif rev_growth > 0:
            score = 5
            tier = "slow"
        else:
            score = 0
            tier = "declining"

    return {
        "revenue_growth_yoy": round(rev_growth, 2) if rev_growth else None,
        "eps_growth_yoy": round(eps_growth, 2) if eps_growth else None,
        "growth_tier": tier,
        "score": score,
    }


def score_insiders(insider_data: dict) -> dict:
    """Score insider buying/selling activity."""
    transactions = insider_data.get("data", []) if insider_data else []

    if not transactions:
        return {"net_signal": "neutral", "score": 10, "detail": "no insider data"}

    buy_value = 0
    sell_value = 0
    for tx in transactions[:20]:  # last 20 transactions
        change = tx.get("change", 0)
        if change > 0:
            buy_value += change
        elif change < 0:
            sell_value += abs(change)

    if buy_value > sell_value * 2:
        return {"net_signal": "buying", "score": 20, "detail": f"Net insider buying (buys {buy_value:.0f} vs sells {sell_value:.0f})"}
    elif sell_value > buy_value * 2:
        return {"net_signal": "selling", "score": 5, "detail": f"Net insider selling (sells {sell_value:.0f} vs buys {buy_value:.0f})"}
    else:
        return {"net_signal": "neutral", "score": 10, "detail": "Mixed insider activity"}


def score_debt(financials: dict) -> dict:
    """Score balance sheet health."""
    metric = financials.get("metric", {}) if financials else {}

    debt_equity = metric.get("totalDebt/totalEquityAnnual")
    current_ratio = metric.get("currentRatioAnnual")
    roe = metric.get("roeRtnOnEquityTTM") or metric.get("roeTTM")

    score = 10
    signal = "unknown"

    if debt_equity is not None:
        if debt_equity < 0.3:
            score = 20
            signal = "clean"
        elif debt_equity < 1.0:
            score = 15
            signal = "moderate"
        elif debt_equity < 2.0:
            score = 10
            signal = "leveraged"
        else:
            score = 5
            signal = "highly leveraged"

    return {
        "debt_to_equity": round(debt_equity, 2) if debt_equity else None,
        "current_ratio": round(current_ratio, 2) if current_ratio else None,
        "roe": round(roe, 2) if roe else None,
        "debt_signal": signal,
        "score": score,
    }


def main():
    parser = argparse.ArgumentParser(description="Score fundamentals")
    parser.add_argument("ticker", help="Stock ticker symbol")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    try:
        raw = read_json("raw_data.json", ticker)
    except FileNotFoundError:
        error_exit(f"raw_data.json not found for {ticker}. Run fetch skills first.")

    # Get current price
    quote = raw.get("quote", {})
    current_price = quote.get("c", 0)

    # Score each dimension (0-20 points each, 120 max → normalize to 100)
    earnings_result = score_earnings(raw.get("earnings", []))
    analyst_result = score_analyst_consensus(
        raw.get("recommendations", []),
        raw.get("price_target", {}),
        current_price,
    )
    valuation_result = score_valuation(raw.get("financials", {}))
    growth_result = score_growth(raw.get("financials", {}))
    insider_result = score_insiders(raw.get("insider_transactions", {}))
    debt_result = score_debt(raw.get("financials", {}))

    # Total score: sum of 6 dimensions (0-20 each = 0-120), normalize to 0-100
    raw_score = (
        earnings_result["score"]
        + analyst_result["total_score"]
        + valuation_result["score"]
        + growth_result["score"]
        + insider_result["score"]
        + debt_result["score"]
    )
    # Max possible = 120 (6 × 20), normalize to 100
    normalized_score = round(raw_score / 120 * 100)

    # Grade
    if normalized_score >= 85:
        grade = "A"
    elif normalized_score >= 75:
        grade = "B+"
    elif normalized_score >= 65:
        grade = "B"
    elif normalized_score >= 55:
        grade = "C+"
    elif normalized_score >= 45:
        grade = "C"
    elif normalized_score >= 35:
        grade = "D"
    else:
        grade = "F"

    result = {
        "ticker": ticker,
        "timestamp": timestamp_now(),
        "fundamental_score": normalized_score,
        "grade": grade,
        "raw_score": raw_score,
        "max_raw_score": 120,
        "current_price": current_price,
        "earnings": earnings_result,
        "analyst_consensus": analyst_result,
        "valuation": valuation_result,
        "growth": growth_result,
        "insider_activity": insider_result,
        "balance_sheet": debt_result,
    }

    write_json(result, "fundamental_score.json", ticker)


if __name__ == "__main__":
    main()
