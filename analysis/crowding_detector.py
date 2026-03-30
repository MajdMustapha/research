"""
City crowding detector.
Computes 5 metrics per city from historical CLOB and Goldsky data,
combines them into a single opportunity score (0–100), and writes
ranked city_configs + HTML report.
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from backtest.reporter import generate_crowding_report
from connectors.goldsky import fetch_first10_wallets, fetch_wallets_in_window
from connectors.polymarket_gamma import (
    fetch_price_history,
    fetch_resolved_weather_markets,
    parse_market_buckets,
)
from strategies.resolution_parser import parse_resolution_source

logger = logging.getLogger(__name__)

METRIC_WEIGHTS = {
    "ttr": 0.30,
    "dwc": 0.25,
    "edr": 0.20,
    "vcr": 0.15,
    "bas": 0.10,
}


@dataclass
class CityCrowdingReport:
    city: str
    period_days: int
    markets_analysed: int
    metrics: dict
    opportunity_score: float
    entry_window_minutes: int
    sigma_table: dict
    resolution_station: str
    ranked_at: str
    trend: str


# ── Metric computations ──────────────────────────────────────────────

def compute_ttr(
    price_history: list[tuple[int, float]],
    initial_gap: float,
    model_drop_ts: int,
) -> float:
    """Minutes until gap halved, or 120 if never halved within 2h."""
    if initial_gap <= 0 or not price_history:
        return 120.0

    gap_threshold = initial_gap * 0.5
    baseline_price = None

    for ts, price in price_history:
        if ts < model_drop_ts:
            continue
        if baseline_price is None:
            baseline_price = price
            continue
        remaining_gap = (baseline_price + initial_gap) - price
        if remaining_gap <= gap_threshold:
            return (ts - model_drop_ts) / 60
    return 120.0


def ttr_to_score(ttr_minutes: float) -> float:
    return min(100.0, (ttr_minutes / 120.0) * 100.0)


def dwc_to_score(distinct_wallets: int) -> float:
    return max(0.0, 100.0 * math.exp(-0.3 * distinct_wallets))


def compute_bas(order_book_at_t5: dict) -> float:
    bids = order_book_at_t5.get("bids", [])
    asks = order_book_at_t5.get("asks", [])
    if not bids or not asks:
        return 0.15
    best_bid = max(b[0] if isinstance(b, (list, tuple)) else float(b.get("price", 0)) for b in bids)
    best_ask = min(a[0] if isinstance(a, (list, tuple)) else float(a.get("price", 999)) for a in asks)
    if best_ask == 0:
        return 0.0
    return (best_ask - best_bid) / best_ask


def bas_to_score(spread_fraction: float) -> float:
    return min(100.0, (spread_fraction / 0.12) * 100.0)


def compute_edr(
    price_history: list[tuple[int, float]],
    model_prob: float,
    model_drop_ts: int,
) -> float:
    """Fraction of original edge remaining at T+60 min."""
    def price_at(target_ts: int) -> float | None:
        candidates = [(abs(ts - target_ts), p) for ts, p in price_history]
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[0])[1]

    p0 = price_at(model_drop_ts)
    p60 = price_at(model_drop_ts + 3600)
    if p0 is None or p60 is None:
        return 0.0

    edge_t0 = model_prob - p0
    edge_t60 = model_prob - p60
    if edge_t0 <= 0:
        return 0.0
    return max(0.0, min(1.0, edge_t60 / edge_t0))


def edr_to_score(edr: float) -> float:
    return min(100.0, (edr / 0.6) * 100.0)


def compute_vcr(token_ids: list[str], model_drop_ts: int) -> float:
    """Top-3 wallets' volume / total volume in first 60 min."""
    events = fetch_wallets_in_window(token_ids, model_drop_ts, duration_seconds=3600)
    if not events:
        return 0.0

    wallet_volumes: dict[str, float] = {}
    for e in events:
        w = e.get("maker", "").lower()
        vol = float(e.get("makerAmountFilled", 0))
        wallet_volumes[w] = wallet_volumes.get(w, 0) + vol

    if not wallet_volumes:
        return 0.0

    total_vol = sum(wallet_volumes.values())
    top3_vol = sum(sorted(wallet_volumes.values(), reverse=True)[:3])
    return top3_vol / total_vol if total_vol > 0 else 0.0


