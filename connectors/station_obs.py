"""
Iowa Environmental Mesonet (IEM) ASOS archive connector.
Provides station-level daily max temperature for backtest ground truth.
Source: mesonet.agron.iastate.edu — free, no API key, global coverage.
"""
from __future__ import annotations

import csv
import io
import logging

import httpx

logger = logging.getLogger(__name__)

IEM_DAILY_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
IEM_HOURLY_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

STATION_NETWORK: dict[str, str | None] = {
    "KLGA": "NY_ASOS",
    "KORD": "IL_ASOS",
    "KMIA": "FL_ASOS",
    "EGLC": None,
    "SAEZ": None,
    "RKSS": None,
    "LTAC": None,
    "VABB": None,
    "SBGR": None,
}


def fetch_daily_max_temp_f(station_id: str, date: str) -> float | None:
    """
    Return the observed daily high temperature in degF for a given ICAO station + date.
    date format: "YYYY-MM-DD"

    For US stations: uses IEM daily summary.
    For international stations: scans all hourly tmpf observations, returns the max.
    Returns None if data unavailable.
    """
    network = STATION_NETWORK.get(station_id)
    if network:
        return _fetch_daily_us(station_id, network, date)
    return _fetch_daily_international(station_id, date)


def _fetch_daily_us(station_id: str, network: str, date: str) -> float | None:
    """IEM daily.py — official daily max for US ASOS stations."""
    try:
        resp = httpx.get(
            IEM_DAILY_URL,
            params={
                "sts": date,
                "ets": date,
                "network": network,
                "stations": station_id,
                "var": "max_temp_f",
                "format": "csv",
            },
            timeout=15,
        )
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        if rows and rows[0].get("max_temp_f") not in ("M", "", None):
            return float(rows[0]["max_temp_f"])
    except Exception as exc:
        logger.debug("IEM daily fetch failed for %s %s: %s", station_id, date, exc)
    return None


def _fetch_daily_international(station_id: str, date: str) -> float | None:
    """
    Pull all hourly tmpf observations for a full UTC calendar day and return the max.
    """
    try:
        resp = httpx.get(
            IEM_HOURLY_URL,
            params={
                "data": "tmpf",
                "station": station_id,
                "sts": f"{date}T00:00:00Z",
                "ets": f"{date}T23:59:59Z",
                "tz": "UTC",
                "format": "onlycomma",
                "latlon": "no",
            },
            timeout=20,
        )
        temps: list[float] = []
        for line in resp.text.splitlines():
            parts = line.split(",")
            if len(parts) >= 3 and parts[2] not in ("M", "tmpf", "", "T"):
                try:
                    temps.append(float(parts[2]))
                except ValueError:
                    pass
        return max(temps) if temps else None
    except Exception as exc:
        logger.debug("IEM hourly fetch failed for %s %s: %s", station_id, date, exc)
    return None


def get_actual_high_temp_c(
    station_id: str,
    date: str,
    lat: float,
    lon: float,
    open_meteo_fallback_fn,
) -> tuple[float | None, str]:
    """
    Canonical entry point called by backtest/data_collector.py.
    Returns (temp_celsius, source) where source = "iem" | "open_meteo" | "unavailable".
    """
    temp_f = fetch_daily_max_temp_f(station_id, date)
    if temp_f is not None:
        return round((temp_f - 32) * 5 / 9, 2), "iem"

    temp_c = open_meteo_fallback_fn(lat, lon, date)
    if temp_c is not None:
        return round(temp_c, 2), "open_meteo"

    return None, "unavailable"
