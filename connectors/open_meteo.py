"""
Open-Meteo API connectors.
  - Forecast API (current predictions)
  - Previous Runs API (backtest: what GFS/ECMWF predicted N days ago)
  - Archive API (historical ground truth fallback)
  - Ensemble API (31-member GFS for probabilistic forecasts)
All free, no API key required.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


# ── Forecast (live mode) ────────────────────────────────────────────

def fetch_forecast_tmax(
    lat: float, lon: float, date: str,
    models: str = "gfs_seamless,ecmwf_ifs025,icon_seamless",
) -> dict[str, float]:
    """
    Fetch multi-model daily temperature_2m_max forecast for a single date.
    Returns {"gfs_seamless": val, "ecmwf_ifs025": val, "icon_seamless": val}.
    """
    resp = httpx.get(FORECAST_URL, params={
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max",
        "start_date": date, "end_date": date,
        "models": models,
        "timezone": "UTC",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    results: dict[str, float] = {}
    for model in models.split(","):
        model_data = data.get("daily", {})
        key = f"temperature_2m_max_{model}" if len(models.split(",")) > 1 else "temperature_2m_max"
        vals = model_data.get(key, model_data.get("temperature_2m_max"))
        if vals and vals[0] is not None:
            results[model] = vals[0]
    return results


# ── Previous Runs (backtest) ────────────────────────────────────────

def fetch_previous_run_tmax(
    lat: float, lon: float, date: str, past_days: int = 1,
    models: str = "gfs_seamless,ecmwf_ifs025,icon_seamless",
) -> dict[str, float]:
    """
    Fetch what models predicted `past_days` ago for the given date.
    past_days=1 → T-24h prediction; past_days=2 → T-48h prediction.
    """
    resp = httpx.get(PREVIOUS_RUNS_URL, params={
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max",
        "start_date": date, "end_date": date,
        "models": models,
        "past_days": past_days,
        "timezone": "UTC",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    results: dict[str, float] = {}
    daily = data.get("daily", {})
    for model in models.split(","):
        key = f"temperature_2m_max_{model}"
        vals = daily.get(key, daily.get("temperature_2m_max"))
        if vals and len(vals) > 0 and vals[0] is not None:
            results[model] = vals[0]
    return results


# ── Archive (ground truth fallback) ─────────────────────────────────

def fetch_archive_tmax_c(lat: float, lon: float, date: str) -> float | None:
    """
    Fetch observed daily max temperature from Open-Meteo historical archive.
    Returns degrees Celsius or None if unavailable.
    """
    try:
        resp = httpx.get(ARCHIVE_URL, params={
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max",
            "start_date": date, "end_date": date,
            "timezone": "UTC",
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        vals = data.get("daily", {}).get("temperature_2m_max", [])
        if vals and vals[0] is not None:
            return float(vals[0])
    except Exception as exc:
        logger.debug("Open-Meteo archive fetch failed for %s,%s %s: %s", lat, lon, date, exc)
    return None


# ── Ensemble (31-member GFS) ────────────────────────────────────────

def fetch_ensemble_probabilities(
    lat: float, lon: float, date: str,
    thresholds: list[float],
    model: str = "gfs_seamless",
) -> dict[float, float]:
    """
    Fetch 31-member GFS ensemble for a given location and date.
    Returns {threshold: probability_of_exceeding} for each threshold.
    """
    resp = httpx.get(ENSEMBLE_URL, params={
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max",
        "start_date": date, "end_date": date,
        "models": model,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    members: list[float] = []
    # Ensemble API returns temperature_2m_max as list or per-member keys
    tmax = daily.get("temperature_2m_max")
    if isinstance(tmax, dict):
        for k, v in tmax.items():
            if k.startswith("member") and v:
                members.append(v[0] if isinstance(v, list) else v)
    elif isinstance(tmax, list):
        members = [v for v in tmax if v is not None]
    else:
        # Try member-keyed format
        for key in sorted(daily.keys()):
            if "member" in key and "temperature" in key:
                vals = daily[key]
                if vals and vals[0] is not None:
                    members.append(vals[0])

    if len(members) < 3:
        return {}

    results: dict[float, float] = {}
    for t in thresholds:
        results[t] = sum(1 for m in members if m >= t) / len(members)
    return results


def ensemble_bucket_probability(
    lat: float, lon: float, date: str,
    lo: float, hi: float,
) -> float | None:
    """
    Returns P(temperature daily high in [lo, hi]) from ensemble.
    Returns None if ensemble unavailable (triggers Gaussian fallback).
    """
    thresholds = [lo] + ([] if hi == float("inf") else [hi])
    probs = fetch_ensemble_probabilities(lat, lon, date, thresholds)
    if not probs:
        return None
    p_exceed_lo = probs.get(lo, 0.0)
    p_exceed_hi = probs.get(hi, 0.0) if hi != float("inf") else 0.0
    return max(0.0, p_exceed_lo - p_exceed_hi)


def ensemble_confidence(
    lat: float, lon: float, date: str, threshold: float,
) -> float:
    """
    Agreement score: how one-sided are the ensemble members?
    Returns 0.5 (no agreement) to 1.0 (unanimous).
    """
    probs = fetch_ensemble_probabilities(lat, lon, date, [threshold])
    p = probs.get(threshold, 0.5)
    return max(p, 1 - p)