def vcr_to_score(vcr: float) -> float:
    return max(0.0, min(100.0, (1.0 - (vcr - 0.3) / 0.5) * 100.0))


def compute_opportunity_score(metrics: dict[str, float]) -> float:
    return sum(metrics.get(k, 0) * w for k, w in METRIC_WEIGHTS.items())


# ── Main detector class ──────────────────────────────────────────────

class CrowdingDetector:
    def __init__(self, db_path: str = "data/markets.db"):
        self.db_path = db_path

    def run(self, days: int = 90) -> list[CityCrowdingReport]:
        """
        Main entry point. Returns reports sorted by opportunity_score descending.
        """
        reports: list[CityCrowdingReport] = []
        raw_markets = fetch_resolved_weather_markets(days=days)

        # Group markets by city
        city_markets: dict[str, list[dict]] = {}
        for m in raw_markets:
            q = (m.get("question", "") or "").lower()
            for city_name in ("new york", "london", "chicago", "buenos aires",
                              "seoul", "ankara", "miami", "mumbai", "são paulo"):
                if city_name in q:
                    city_markets.setdefault(city_name.title(), []).append(m)
                    break

        for city, markets in city_markets.items():
            if len(markets) < 10:
                continue

            report = self._analyse_city(city, markets, days)
            if report:
                reports.append(report)

        reports.sort(key=lambda r: r.opportunity_score, reverse=True)
        self._write_db(reports)
        self._write_config_overrides(reports)

        report_dicts = [self._report_to_dict(r) for r in reports]
        generate_crowding_report(report_dicts)

        return reports

    def _analyse_city(
        self, city: str, markets: list[dict], days: int,
    ) -> CityCrowdingReport | None:
        all_ttr, all_dwc, all_bas, all_edr, all_vcr = [], [], [], [], []
        station_ids: set[str] = set()
        sigma_errors: dict[int, list[float]] = {}

        for market in markets:
            desc = market.get("description", "")
            src = parse_resolution_source(desc)
            if src.station_id != "UNKNOWN":
                station_ids.add(src.station_id)

            buckets = parse_market_buckets(market)
            token_ids = [b["token_id"] for b in buckets if b.get("token_id")]
            if not token_ids:
                continue

            # Approximate model drop timestamp (midnight UTC of market date)
            end_date = market.get("end_date_iso") or market.get("closed_time", "")
            try:
                dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                drop_ts = int((dt - timedelta(hours=24)).replace(
                    hour=0, minute=30, second=0
                ).timestamp())
            except (ValueError, TypeError):
                continue

            # Metric 2: DWC
            try:
                wallets = fetch_first10_wallets(token_ids, drop_ts)
                all_dwc.append(len(wallets))
            except Exception:
                pass

            # Metric 5: VCR
            try:
                vcr = compute_vcr(token_ids, drop_ts)
                all_vcr.append(vcr)
            except Exception:
                pass

            # Metrics 1, 3, 4 require price history (approximate from available data)
            if token_ids:
                try:
                    ph = fetch_price_history(
                        token_ids[0], drop_ts, drop_ts + 7200, fidelity=60
                    )
                    if ph:
                        # TTR
                        initial_gap = 0.10  # approximate
                        ttr = compute_ttr(ph, initial_gap, drop_ts)
                        all_ttr.append(ttr)

                        # BAS (approximate from price spread at T+5)
                        t5_prices = [p for ts, p in ph if drop_ts + 240 <= ts <= drop_ts + 360]
                        if len(t5_prices) >= 2:
                            spread = max(t5_prices) - min(t5_prices)
                            mid = statistics.mean(t5_prices)
                            all_bas.append(spread / mid if mid > 0 else 0.15)

                        # EDR
                        edr = compute_edr(ph, 0.5, drop_ts)  # model_prob ~0.5 default
                        all_edr.append(edr)
                except Exception:
                    pass

        # Aggregate
        med_ttr = statistics.median(all_ttr) if all_ttr else 30
        med_dwc = statistics.median(all_dwc) if all_dwc else 5
        med_bas = statistics.median(all_bas) if all_bas else 0.05
        med_edr = statistics.median(all_edr) if all_edr else 0.3
        med_vcr = statistics.median(all_vcr) if all_vcr else 0.6

        raw_metrics = {
            "ttr": ttr_to_score(med_ttr),
            "dwc": dwc_to_score(int(med_dwc)),
            "bas": bas_to_score(med_bas),
            "edr": edr_to_score(med_edr),
            "vcr": vcr_to_score(med_vcr),
        }

        score = compute_opportunity_score(raw_metrics)
        station = list(station_ids)[0] if station_ids else "UNKNOWN"
        now_iso = datetime.now(timezone.utc).isoformat()

        return CityCrowdingReport(
            city=city,
            period_days=days,
            markets_analysed=len(markets),
            metrics=raw_metrics,
            opportunity_score=score,
            entry_window_minutes=int(med_ttr),
            sigma_table={},
            resolution_station=station,
            ranked_at=now_iso,
            trend=self._compute_trend(city, score),
        )

    def _compute_trend(self, city: str, current_score: float) -> str:
        """Compare current score to previous run in crowding.db."""
        try:
            db_path = "data/crowding.db"
            if not os.path.exists(db_path):
                return "stable"
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT opportunity_score FROM crowding_history "
                "WHERE city = ? ORDER BY ranked_at DESC LIMIT 1",
                (city,),
            ).fetchone()
            conn.close()
            if row:
                prev = float(row[0])
                if current_score - prev >= 5:
                    return "improving"
                if prev - current_score >= 5:
                    return "degrading"
        except Exception:
            pass
        return "stable"

    def _write_db(self, reports: list[CityCrowdingReport]) -> None:
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/crowding.db")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crowding_history (
                city TEXT,
                period_days INTEGER,
                markets_analysed INTEGER,
                opportunity_score REAL,
                entry_window_minutes INTEGER,
                resolution_station TEXT,
                metrics_json TEXT,
                trend TEXT,
                ranked_at TEXT
            )
        """)
        for r in reports:
            conn.execute(
                "INSERT INTO crowding_history VALUES (?,?,?,?,?,?,?,?,?)",
                (r.city, r.period_days, r.markets_analysed, r.opportunity_score,
                 r.entry_window_minutes, r.resolution_station,
                 json.dumps(r.metrics), r.trend, r.ranked_at),
            )
        conn.commit()
        conn.close()

    def _write_config_overrides(self, reports: list[CityCrowdingReport]) -> None:
        overrides = []
        for r in reports:
            overrides.append({
                "name": r.city,
                "opportunity_score": round(r.opportunity_score, 1),
                "entry_window_minutes": r.entry_window_minutes,
                "sigma_table": {str(k): round(v, 2) for k, v in r.sigma_table.items()},
                "resolution_station": r.resolution_station,
                "active": r.opportunity_score >= 50.0,
                "metrics": {k: round(v, 1) for k, v in r.metrics.items()},
                "trend": r.trend,
                "ranked_at": r.ranked_at,
            })
        os.makedirs("data", exist_ok=True)
        with open("data/config_city_overrides.json", "w") as f:
            json.dump(overrides, f, indent=2)

    @staticmethod
    def _report_to_dict(r: CityCrowdingReport) -> dict:
        return {
            "city": r.city,
            "period_days": r.period_days,
            "markets_analysed": r.markets_analysed,
            "metrics": r.metrics,
            "opportunity_score": r.opportunity_score,
            "entry_window_minutes": r.entry_window_minutes,
            "resolution_station": r.resolution_station,
            "trend": r.trend,
        }
