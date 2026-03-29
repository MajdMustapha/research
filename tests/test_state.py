"""Tests for lib/state.py — SQLite state machine tests."""

import os
import tempfile
import pytest
from unittest.mock import patch
from datetime import datetime, timezone

# Patch DATABASE_PATH before importing state
_test_db_dir = tempfile.mkdtemp()
_test_db_path = os.path.join(_test_db_dir, "test_state.db")


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Use a fresh temp database for each test."""
    db_path = str(tmp_path / "test_state.db")
    with patch("lib.state.DATABASE_PATH", db_path):
        from lib.state import init_db
        init_db()
        yield db_path


class TestPositions:
    def test_position_save_and_retrieve(self, setup_test_db):
        with patch("lib.state.DATABASE_PATH", setup_test_db):
            from lib.state import save_position, get_open_positions

            pos_id = save_position({
                "market_id": "market_1",
                "token_id": "token_1",
                "market_question": "Will X happen?",
                "side": "yes",
                "entry_price": 0.45,
                "size_usdc": 20.0,
                "shares": 44.44,
                "order_id": "order_abc",
                "layer": "layer1",
                "signal_summary": "News signal",
            })
            assert pos_id == 1

            positions = get_open_positions()
            assert len(positions) == 1
            assert positions[0]["market_id"] == "market_1"
            assert positions[0]["side"] == "yes"
            assert positions[0]["entry_price"] == 0.45
            assert positions[0]["status"] == "open"

    def test_close_position(self, setup_test_db):
        with patch("lib.state.DATABASE_PATH", setup_test_db):
            from lib.state import save_position, close_position, get_open_positions

            pos_id = save_position({
                "market_id": "market_2",
                "token_id": "token_2",
                "side": "no",
                "entry_price": 0.60,
                "size_usdc": 15.0,
                "shares": 25.0,
            })
            close_position(pos_id, exit_price=0.80, pnl=5.0)

            positions = get_open_positions()
            assert len(positions) == 0


class TestSignals:
    def test_signal_save_and_update(self, setup_test_db):
        with patch("lib.state.DATABASE_PATH", setup_test_db):
            from lib.state import save_signal, get_signal, update_signal_action

            signal_id = save_signal({
                "source": "news",
                "market_id": "market_1",
                "token_id": "token_1",
                "market_question": "Will X happen?",
                "current_price": 0.45,
                "estimated_true_prob": 0.65,
                "edge": 0.20,
                "confidence": 0.80,
                "claude_reasoning": "Strong signal based on news",
                "recommended_side": "yes",
            })
            assert signal_id == 1

            signal = get_signal(signal_id)
            assert signal is not None
            assert signal["source"] == "news"
            assert signal["edge"] == 0.20
            assert signal["action_taken"] is None

            now = datetime.now(timezone.utc)
            update_signal_action(signal_id, "approved", now)

            signal = get_signal(signal_id)
            assert signal["action_taken"] == "approved"


class TestBankroll:
    def test_bankroll_paper_mode(self, setup_test_db):
        """In paper mode, bankroll comes from settings.yaml."""
        with patch("lib.state.DATABASE_PATH", setup_test_db):
            from lib.state import get_bankroll
            # Default paper_bankroll in settings.yaml is 1000.0
            bankroll = get_bankroll()
            assert bankroll == 1000.0

    def test_lp_capital_calculation(self, setup_test_db):
        with patch("lib.state.DATABASE_PATH", setup_test_db):
            from lib.state import save_position, get_lp_capital

            # No positions: LP capital = full bankroll
            lp = get_lp_capital()
            assert lp == 1000.0

            # Add a position worth $200
            save_position({
                "market_id": "m1",
                "token_id": "t1",
                "side": "yes",
                "entry_price": 0.50,
                "size_usdc": 200.0,
                "shares": 400.0,
            })

            lp = get_lp_capital()
            assert lp == 800.0


class TestLPOrders:
    def test_lp_order_lifecycle(self, setup_test_db):
        with patch("lib.state.DATABASE_PATH", setup_test_db):
            from lib.state import save_lp_order, get_active_lp_orders, update_lp_order_status

            save_lp_order({
                "market_id": "lp_m1",
                "token_id": "lp_t1",
                "order_id": "lp_order_1",
                "side": "buy",
                "price": 0.48,
                "size": 50.0,
            })

            active = get_active_lp_orders()
            assert len(active) == 1
            assert active[0]["order_id"] == "lp_order_1"

            update_lp_order_status("lp_order_1", "cancelled")
            active = get_active_lp_orders()
            assert len(active) == 0


class TestPaperTrades:
    def test_paper_trade_save(self, setup_test_db):
        with patch("lib.state.DATABASE_PATH", setup_test_db):
            from lib.state import save_paper_trade, get_open_paper_trades

            pt_id = save_paper_trade({
                "signal_id": 1,
                "market_id": "pm1",
                "token_id": "pt1",
                "market_question": "Test?",
                "side": "yes",
                "entry_price": 0.40,
                "size_usdc": 20.0,
                "shares": 50.0,
                "layer": "layer1",
            })
            assert pt_id == 1

            trades = get_open_paper_trades()
            assert len(trades) == 1
            assert trades[0]["entry_price"] == 0.40


class TestSystemEvents:
    def test_log_event(self, setup_test_db):
        with patch("lib.state.DATABASE_PATH", setup_test_db):
            from lib.state import log_event, get_connection

            log_event("startup", "scanner", "Scanner started")

            conn = get_connection()
            rows = conn.execute("SELECT * FROM system_events").fetchall()
            conn.close()
            assert len(rows) == 1
            assert rows[0]["event_type"] == "startup"


class TestWALMode:
    def test_wal_mode_enabled(self, setup_test_db):
        with patch("lib.state.DATABASE_PATH", setup_test_db):
            from lib.state import get_connection

            conn = get_connection()
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()
            assert mode == "wal"
