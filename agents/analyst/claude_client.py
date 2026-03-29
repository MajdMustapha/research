"""
Claude API client for market signal analysis.
Uses Sonnet for full analysis, Haiku for cheap news screening.
"""

import json
import os
import re

import yaml

from agents.analyst.prompts import ANALYST_SYSTEM_PROMPT, HAIKU_SCREENER_PROMPT
from lib.logger import get_logger

logger = get_logger(__name__)


def _get_client():
    """Lazy-load Anthropic client."""
    import anthropic
    return anthropic.Anthropic()


def _load_settings() -> dict:
    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        "settings.yaml",
    )
    with open(settings_path) as f:
        return yaml.safe_load(f)


def get_min_edge_for_market(market: dict, settings: dict = None) -> float:
    """Get category-appropriate minimum edge threshold."""
    if settings is None:
        settings = _load_settings()
    category = (market.get("category") or "").lower()
    thresholds = settings.get("min_edge_by_category", {})
    return thresholds.get(category, thresholds.get("default", 0.13))


def analyse_signal(
    market: dict,
    signal: dict,
    price_history: list,
) -> dict:
    """
    Call Claude Sonnet to analyse a market signal.
    Returns parsed analyst output dict.
    Raises on API error or invalid JSON response.
    """
    settings = _load_settings()
    min_edge = get_min_edge_for_market(market, settings)
    model = settings.get("analyst", {}).get("sonnet_model", "claude-sonnet-4-6")

    user_message = f"""
MARKET:
- Question: {market.get('question')}
- Current YES price: {market.get('outcomePrices', [0])[0]}
- Resolution deadline: {market.get('endDate')}
- Resolution criteria: {(market.get('description') or 'Not provided')[:500]}
- 24h volume: {market.get('volume24hr', 'unknown')}
- Category: {market.get('category', 'unknown')}

MINIMUM EDGE THRESHOLD FOR THIS MARKET CATEGORY: {min_edge:.0%}
If your estimated edge is below this threshold, set signal_valid=false.
This threshold accounts for taker fees in this category.

SIGNAL TYPE: {signal.get('source')}
SIGNAL DETAILS: {json.dumps(signal, indent=2, default=str)[:1000]}

RECENT PRICE HISTORY (last 7 days, daily):
{json.dumps(price_history[-7:] if price_history else [], indent=2)}

Analyse this signal and return JSON only.
"""

    logger.info(f"Calling Claude analyst for market: {market.get('id')}")

    client = _get_client()
    response = client.messages.create(
        model=model,
        max_tokens=1000,
        system=ANALYST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = re.sub(r"```json|```", "", raw).strip()
        result = json.loads(cleaned)

    # Validate required fields
    required = [
        "signal_valid",
        "estimated_true_prob",
        "edge",
        "confidence",
        "recommended_side",
    ]
    for field in required:
        if field not in result:
            raise ValueError(f"Claude response missing field: {field}")

    logger.info(
        f"Analyst result: valid={result['signal_valid']}, "
        f"edge={result['edge']:.2f}, confidence={result['confidence']:.2f}"
    )
    return result


def screen_news_relevance(headline: str, markets: list[dict]) -> list[str]:
    """
    Fast, cheap Haiku call to check if news affects any tracked market.
    Returns list of relevant market IDs.
    """
    settings = _load_settings()
    model = settings.get("analyst", {}).get("haiku_model", "claude-haiku-4-5-20251001")

    market_list = "\n".join(
        [f"- {m['id']}: {m['question']}" for m in markets[:30]]
    )

    client = _get_client()
    response = client.messages.create(
        model=model,
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Headline: {headline}\n\n"
                    f"Markets:\n{market_list}\n\n"
                    "Which market IDs are affected? Output JSON array only."
                ),
            }
        ],
        system=HAIKU_SCREENER_PROMPT,
    )

    try:
        return json.loads(response.content[0].text.strip())
    except (json.JSONDecodeError, IndexError):
        return []
