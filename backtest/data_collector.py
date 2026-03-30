"""
Backtest data collector.
Pulls resolved markets from Gamma API, past forecasts from Open-Meteo
Previous Runs API, historical CLOB prices, and ground truth temps from IEM.
Supports checkpoint/resume for long backtest runs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from config import Config
from connectors.open_meteo import (
    fetch_archive_tmax_c,
    fetch_previous_run_tmax,
)
from connectors.polymarket_gamma import (
    fetch_price_history,
    fetch_resolved_weather_markets,
    parse_market_buckets,
)
from connectors.station_obs import get_actual_high_temp_c
from strategies.resolution_parser import (
    STATION_COORDS,
    parse_resolution_source,
)

logger = logging.getLogger(__name__)

CHECKPOINT_PATH = "data/backtest_checkpoint.json"

# Patterns to extract city and date from market question strings
CITY_DATE_PATTERNS = [
    re.compile(
        r"(?:highest|high|max).*?temperature.*?in\s+(.+?)\s+on\s+(\w+\s+\d{1,2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"temperature.*?in\s+(.+?)\s+on\s+(\w+\s+\d{1,2}(?:,?\s*\d{4})?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(.+?)\s+(?:highest|high|max).*?temperature.*?(\w+\s+\d{1,2})",
        re.IGNORECASE,
    ),
]

CITY_ALIASES: dict[str, str] = {
    "nyc": "New York",
    "new york city": "New York",
    "buenos aires": "Buenos Aires",
    "ba": "Buenos Aires",
    "london": "London",
    "seoul": "Seoul",
    "chicago": "Chicago",
    "ankara": "Ankara",
    "miami": "Miami",
    "mumbai": "Mumbai",
    "são paulo": "São Paulo",
    "sao paulo": "São Paulo",
}


def parse_city_date(question: str) -> tuple[str | None, str | None]:
    """
    Parse city name and date from a Polymarket market question string.
    Returns (city_name, date_str_YYYY_MM_DD) or (None, None).
    """
    for pattern in CITY_DATE_PATTERNS:
        match = pattern.search(question)
        if match:
            raw_city = match.group(1).strip().rstrip("?")
            raw_date = match.group(2).strip().rstrip("?")

            city = CITY_ALIASES.get(raw_city.lower(), raw_city)

            try:
                for fmt in ("%B %d %Y", "%B %d, %Y", "%b %d %Y", "%b %d, %Y",
                            "%B %d", "%b %d"):
                    try:
                        dt = datetime.strptime(raw_date, fmt)
                        if dt.year == 1900:
                            dt = dt.replace(year=datetime.now().year)
                        return city, dt.strftime("%Y-%m-%d")
                    except ValueError:
                        continue
            except Exception:
                pass

    return None, None


def save_checkpoint(processed: list[str], results: list[dict]) -> None:
    os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump({
            "processed_condition_ids": processed,
            "partial_results": results,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }, f)


def load_checkpoint() -> tuple[list[str], list[dict]]:
    try:
        with open(CHECKPOINT_PATH) as f:
            c = json.load(f)
        return c["processed_condition_ids"], c["partial_results"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return [], []


def collect_backtest_data(
    config: Config,
    days: int = 180,
    cities: list[str] | None = None,
    horizon: int = 24,
) -> list[dict]:
    """
    Main data collection pipeline for backtest.
    Returns list of market records with forecasts, prices, and ground truth.
    """
    city_configs = {c["name"]: c for c in config.CITIES}
    active_cities = set(cities) if cities else {c["name"] for c in config.CITIES if c.get("active")}

    processed_ids, results = load_checkpoint()
    processed_set = set(processed_ids)

    raw_markets = fetch_resolved_weather_markets(days=days)
    logger.info("Processing %d raw markets (%d already checkpointed)",
                len(raw_markets), len(processed_ids))

    for i, market in enumerate(raw_markets):
        condition_id = market.get("conditionId") or market.get("id", str(i))
        if condition_id in processed_set:
            continue

        question = market.get("question", "")
        description = market.get("description", "")
        city, date = parse_city_date(question)

        if not city or not date:
            continue
        if city not in active_cities:
            continue

        city_cfg = city_configs.get(city)
        if not city_cfg:
            continue

        resolution_src = parse_resolution_source(description)
        station_id = resolution_src.station_id
        if station_id == "UNKNOWN":
            station_id = city_cfg.get("resolution_station", "UNKNOWN")

        station_coords = STATION_COORDS.get(station_id)
        lat = station_coords[0] if station_coords else city_cfg["lat"]
        lon = station_coords[1] if station_coords else city_cfg["lon"]

        # Fetch past forecasts
        past_days = 1 if horizon == 24 else 2
        try:
            forecast = fetch_previous_run_tmax(lat, lon, date, past_days=past_days)
        except Exception as exc:
            logger.debug("Previous run fetch failed for %s %s: %s", city, date, exc)
            forecast = {}

        if not forecast:
            continue

        # Fetch ground truth
        actual_c, source = get_actual_high_temp_c(
            station_id, date, lat, lon, fetch_archive_tmax_c,
        )

        # Fetch price history for each bucket
        buckets = parse_market_buckets(market)
        bucket_prices: dict[str, list[tuple[int, float]]] = {}
        try:
            resolution_dt = datetime.fromisoformat(date) if "T" not in date else datetime.fromisoformat(date)
            resolution_ts = int(resolution_dt.replace(tzinfo=timezone.utc).timestamp())
        except (ValueError, TypeError):
            continue

        entry_ts = resolution_ts - (horizon * 3600)
        for bucket in buckets:
            tid = bucket.get("token_id", "")
            if tid:
                ph = fetch_price_history(tid, entry_ts, entry_ts + 3600, fidelity=60)
                bucket_prices[bucket["label"]] = ph
                time.sleep(0.2)

        # Determine resolved outcome
        resolved_bucket = None
        for bucket in buckets:
            if bucket.get("winner"):
                resolved_bucket = bucket["label"]
                break

        record = {
            "condition_id": condition_id,
            "city": city,
            "date": date,
            "question": question,
            "station_id": station_id,
            "lat": lat,
            "lon": lon,
            "forecast": forecast,
            "actual_high_c": actual_c,
            "actual_source": source,
            "buckets": buckets,
            "bucket_prices": bucket_prices,
            "resolved_bucket": resolved_bucket,
            "city_config": city_cfg,
        }
        results.append(record)
        processed_ids.append(condition_id)
        processed_set.add(condition_id)

        if len(results) % 20 == 0:
            save_checkpoint(processed_ids, results)
            logger.info("Checkpoint saved: %d markets processed", len(results))

        time.sleep(0.3)

    save_checkpoint(processed_ids, results)
    logger.info("Data collection complete: %d market records", len(results))
    return results


def coverage_check(
    config: Config,
    cities: list[str] | None = None,
    days: int = 180,
) -> None:
    """Print IEM station coverage stats per city."""
    from connectors.station_obs import fetch_daily_max_temp_f

    active_cities = cities or [c["name"] for c in config.CITIES if c.get("active")]

    for city_cfg in config.CITIES:
        if city_cfg["name"] not in active_cities:
            continue

        station = city_cfg.get("resolution_station", "UNKNOWN")
        iem_count = 0
        om_count = 0
        total = 0

        start = datetime.now() - timedelta(days=days)
        for d in range(days):
            date = (start + timedelta(days=d)).strftime("%Y-%m-%d")
            total += 1
            temp_f = fetch_daily_max_temp_f(station, date)
            if temp_f is not None:
                iem_count += 1
            else:
                temp_c = fetch_archive_tmax_c(city_cfg["lat"], city_cfg["lon"], date)
                if temp_c is not None:
                    om_count += 1
            time.sleep(0.1)

        pct_iem = iem_count / total * 100 if total else 0
        pct_om = om_count / total * 100 if total else 0
        flag = " *** LOW COVERAGE ***" if pct_iem < 80 else ""
        print(
            f"{station:6s}  ({city_cfg['name']:20s}):  "
            f"{iem_count:3d}/{total} days from IEM ({pct_iem:5.1f}%), "
            f"{om_count:3d} from Open-Meteo ({pct_om:5.1f}%){flag}"
        )
