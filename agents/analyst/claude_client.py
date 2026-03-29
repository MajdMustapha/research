"""
Claude client for market signal analysis.
Uses `claude` CLI (Claude Max subscription) instead of API key.
Sonnet for full analysis, Haiku for cheap news screening.
"""

import json
import os
import re
import subprocess

import yaml

from agents.analyst.prompts import ANALYST_SYSTEM_PROMPT, HAIKU_SCREENER_PROMPT
from lib.logger import get_logger

logger = get_logger(__name__)

# JSON schema for structured analyst output
_ANALYST_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "market_id": {"type": "string"},
        "signal_valid": {"type": "boolean"},
        "estimated_true_prob": {"type": "number"},
        "current_market_prob": {"type": "number"},
        "edge": {"type": "number"},
        "confidence": {"type": "number"},
        "recommended_side": {"type": "string", "enum": ["yes", "no", "none"]},
        "hold_duration_days": {"type": "integer"},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
        "resolution_rule_concern": {"type": "boolean"},
        "data_quality_concern": {"type": "boolean"},
    },
    "required": [
        "signal_valid", "estimated_true_prob", "current_market_prob",
        "edge", "confidence", "recommended_side",
    ],
})


def _call_claude(
    prompt: str,
    system_prompt: str,
    model: str = "sonnet",
    max_tokens: int = 1000,
    json_schema: str | None = None,
) -> str:
    """
    Call Claude via CLI. Uses Max subscription — no API key needed.
    Returns raw text output.
    """
    cmd = [
        "claude",
        "-p", prompt,
        "--model", model,
        "--output-format", "text",
        "--append-system-prompt", system_prompt,
        "--no-session-persistence",
        "--bare",
    ]
    if json_schema:
        cmd.extend(["--json-schema", json_schema])

    logger.debug(f"Claude CLI call: model={model}, prompt_len={len(prompt)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        )
        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI error (exit {result.returncode}): {result.stderr[:300]}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude CLI timed out after 120s")


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
    Raises on CLI error or invalid JSON response.
    """
    settings = _load_settings()
    min_edge = get_min_edge_for_market(market, settings)
    model = settings.get("analyst", {}).get("claude_model", "sonnet")

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

    raw = _call_claude(
        prompt=user_message,
        system_prompt=ANALYST_SYSTEM_PROMPT,
        model=model,
        max_tokens=1000,
        json_schema=_ANALYST_SCHEMA,
    )

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Strip markdown fences if present
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
    Fast Haiku call to check if news affects any tracked market.
    Returns list of relevant market IDs.
    """
    settings = _load_settings()
    model = settings.get("analyst", {}).get("screener_model", "haiku")

    market_list = "\n".join(
        [f"- {m['id']}: {m['question']}" for m in markets[:30]]
    )

    prompt = (
        f"Headline: {headline}\n\n"
        f"Markets:\n{market_list}\n\n"
        "Which market IDs are affected? Output JSON array only."
    )

    try:
        raw = _call_claude(
            prompt=prompt,
            system_prompt=HAIKU_SCREENER_PROMPT,
            model=model,
            max_tokens=200,
        )
        return json.loads(raw)
    except (json.JSONDecodeError, RuntimeError) as e:
        logger.warning(f"News screener failed: {e}")
        return []
