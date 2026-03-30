"""
Resolution source parser.
Parses the exact weather station used for Polymarket market resolution
from the market description field. Critical for accurate forecasting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STATION_PATTERNS = [
    re.compile(r"\b([A-Z]{4})\s*(?:\(|station)", re.IGNORECASE),
    re.compile(
        r"wunderground\.com/history/daily/[^/]+/[^/]+/[^/]+/([A-Z]{4})",
        re.IGNORECASE,
    ),
    re.compile(r"Weather Underground.*?([A-Z]{4})", re.IGNORECASE),
]

STATION_COORDS: dict[str, tuple[float, float]] = {
    "KLGA": (40.7773, -73.8726),
    "EGLC": (51.5053, 0.0553),
    "SAEZ": (-34.8222, -58.5358),
    "RKSS": (37.5583, 126.7906),
    "KORD": (41.9742, -87.9073),
    "LTAC": (40.1281, 32.9951),
    "KMIA": (25.7959, -80.2870),
    "VABB": (19.0887, 72.8679),
    "SBGR": (-23.4356, -46.4731),
}


@dataclass
class ResolutionSource:
    station_id: str
    source_type: str  # "weather_underground" | "nws" | "noaa" | "unknown"
    raw_description: str


def parse_resolution_source(market_description: str) -> ResolutionSource:
    """
    Parse a Polymarket market's description to extract the exact weather station.
    Markets with UNKNOWN station are excluded from live trading.
    """
    for pattern in STATION_PATTERNS:
        match = pattern.search(market_description)
        if match:
            station = match.group(1).upper()
            source = (
                "weather_underground"
                if "wunderground" in market_description.lower()
                else "nws"
            )
            return ResolutionSource(
                station_id=station,
                source_type=source,
                raw_description=market_description,
            )
    return ResolutionSource(
        station_id="UNKNOWN",
        source_type="unknown",
        raw_description=market_description,
    )


def parse_bucket_range(label: str) -> tuple[float, float]:
    """
    Parse a bucket label like "29-31 C" or "29°C to 31°C" or ">= 35" into (lo, hi).
    Handles various Polymarket formatting conventions.
    """
    label = label.replace("°", "").replace("C", "").replace("F", "").strip()

    # ">= X" or "X or more" or "X+"
    ge_match = re.match(r"[>≥]=?\s*(-?\d+\.?\d*)", label)
    if ge_match:
        return float(ge_match.group(1)), float("inf")

    # "< X" or "less than X" or "under X"
    lt_match = re.match(r"[<≤]=?\s*(-?\d+\.?\d*)", label)
    if lt_match:
        return float("-inf"), float(lt_match.group(1))
    less_match = re.match(r"(?:less than|under|below)\s+(-?\d+\.?\d*)", label, re.IGNORECASE)
    if less_match:
        return float("-inf"), float(less_match.group(1))

    # "X or more"
    or_more = re.match(r"(-?\d+\.?\d*)\s*(?:or more|\+)", label)
    if or_more:
        return float(or_more.group(1)), float("inf")

    # Range: "X-Y" or "X to Y" or "X – Y"
    range_match = re.match(
        r"(-?\d+\.?\d*)\s*[-–toTO]+\s*(-?\d+\.?\d*)", label
    )
    if range_match:
        return float(range_match.group(1)), float(range_match.group(2))

    return 0.0, 0.0
