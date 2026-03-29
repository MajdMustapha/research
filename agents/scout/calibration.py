"""
Operator calibration exercise.
Pulls 20 recently-resolved markets in a given domain.
For each: hides outcome, asks operator to estimate probability.
Scores: Brier score, direction accuracy, edge vs market price.
"""

from lib.cli import search_markets
from lib.logger import get_logger

logger = get_logger(__name__)

DOMAIN_SEARCH_TERMS = {
    "crypto": ["bitcoin price", "ethereum", "solana", "BTC", "ETH"],
    "politics": ["election", "president", "congress", "senate", "vote"],
    "sports": ["NBA", "NFL", "World Cup", "Super Bowl", "champion"],
    "macro": ["Fed rate", "inflation", "GDP", "recession", "interest rate"],
    "geopolitics": [
        "war", "ceasefire", "sanctions", "Ukraine", "Russia", "Taiwan",
        "China", "NATO", "trade deal", "invasion", "troops", "missile",
        "Maduro", "Iran", "Middle East", "coup",
    ],
}


def run_calibration_session(domain: str, num_markets: int = 20) -> dict:
    """
    Interactive calibration session.
    Returns calibration report dict.
    """
    print(f"\n=== CALIBRATION: {domain.upper()} ===")
    print(f"You will see {num_markets} resolved markets.")
    print("Before seeing the outcome, estimate the probability (0-100%).")
    print("This measures whether you have genuine edge in this domain.\n")

    markets = _fetch_resolved_markets(domain, num_markets)
    if len(markets) < 10:
        return {"error": f"Not enough resolved markets found for domain: {domain}"}

    results = []
    for i, market in enumerate(markets, 1):
        question = market.get("question", "Unknown market")
        market_price_at_close = _get_closing_price(market)
        actual_outcome = _get_actual_outcome(market)

        print(f"[{i}/{len(markets)}] {question}")
        print(f"  Market's final price before resolution: {market_price_at_close:.0%}")

        try:
            operator_input = input(
                "  Your probability estimate (0-100, or 's' to skip): "
            ).strip()
            if operator_input.lower() == "s":
                continue
            operator_prob = float(operator_input) / 100.0
            operator_prob = max(0.01, min(0.99, operator_prob))
        except (ValueError, EOFError):
            print("  Invalid input, skipping.")
            continue

        operator_brier = (operator_prob - actual_outcome) ** 2
        market_brier = (market_price_at_close - actual_outcome) ** 2
        operator_correct = (operator_prob > 0.5) == (actual_outcome > 0.5)
        edge = market_brier - operator_brier

        print(f"  Actual outcome: {'YES' if actual_outcome > 0.5 else 'NO'}")
        print(
            f"  Your Brier score: {operator_brier:.3f} | "
            f"Market Brier: {market_brier:.3f}"
        )
        print(
            f"  You {'BEAT' if edge > 0 else 'LOST TO'} the market on this one.\n"
        )

        results.append({
            "market_id": market.get("id"),
            "question": question,
            "operator_prob": operator_prob,
            "market_prob": market_price_at_close,
            "actual_outcome": actual_outcome,
            "operator_brier": operator_brier,
            "market_brier": market_brier,
            "operator_correct": operator_correct,
            "market_correct": (market_price_at_close > 0.5) == (actual_outcome > 0.5),
            "operator_beat_market": edge > 0,
        })

    return _score_calibration(results, domain)


def _score_calibration(results: list, domain: str) -> dict:
    if not results:
        return {"error": "No results to score"}

    n = len(results)
    avg_operator_brier = sum(r["operator_brier"] for r in results) / n
    avg_market_brier = sum(r["market_brier"] for r in results) / n
    beat_market_pct = sum(1 for r in results if r["operator_beat_market"]) / n
    direction_accuracy = sum(1 for r in results if r["operator_correct"]) / n

    has_edge = beat_market_pct >= 0.45 and direction_accuracy >= 0.55

    report = {
        "domain": domain,
        "markets_tested": n,
        "avg_operator_brier": round(avg_operator_brier, 4),
        "avg_market_brier": round(avg_market_brier, 4),
        "beat_market_pct": round(beat_market_pct, 3),
        "direction_accuracy": round(direction_accuracy, 3),
        "has_edge": has_edge,
        "verdict": (
            f"EDGE CONFIRMED in {domain}: you beat the market "
            f"{beat_market_pct:.0%} of the time "
            f"with {direction_accuracy:.0%} directional accuracy. Proceed."
        )
        if has_edge
        else (
            f"NO EDGE DETECTED in {domain}: you beat the market only "
            f"{beat_market_pct:.0%} of the time. "
            f"Do not deploy capital in this domain."
        ),
        "results": results,
    }

    print(f"\n=== CALIBRATION RESULTS: {domain.upper()} ===")
    print(f"Markets tested:       {n}")
    print(f"Your Brier score:     {avg_operator_brier:.4f}")
    print(f"Market Brier score:   {avg_market_brier:.4f}")
    print(f"You beat market:      {beat_market_pct:.0%} of markets")
    print(f"Direction accuracy:   {direction_accuracy:.0%}")
    print(f"\n{'PASS' if has_edge else 'FAIL'} {report['verdict']}\n")

    return report


def _fetch_resolved_markets(domain: str, limit: int) -> list:
    """Fetch recently resolved markets for a domain using CLI."""
    all_markets = []
    for term in DOMAIN_SEARCH_TERMS.get(domain, [domain]):
        try:
            results = search_markets(term, limit=20)
            if results:
                resolved = [m for m in results if m.get("closed") is True]
                all_markets.extend(resolved)
        except Exception as e:
            logger.warning(f"Failed to search for '{term}': {e}")

    seen = set()
    unique = []
    for m in all_markets:
        mid = m.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            unique.append(m)
    return unique[:limit]


def _get_closing_price(market: dict) -> float:
    """Get the final YES price before resolution."""
    prices = market.get("outcomePrices", ["0.5", "0.5"])
    try:
        return float(prices[0])
    except (ValueError, IndexError):
        return 0.5


def _get_actual_outcome(market: dict) -> float:
    """Return 1.0 if YES resolved, 0.0 if NO resolved."""
    prices = market.get("outcomePrices", ["0.5"])
    try:
        yes_price = float(prices[0])
        return 1.0 if yes_price >= 0.99 else 0.0
    except (ValueError, IndexError):
        return 0.5
