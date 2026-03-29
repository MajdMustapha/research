"""Tests for lib/cli.py — CLI wrapper integration tests."""

import json
import pytest
from unittest.mock import patch, MagicMock

from lib.cli import (
    _run,
    get_market,
    get_market_price,
    create_limit_order,
    PolymarketCLIError,
)


class TestCLIRunner:
    def test_cli_parses_market_json(self):
        """CLI wrapper correctly parses market JSON output."""
        mock_output = json.dumps({
            "id": "market_123",
            "question": "Will X happen?",
            "outcomePrices": ["0.65", "0.35"],
        })
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mock_output

        with patch("lib.cli.subprocess.run", return_value=mock_result):
            result = _run(["markets", "get", "market_123"])
            assert result["id"] == "market_123"
            assert result["question"] == "Will X happen?"

    def test_cli_raises_on_error(self):
        """PolymarketCLIError raised on non-zero exit."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "Error: market not found"
        mock_result.stderr = ""

        with patch("lib.cli.subprocess.run", return_value=mock_result):
            with pytest.raises(PolymarketCLIError, match="CLI error"):
                _run(["markets", "get", "nonexistent"])

    def test_cli_raises_on_timeout(self):
        """PolymarketCLIError raised on subprocess timeout."""
        import subprocess

        with patch("lib.cli.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="test", timeout=30)):
            with pytest.raises(PolymarketCLIError, match="CLI timeout"):
                _run(["clob", "book", "token_1"])

    def test_cli_raises_on_invalid_json(self):
        """PolymarketCLIError raised on unparseable JSON."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json {{"

        with patch("lib.cli.subprocess.run", return_value=mock_result):
            with pytest.raises(PolymarketCLIError, match="JSON parse error"):
                _run(["markets", "get", "test"])

    def test_cli_returns_none_on_empty_output(self):
        """Empty stdout returns None instead of raising."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   "

        with patch("lib.cli.subprocess.run", return_value=mock_result):
            assert _run(["clob", "orders"]) is None

    def test_cli_parses_list_response(self):
        """CLI wrapper handles JSON array responses."""
        mock_output = json.dumps([
            {"id": "m1", "question": "Q1"},
            {"id": "m2", "question": "Q2"},
        ])
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mock_output

        with patch("lib.cli.subprocess.run", return_value=mock_result):
            result = _run(["markets", "list"])
            assert isinstance(result, list)
            assert len(result) == 2


class TestMarketFunctions:
    def test_get_market_calls_correct_args(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"id": "test_market"})

        with patch("lib.cli.subprocess.run", return_value=mock_result) as mock_run:
            get_market("test_market")
            args = mock_run.call_args[0][0]
            assert args == ["polymarket", "-o", "json", "markets", "get", "test_market"]

    def test_get_market_price_calls_correct_args(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"mid": "0.55"})

        with patch("lib.cli.subprocess.run", return_value=mock_result) as mock_run:
            get_market_price("token_abc")
            args = mock_run.call_args[0][0]
            assert "midpoint" in args
            assert "token_abc" in args


class TestOrderFunctions:
    def test_create_limit_order_includes_post_only(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"orderID": "order_1"})

        with patch("lib.cli.subprocess.run", return_value=mock_result) as mock_run, \
             patch("lib.cli._rate_limit_order"):
            create_limit_order("token_1", "buy", 0.50, 100.0, post_only=True)
            args = mock_run.call_args[0][0]
            assert "--post-only" in args
            assert "--price" in args
            assert "0.5000" in args

    def test_create_limit_order_without_post_only(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"orderID": "order_2"})

        with patch("lib.cli.subprocess.run", return_value=mock_result) as mock_run, \
             patch("lib.cli._rate_limit_order"):
            create_limit_order("token_1", "buy", 0.50, 100.0, post_only=False)
            args = mock_run.call_args[0][0]
            assert "--post-only" not in args
