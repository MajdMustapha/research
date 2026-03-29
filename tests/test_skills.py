#!/usr/bin/env python3
"""Tests for PortfolioMind skills."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.utils import (
    get_workspace_dir,
    write_json,
    read_json,
    merge_into_raw_data,
    load_config,
    compute_portfolio_value,
)


class TestUtils(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_root = None

    def test_get_workspace_dir_creates_directory(self):
        with patch("skills.utils.PROJECT_ROOT", Path(self.tmpdir)):
            d = get_workspace_dir("NVDA", "2026-03-29")
            self.assertTrue(d.exists())
            self.assertTrue(d.is_dir())
            self.assertEqual(d.name, "NVDA")

    def test_write_and_read_json(self):
        with patch("skills.utils.PROJECT_ROOT", Path(self.tmpdir)):
            data = {"ticker": "NVDA", "price": 167.50}
            write_json(data, "test.json", "NVDA", "2026-03-29")
            result = read_json("test.json", "NVDA", "2026-03-29")
            self.assertEqual(result["ticker"], "NVDA")
            self.assertEqual(result["price"], 167.50)

    def test_read_json_missing_file(self):
        with patch("skills.utils.PROJECT_ROOT", Path(self.tmpdir)):
            with self.assertRaises(FileNotFoundError):
                read_json("nonexistent.json", "NVDA", "2026-03-29")

    def test_merge_into_raw_data(self):
        with patch("skills.utils.PROJECT_ROOT", Path(self.tmpdir)):
            merge_into_raw_data({"c": 167.5}, "quote", "NVDA", "2026-03-29")
            merge_into_raw_data({"data": []}, "news", "NVDA", "2026-03-29")
            result = read_json("raw_data.json", "NVDA", "2026-03-29")
            self.assertIn("quote", result)
            self.assertIn("news", result)
            self.assertEqual(result["quote"]["c"], 167.5)

    def test_load_config(self):
        config = load_config()
        self.assertIn("portfolio", config)
        self.assertIn("tickers", config["portfolio"])
        self.assertEqual(len(config["portfolio"]["tickers"]), 7)
        self.assertIn("NVDA", config["portfolio"]["tickers"])

    def test_compute_portfolio_value(self):
        prices = {"NVDA": 170.0, "AMZN": 200.0, "CRM": 180.0,
                  "CRWD": 380.0, "GOOGL": 270.0, "META": 540.0, "MSFT": 360.0}
        result = compute_portfolio_value(prices)
        self.assertGreater(result["total_value"], 0)
        self.assertIn("positions", result)
        self.assertIn("NVDA", result["positions"])
        # Check that weights sum to ~100%
        total_weight = sum(p["weight_pct"] for p in result["positions"].values())
        self.assertAlmostEqual(total_weight, 100.0, places=0)


class TestDCAScore(unittest.TestCase):
    def test_dca_score_oversold(self):
        from skills.compute_technicals import compute_dca_score
        score, label, breakdown = compute_dca_score(
            rsi=25.0, price=150.0, sma200=180.0,
            pct_from_cost=-0.25, bb_pct_b=0.05,
            macd_hist=0.5, prev_macd_hist=0.3,
        )
        # RSI<30: +20, price<SMA200: +15, cost<-20%: +20, bb<0.1: +10, macd rising: +5
        # base 50 + 20 + 15 + 20 + 10 + 5 = 120 → clamped to 100
        self.assertEqual(score, 100)
        self.assertEqual(label, "STRONG BUY")

    def test_dca_score_overbought(self):
        from skills.compute_technicals import compute_dca_score
        score, label, breakdown = compute_dca_score(
            rsi=75.0, price=250.0, sma200=180.0,
            pct_from_cost=0.10, bb_pct_b=0.95,
            macd_hist=-0.5, prev_macd_hist=-0.3,
        )
        # RSI>70: -20, price>SMA200*1.2(216): -10, cost>5%: -10
        # base 50 - 20 - 10 - 10 = 10
        self.assertEqual(score, 10)
        self.assertEqual(label, "WAIT")

    def test_dca_score_neutral(self):
        from skills.compute_technicals import compute_dca_score
        score, label, breakdown = compute_dca_score(
            rsi=50.0, price=180.0, sma200=175.0,
            pct_from_cost=-0.03, bb_pct_b=0.50,
            macd_hist=-0.1, prev_macd_hist=-0.2,
        )
        # All neutral: base 50
        self.assertEqual(score, 50)
        self.assertEqual(label, "NIBBLE")


class TestSuggestedAllocation(unittest.TestCase):
    def test_strong_buy_allocation(self):
        from skills.compute_technicals import compute_suggested_allocation
        config = {"risk_rules": {"max_single_add_eur": 200, "max_position_pct": 0.30}}
        result = compute_suggested_allocation(85, 15.0, config)
        self.assertEqual(result["suggested_eur"], 200)
        self.assertFalse(result["veto"])

    def test_veto_on_high_weight(self):
        from skills.compute_technicals import compute_suggested_allocation
        config = {"risk_rules": {"max_single_add_eur": 200, "max_position_pct": 0.30}}
        result = compute_suggested_allocation(90, 35.0, config)
        self.assertEqual(result["suggested_eur"], 0)
        self.assertTrue(result["veto"])

    def test_wait_on_low_score(self):
        from skills.compute_technicals import compute_suggested_allocation
        config = {"risk_rules": {"max_single_add_eur": 200, "max_position_pct": 0.30}}
        result = compute_suggested_allocation(30, 10.0, config)
        self.assertEqual(result["suggested_eur"], 0)


class TestFundamentalScoring(unittest.TestCase):
    def test_score_earnings(self):
        from skills.score_fundamentals import score_earnings
        earnings = [
            {"actual": 1.5, "estimate": 1.2},
            {"actual": 1.3, "estimate": 1.1},
            {"actual": 1.0, "estimate": 0.9},
            {"actual": 0.8, "estimate": 0.7},
        ]
        result = score_earnings(earnings)
        self.assertEqual(result["beat_streak"], 4)
        self.assertEqual(result["score"], 20)

    def test_score_earnings_broken_streak(self):
        from skills.score_fundamentals import score_earnings
        earnings = [
            {"actual": 1.5, "estimate": 1.2},
            {"actual": 0.9, "estimate": 1.1},  # miss
            {"actual": 1.0, "estimate": 0.9},
        ]
        result = score_earnings(earnings)
        self.assertEqual(result["beat_streak"], 1)

    def test_score_earnings_empty(self):
        from skills.score_fundamentals import score_earnings
        result = score_earnings([])
        self.assertEqual(result["score"], 0)


class TestNewsSummarization(unittest.TestCase):
    def test_keyword_counting(self):
        from skills.summarize_news import count_keyword_hits
        text = "Strong growth in AI revenue beats expectations"
        bullish = count_keyword_hits(text, ["strong", "growth", "beat"])
        self.assertEqual(bullish, 3)

    def test_keyword_no_match(self):
        from skills.summarize_news import count_keyword_hits
        text = "Regular quarterly update"
        bearish = count_keyword_hits(text, ["crash", "decline", "loss"])
        self.assertEqual(bearish, 0)


if __name__ == "__main__":
    unittest.main()
