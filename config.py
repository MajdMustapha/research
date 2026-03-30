"""
Configuration for the Polymarket Weather Arbitrage Bot.
All values loaded from environment / .env file.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field


class Config(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # ── Trading ──────────────────────────────────────────────────────
    LIVE_MODE: bool = False
    MIN_EDGE_PCT: float = 0.14
    MAX_COST_PER_BUCKET: float = 3.0
    MAX_COST_PER_LADDER: float = 15.0
    MAX_OPEN_LADDERS: int = 10
    DAILY_LOSS_LIMIT: float = 20.0
    EARLY_CLOSE_MULTIPLIER: float = 2.5
    MAX_CAPITAL_IN_OPEN_POSITIONS: float = 0.60
    MAX_SLIPPAGE_PCT: float = 0.03
    ORDER_CANCEL_AFTER_MINUTES: int = 45
    RATE_LIMIT_ORDERS_PER_MINUTE: int = 50

    # ── Fees (14a) ──────────────────────────────────────────────────
    FEE_CATEGORY: str = "weather"

    # ── Position sizing — fractional Kelly (14e) ────────────────────
    KELLY_ALPHA: float = 0.20
    KELLY_ALPHA_DEGRADED: float = 0.10
    MIN_COST_PER_BUCKET: float = 1.0

    # ── Forecast model ──────────────────────────────────────────────
    SIGMA_24H: float = 1.5
    SIGMA_48H: float = 2.2
    MODEL_CONSENSUS_MAX_SPREAD: float = 2.0
    ENSEMBLE_MIN_MEMBERS: int = 20
    ENSEMBLE_MIN_CONFIDENCE: float = 0.70

    # ── Crowding detector ───────────────────────────────────────────
    MIN_OPPORTUNITY_SCORE: float = 50.0
    CROWD_RANK_REFRESH_DAYS: int = 7

    # ── Correlated city groups (14d) ────────────────────────────────
    CORRELATED_CITY_GROUPS: dict = {
        "NA_east": ["New York", "Chicago", "Miami"],
        "EU": ["London", "Ankara"],
        "SA": ["Buenos Aires", "São Paulo"],
        "APAC": ["Seoul", "Mumbai"],
    }
    MAX_LADDERS_PER_CITY_GROUP: int = 1

    # ── Model health thresholds (14f) ──────────────────────────────
    BRIER_DEGRADED_THRESHOLD: float = 0.25
    BRIER_SUSPEND_THRESHOLD: float = 0.35
    EDGE_DECAY_SUSPEND: float = 0.08
    EDGE_DECAY_CAUTION: float = 0.10

    # ── Schedule (UTC) ──────────────────────────────────────────────
    GFS_TRIGGER_TIMES: list = ["00:30", "06:30", "12:30", "18:30"]
    ECMWF_TRIGGER_TIMES: list = ["00:30", "12:30"]
    ENTRY_WINDOW_MINUTES: int = 60

    # ── Polymarket ──────────────────────────────────────────────────
    POLYMARKET_PRIVATE_KEY: str = ""
    POLYMARKET_WALLET_ADDRESS: str = ""
    CHAIN_ID: int = 137
    IEM_COVERAGE_FALLBACK_THRESHOLD: float = 0.20

    # ── Alerting — Telegram (14m) ──────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # ── Paper trading ──────────────────────────────────────────────
    PAPER_MODE: str = "conservative"

    # ── VPS / agent loop ───────────────────────────────────────────
    AGENT_STATE_PATH: str = "data/agent_state.json"
    SLEEP_BETWEEN_CYCLES_SECONDS: int = 300

    # ── Cities ─────────────────────────────────────────────────────
    CITIES: list = Field(default=[
        {
            "name": "Buenos Aires",
            "lat": -34.61, "lon": -58.38,
            "active": True,
            "resolution_station": "SAEZ",
            "entry_window_minutes": 65,
            "opportunity_score": None,
            "sigma_table": {},
        },
        {
            "name": "Seoul",
            "lat": 37.57, "lon": 126.98,
            "active": True,
            "resolution_station": "RKSS",
            "entry_window_minutes": 45,
            "opportunity_score": None,
            "sigma_table": {},
        },
        {
            "name": "New York",
            "lat": 40.71, "lon": -74.01,
            "active": True,
            "resolution_station": "KLGA",
            "entry_window_minutes": 12,
            "opportunity_score": None,
            "sigma_table": {},
        },
        {
            "name": "London",
            "lat": 51.51, "lon": -0.13,
            "active": True,
            "resolution_station": "EGLC",
            "entry_window_minutes": 10,
            "opportunity_score": None,
            "sigma_table": {},
        },
        {
            "name": "Chicago",
            "lat": 41.88, "lon": -87.63,
            "active": True,
            "resolution_station": "KORD",
            "entry_window_minutes": 20,
            "opportunity_score": None,
            "sigma_table": {},
        },
        {
            "name": "Ankara",
            "lat": 39.93, "lon": 32.86,
            "active": True,
            "resolution_station": "LTAC",
            "entry_window_minutes": 90,
            "opportunity_score": None,
            "sigma_table": {},
        },
        {
            "name": "Miami",
            "lat": 25.77, "lon": -80.19,
            "active": True,
            "resolution_station": "KMIA",
            "entry_window_minutes": 30,
            "opportunity_score": None,
            "sigma_table": {},
        },
    ])
