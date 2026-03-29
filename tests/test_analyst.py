"""Tests for agents/analyst/claude_client.py — Claude output structure validation."""

import json
import pytest
from unittest.mock import patch, MagicMock

from agents.analyst.claude_client import analyse_signal, screen_news_relevance, get_min_edge_for_market


MOCK_SETTINGS = {
    "analyst": {
        "sonnet_model": "claude-sonnet-4-6",
        "haiku_model": "claude-haiku-4-5-20251001",
        "min_confidence_to_signal": 0.65,
    },
    "min_edge_by_category": {
        "geopolitics": 0.08,
        "politics": 0.13,
        "finance": 0.13,
        "sports": 0.11,
        "crypto_directional": 0.17,
        "default": 0.13,
    },
}

MOCK_MARKET = {
    "id": "test_market_1",
    "question": "Will X happen by end of 2026?",
    "outcomePrices": ["0.45", "0.55"],
    "endDate": "2026-12-31T00:00:00Z",
    "description": "Resolves YES if X happens.",
    "volume24hr": "50000",
    "category": "politics",
}


def _make_mock_response(result_dict: dict):
    """Create a mock Anthropic response."""
    mock_content = MagicMock()
    mock_content.text = json.dumps(result_dict)
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


class TestAnalystOutput:
    def test_analyst_output_schema(self):
        """All required fields present in Claude response."""
        valid_result = {
            "market_id": "test_market_1",
            "signal_valid": True,
            "estimated_true_prob": 0.65,
            "current_market_prob": 0.45,
            "edge": 0.20,
            "confidence": 0.80,
            "recommended_side": "yes",
            "hold_duration_days": 14,
            "key_risks": ["risk1"],
            "reasoning": "Strong signal.",
            "resolution_rule_concern": False,
            "data_quality_concern": False,
        }

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_response(valid_result)

        with patch("agents.analyst.claude_client._get_client", return_value=mock_client), \
             patch("agents.analyst.claude_client._load_settings", return_value=MOCK_SETTINGS):
            result = analyse_signal(
                market=MOCK_MARKET,
                signal={"source": "news", "headline": "Breaking news"},
                price_history=[],
            )

            assert "signal_valid" in result
            assert "estimated_true_prob" in result
            assert "edge" in result
            assert "confidence" in result
            assert "recommended_side" in result

    def test_analyst_rejects_low_edge(self):
        """Signal with edge <10pp returns signal_valid=False."""
        low_edge_result = {
            "market_id": "test_market_1",
            "signal_valid": False,
            "estimated_true_prob": 0.48,
            "current_market_prob": 0.45,
            "edge": 0.03,
            "confidence": 0.80,
            "recommended_side": "none",
            "hold_duration_days": 0,
            "key_risks": [],
            "reasoning": "Edge too small.",
            "resolution_rule_concern": False,
            "data_quality_concern": False,
        }

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_response(low_edge_result)

        with patch("agents.analyst.claude_client._get_client", return_value=mock_client), \
             patch("agents.analyst.claude_client._load_settings", return_value=MOCK_SETTINGS):
            result = analyse_signal(
                market=MOCK_MARKET,
                signal={"source": "news"},
                price_history=[],
            )
            assert result["signal_valid"] is False
            assert result["edge"] < 0.10

    def test_analyst_rejects_low_confidence(self):
        """Signal with confidence <0.65 returns signal_valid=False."""
        low_conf_result = {
            "market_id": "test_market_1",
            "signal_valid": False,
            "estimated_true_prob": 0.70,
            "current_market_prob": 0.45,
            "edge": 0.25,
            "confidence": 0.40,
            "recommended_side": "none",
            "hold_duration_days": 0,
            "key_risks": ["uncertain data"],
            "reasoning": "Low confidence.",
            "resolution_rule_concern": False,
            "data_quality_concern": False,
        }

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_response(low_conf_result)

        with patch("agents.analyst.claude_client._get_client", return_value=mock_client), \
             patch("agents.analyst.claude_client._load_settings", return_value=MOCK_SETTINGS):
            result = analyse_signal(
                market=MOCK_MARKET,
                signal={"source": "news"},
                price_history=[],
            )
            assert result["signal_valid"] is False
            assert result["confidence"] < 0.65

    def test_analyst_handles_markdown_fences(self):
        """Claude response with markdown fences is cleaned and parsed."""
        valid_result = {
            "signal_valid": True,
            "estimated_true_prob": 0.65,
            "edge": 0.20,
            "confidence": 0.80,
            "recommended_side": "yes",
        }
        markdown_wrapped = f"```json\n{json.dumps(valid_result)}\n```"

        mock_content = MagicMock()
        mock_content.text = markdown_wrapped
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("agents.analyst.claude_client._get_client", return_value=mock_client), \
             patch("agents.analyst.claude_client._load_settings", return_value=MOCK_SETTINGS):
            result = analyse_signal(
                market=MOCK_MARKET,
                signal={"source": "news"},
                price_history=[],
            )
            assert result["signal_valid"] is True


class TestEdgeThresholds:
    def test_geopolitics_lowest_threshold(self):
        market = {"category": "geopolitics"}
        assert get_min_edge_for_market(market, MOCK_SETTINGS) == 0.08

    def test_crypto_highest_threshold(self):
        market = {"category": "crypto_directional"}
        assert get_min_edge_for_market(market, MOCK_SETTINGS) == 0.17

    def test_unknown_category_uses_default(self):
        market = {"category": "unknown_category"}
        assert get_min_edge_for_market(market, MOCK_SETTINGS) == 0.13

    def test_empty_category_uses_default(self):
        market = {}
        assert get_min_edge_for_market(market, MOCK_SETTINGS) == 0.13


class TestNewsScreener:
    def test_screen_returns_market_ids(self):
        mock_content = MagicMock()
        mock_content.text = '["market_1", "market_2"]'
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("agents.analyst.claude_client._get_client", return_value=mock_client), \
             patch("agents.analyst.claude_client._load_settings", return_value=MOCK_SETTINGS):
            result = screen_news_relevance(
                "Breaking: Major policy change",
                [{"id": "market_1", "question": "Q1"}, {"id": "market_2", "question": "Q2"}],
            )
            assert result == ["market_1", "market_2"]

    def test_screen_returns_empty_on_no_match(self):
        mock_content = MagicMock()
        mock_content.text = "[]"
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("agents.analyst.claude_client._get_client", return_value=mock_client), \
             patch("agents.analyst.claude_client._load_settings", return_value=MOCK_SETTINGS):
            result = screen_news_relevance("Unrelated headline", [])
            assert result == []
