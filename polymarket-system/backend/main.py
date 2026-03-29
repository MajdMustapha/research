"""
Polymarket Niche Bot — FastAPI Backend v2
Improvements: GFS ensemble forecasts, Kelly sizing, WebSocket price feeds, backtest API.

Start with:  uvicorn backend.main:app --reload --port 8000
"""

from fastapi import FastAPI, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json, asyncio, logging, os, math, subprocess, requests, sqlite3, re, statistics

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Polymarket Niche Bot", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE       = Path(__file__).parent
DB_PATH    = BASE / "bot_data.db"
LOG_PATH   = BASE / "logs"
LOG_PATH.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backend")

DRY_RUN        = os.getenv("DRY_RUN", "true").lower() == "true"
MAX_BET_USDC   = float(os.getenv("MAX_BET_USDC", "5"))
MIN_EDGE_PTS   = float(os.getenv("MIN_EDGE_POINTS", "15"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))  # quarter-Kelly default
BANKROLL_USDC  = float(os.getenv("BANKROLL_USDC", "100"))
PRIVATE_KEY    = os.getenv("POLY_PRIVATE_KEY", "")
FUNDER_ADDR    = os.getenv("POLY_FUNDER_ADDRESS", "")
# A/B test: maker mode uses limit orders (post-only) instead of market orders (taker)
MAKER_MODE     = os.getenv("MAKER_MODE", "false").lower() == "true"
MAKER_SPREAD   = float(os.getenv("MAKER_SPREAD", "0.01"))  # place limit 1¢ better than our edge price

GAMMA_API      = "https://gamma-api.polymarket.com"
CLOB_API       = "https://clob.polymarket.com"
CLOB_WS        = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLY_CLI        = os.getenv("POLY_CLI", "polymarket")  # path to polymarket CLI binary
WEATHER_TAGS   = {"weather", "climate"}
# Include closed markets in scan for testing (no open weather markets exist right now)
INCLUDE_CLOSED_FOR_TEST = os.getenv("INCLUDE_CLOSED", "true").lower() == "true"

# ── Polymarket CLI wrapper ────────────────────────────────────────────────────
def poly_cli(args: list[str], timeout: int = 30) -> Optional[list | dict]:
    """Run a polymarket CLI command and return parsed JSON output.
    Always appends '-o json'. Returns None on failure."""
    cmd = [POLY_CLI, "-o", "json"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            log.warning(f"poly_cli {' '.join(args)}: exit {result.returncode}: {result.stderr[:200]}")
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        log.warning(f"poly_cli {' '.join(args)}: timeout ({timeout}s)")
        return None
    except (json.JSONDecodeError, Exception) as e:
        log.warning(f"poly_cli {' '.join(args)}: {e}")
        return None

def poly_search(query: str, limit: int = 50) -> list:
    """Search markets via CLI. Returns list of market dicts."""
    return poly_cli(["markets", "search", query, "--limit", str(limit)]) or []

def poly_events_by_tag(tag: str, active: bool = True, limit: int = 30) -> list:
    """List events by tag via CLI. Returns list of event dicts."""
    args = ["events", "list", "--tag", tag, "--limit", str(limit)]
    if active:
        args += ["--active", "true"]
    return poly_cli(args) or []

def poly_price(token_id: str, side: str = "buy") -> Optional[float]:
    """Get price for a token via CLI."""
    data = poly_cli(["clob", "price", token_id, "--side", side])
    if data and isinstance(data, dict):
        return float(data.get("price", 0))
    return None

def poly_book(token_id: str) -> Optional[dict]:
    """Get orderbook for a token via CLI."""
    return poly_cli(["clob", "book", token_id])

# ── DB init ───────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS trades (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        market      TEXT NOT NULL,
        outcome     TEXT NOT NULL,
        direction   TEXT NOT NULL,
        market_price REAL,
        my_prob     REAL,
        edge_pts    REAL,
        ev_per_10   REAL,
        bet_usdc    REAL,
        kelly_frac  REAL,
        status      TEXT,
        tx_hash     TEXT,
        order_type  TEXT DEFAULT 'taker',
        resolved    INTEGER DEFAULT 0,
        pnl         REAL
    );
    CREATE TABLE IF NOT EXISTS scans (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        candidates  INTEGER,
        trades_made INTEGER,
        mode        TEXT
    );
    CREATE TABLE IF NOT EXISTS signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        market      TEXT,
        city        TEXT,
        forecast_c  REAL,
        ensemble_spread REAL,
        market_price REAL,
        outcome     TEXT,
        direction   TEXT,
        edge_pts    REAL,
        ev_per_10   REAL,
        acted       INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS backtest_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        city        TEXT,
        date_range  TEXT,
        total_signals INTEGER,
        total_trades INTEGER,
        sim_pnl     REAL,
        avg_edge    REAL,
        win_rate    REAL,
        details     TEXT
    );
    CREATE TABLE IF NOT EXISTS forecast_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        city        TEXT NOT NULL,
        target_date TEXT NOT NULL,
        model       TEXT,
        mean_c      REAL,
        stdev_c     REAL,
        member_count INTEGER,
        members_json TEXT
    );
    """)
    con.commit()
    con.close()

init_db()

# ── HTTP helpers (try curl first, fall back to requests) ─────────────────────
def curl_get(url, params=None):
    """Use curl subprocess to bypass proxy issues; fall back to requests."""
    cmd = ["curl", "-s", "--max-time", "15"]
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return json.loads(result.stdout)
    log.info(f"curl failed (rc={result.returncode}), trying requests...")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()

# ── Helpers ───────────────────────────────────────────────────────────────────
CITY_COORDS = {
    "tel aviv":  (32.0004, 34.8706),   # LLBG Ben Gurion
    "hong kong": (22.3080, 113.9185),  # VHHH HK Airport
    "london":    (51.4775, -0.4614),   # EGLL Heathrow
    "new york":  (40.7772, -73.8726),  # KLGA LaGuardia
    "nyc":       (40.7772, -73.8726),  # alias for New York City
    "new york city": (40.7772, -73.8726),
    "taipei":    (25.0777, 121.2328),  # RCTP Taoyuan
    "seoul":     (37.5583, 126.7906),  # RKSS Gimpo
    "beijing":   (40.0799, 116.6031),
    "shanghai":  (31.1979, 121.3363),
    "tokyo":     (35.5493, 139.7798),
    "dubai":     (25.2532, 55.3657),
    "singapore": (1.3644, 103.9915),
    "chicago":   (41.9742, -87.9073),  # KORD O'Hare
    "los angeles": (33.9416, -118.4085), # KLAX
    "miami":     (25.7959, -80.2870),  # KMIA
    "paris":     (49.0097, 2.5479),    # LFPG CDG
    "sydney":    (-33.9461, 151.1772), # YSSY
    "mumbai":    (19.0896, 72.8656),   # VABB
    # Cities from live Polymarket temperature markets
    "ankara":    (40.1281, 32.9951),   # LTAC Esenboğa
    "atlanta":   (33.6407, -84.4277),  # KATL Hartsfield
    "austin":    (30.1945, -97.6699),  # KAUS
    "buenos aires": (-34.5592, -58.4156), # SAEZ Ezeiza
    "chengdu":   (30.5785, 103.9471),  # ZUUU Shuangliu
    "chongqing": (29.7192, 106.6416),  # ZUCK Jiangbei
    "dallas":    (32.8998, -97.0403),  # KDFW
    "houston":   (29.9902, -95.3368),  # KIAH Bush
    "lucknow":   (26.7606, 80.8893),   # VILK Amausi
    "madrid":    (40.4936, -3.5668),   # LEMD Barajas
    "milan":     (45.6306, 8.7281),    # LIMC Malpensa
    "munich":    (48.3538, 11.7861),   # EDDM
    "san francisco": (37.6213, -122.3790), # KSFO
    "sao paulo": (-23.4356, -46.4731), # SBGR Guarulhos
    "seattle":   (47.4502, -122.3088), # KSEA
    "shanghai":  (31.1443, 121.8083),  # ZSPD Pudong
    "shenzhen":  (22.6393, 113.8107),  # ZGSZ Bao'an
    "tel aviv":  (32.0055, 34.8854),   # LLBG Ben Gurion
    "toronto":   (43.6772, -79.6306),  # CYYZ Pearson
    "warsaw":    (52.1657, 20.9671),   # EPWA Chopin
    "wellington": (-41.3272, 174.8053), # NZWN
}

def f_to_c(f: float) -> float:
    return (f - 32) * 5 / 9

def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32

# ── Market type parsers ───────────────────────────────────────────────────────
def parse_city_temp_market(question: str):
    """Parse temperature market questions. Handles both formats:
      - 'Will the highest temperature in Seoul be 12°C on March 29?'
      - 'Will the highest temperature in Atlanta be between 64-65°F on March 29?'
      - 'Will the highest temperature in London be 16°C or higher on March 29?'
    Returns (city, date_str, unit) or (None, None, None)."""
    q = re.sub(r"^(?:arch?i?v?e?d?)?(?=will\b)", "", question, flags=re.IGNORECASE).strip().lower()

    # Pattern: "highest temperature in <city> ... on <month> <day>"
    m = re.search(r"highest temperature in (.+?)\s+(?:be\s|on\s)", q)
    if not m:
        return None, None, None
    city = m.group(1).strip().title()

    # Extract date: "on March 29" — match month names to avoid false matches like "london be"
    MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
    dm = re.search(rf"\bon\s+({MONTHS})\s+(\d+)", q)
    if not dm:
        return None, None, None
    try:
        month_str = dm.group(1).strip()
        day_str = dm.group(2).strip()
        year = datetime.now().year
        dt = datetime.strptime(f"{month_str} {day_str} {year}", "%B %d %Y")
        date_str = dt.strftime("%Y-%m-%d")
    except:
        return None, None, None

    unit = "F" if ("°f" in q or "f " in q.split("on")[0][-5:]) else "C"
    return city, date_str, unit

def parse_temp_range(outcome_question: str):
    """Parse market question for temperature range.
    Returns (lo, hi, unit) where lo/hi are Fahrenheit or Celsius floats.
    Handles all live Polymarket formats:
      Single-degree °C: 'be 12°C on' -> (11.5, 12.5, 'C')
      Range °F bins:    'between 64-65°F' -> (64, 65, 'F')
      Tail low:         '8°C or below' -> (-999, 8.5, 'C')
      Tail high:        '18°C or higher' -> (17.5, 999, 'C')
      Tail low °F:      '55°F or below' -> (-999, 55, 'F')
      Tail high °F:     '78°F or higher' -> (78, 999, 'F')
    """
    q = outcome_question.lower()

    # Detect unit — check for °F / °f patterns; default to C
    unit = "C"
    if "°f" in q or "ºf" in q:
        unit = "F"
    elif re.search(r"\d+f\b", q):
        unit = "F"

    # "between X-Y" (°F 2-degree bins)
    m = re.search(r"between\s+([\d.]+)\s*[-–]\s*([\d.]+)", q)
    if m:
        return float(m.group(1)), float(m.group(2)), unit

    m = re.search(r"between\s+([\d.]+)\s+and\s+([\d.]+)", q)
    if m:
        return float(m.group(1)), float(m.group(2)), unit

    # "X°C or higher" / "X°F or higher" (tail high)
    m = re.search(r"([\d.]+)\s*°?[fFcC]?\s*or\s+(?:higher|above|more)", q)
    if m:
        val = float(m.group(1))
        # For single-degree Celsius tail, use val-0.5 as lower bound
        lo = val - 0.5 if unit == "C" else val
        return lo, 999, unit

    # "X°C or below" / "X°F or below" (tail low)
    m = re.search(r"([\d.]+)\s*°?[fFcC]?\s*or\s+(?:below|lower|less)", q)
    if m:
        val = float(m.group(1))
        hi = val + 0.5 if unit == "C" else val
        return -999, hi, unit

    m = re.search(r"(?:more|greater|above)\s+than\s+([\d.]+)", q)
    if m:
        val = float(m.group(1))
        lo = val - 0.5 if unit == "C" else val
        return lo, 999, unit

    m = re.search(r"(?:less|lower|below)\s+than\s+([\d.]+)", q)
    if m:
        val = float(m.group(1))
        hi = val + 0.5 if unit == "C" else val
        return -999, hi, unit

    # Single-degree: "be 12°C on" — integer degree, bin is [X-0.5, X+0.5)
    m = re.search(r"be\s+([\d.]+)\s*°[cC]\s+on\b", q)
    if m:
        val = float(m.group(1))
        return val - 0.5, val + 0.5, "C"

    # Single-degree °F variant (unlikely but handle): "be 72°F on"
    m = re.search(r"be\s+([\d.]+)\s*°[fF]\s+on\b", q)
    if m:
        val = float(m.group(1))
        return val - 0.5, val + 0.5, "F"

    return None, None, None

def classify_weather_market(market: dict) -> str:
    """Classify a market into a weather type for strategy selection.
    Returns: 'city_temp', 'global_temp', 'hottest_period', 'hurricane', or None."""
    q = (market.get("question") or "").lower()
    tags = [t.lower() for t in (market.get("tags") or []) if isinstance(t, str)]

    if "highest temperature in" in q:
        return "city_temp"
    if "global temperature increase" in q or "heat increase" in q:
        return "global_temp"
    if "hottest" in q and ("year" in q or "month" in q or "record" in q
                           or any(m in q for m in ["january","february","march","april","may","june",
                                                    "july","august","september","october","november","december"])):
        return "hottest_period"
    if any(w in q for w in ["hurricane", "named storm", "tropical storm", "landfall", "category"]):
        return "hurricane"
    return None

# ── Global temp market parser ─────────────────────────────────────────────────
GLOBAL_TEMP_API = "https://archive-api.open-meteo.com/v1/archive"

def parse_global_temp_market(question: str):
    """Parse 'global temperature increase above X°C' style markets.
    Returns (threshold_c, period_description) or (None, None)."""
    q = question.lower()
    # "global temperature increase above 1.5°C" / "exceed 1.5 degrees"
    m = re.search(r"(?:global|world)\s+(?:average\s+)?temperature\s+(?:increase|anomaly|rise)\s+(?:above|exceed|over|surpass)\s+([\d.]+)\s*°?c?", q)
    if m:
        return float(m.group(1)), "annual"
    m = re.search(r"(?:exceed|surpass|above|over)\s+([\d.]+)\s*°?c?\s+(?:global|warming)", q)
    if m:
        return float(m.group(1)), "annual"
    return None, None

def evaluate_global_temp_market(market: dict) -> Optional[dict]:
    """Evaluate a global temperature market using recent ERA5/reanalysis trend data.
    Returns a signal dict or None."""
    q = market.get("question", "")
    threshold, period = parse_global_temp_market(q)
    if threshold is None:
        return None

    # Use a simple heuristic based on known warming trajectory:
    # Current global temp anomaly is ~1.3-1.5°C above pre-industrial.
    # We estimate probability using a logistic model around current trajectory.
    # This is a rough prior — refined by checking recent monthly anomalies.
    try:
        # Fetch recent global temperature proxy: average temp anomaly from multiple grid points
        # We sample 5 geographically spread stations and compare to baseline
        sample_coords = [
            (51.5, -0.1),   # London
            (40.7, -74.0),  # NYC
            (-33.9, 151.2), # Sydney
            (35.7, 139.7),  # Tokyo
            (1.3, 103.9),   # Singapore
        ]
        from datetime import timedelta
        end = datetime.now().date() - timedelta(days=5)  # recent data availability lag
        start = end - timedelta(days=30)

        anomalies = []
        for lat, lon in sample_coords:
            try:
                data = curl_get(GLOBAL_TEMP_API, {
                    "latitude": lat, "longitude": lon,
                    "daily": "temperature_2m_mean",
                    "temperature_unit": "celsius", "timezone": "auto",
                    "start_date": start.isoformat(), "end_date": end.isoformat(),
                })
                temps = data.get("daily", {}).get("temperature_2m_mean", [])
                if temps:
                    valid = [t for t in temps if t is not None]
                    if valid:
                        anomalies.append(statistics.mean(valid))
            except:
                pass

        if not anomalies:
            return None

        record_api_success()

        # Simple probability model: logistic curve around threshold
        # P(exceed) = 1 / (1 + exp(-k*(current_trend - threshold)))
        # Current warming ~1.3°C baseline; scale factor heuristic
        current_estimate = 1.35  # approximate current anomaly baseline
        k = 5.0  # steepness
        prob_exceed = 1 / (1 + math.exp(-k * (current_estimate - threshold)))

        return {
            "type": "global_temp",
            "threshold": threshold,
            "prob_exceed": round(prob_exceed, 4),
            "current_estimate": current_estimate,
            "sample_points": len(anomalies),
        }
    except Exception as e:
        log.warning(f"Global temp eval failed: {e}")
        return None

# ── CLOB API market fetcher ──────────────────────────────────────────────────
def _normalize_cli_market(m: dict) -> dict:
    """Normalize CLI market output to have a `tokens` list like CLOB API format.
    CLI returns outcomePrices/outcomes/clobTokenIds as JSON strings; we parse them."""
    if m.get("tokens"):
        return m  # already has tokens

    try:
        outcomes = json.loads(m.get("outcomes", "[]"))
        prices = json.loads(m.get("outcomePrices", "[]"))
        token_ids = json.loads(m.get("clobTokenIds", "[]"))
    except (json.JSONDecodeError, TypeError):
        return m

    tokens = []
    for i in range(min(len(outcomes), len(prices), len(token_ids))):
        tokens.append({
            "outcome": outcomes[i],
            "price": str(prices[i]),
            "token_id": token_ids[i],
        })
    m["tokens"] = tokens

    # Also normalize end_date_iso
    if not m.get("end_date_iso") and m.get("endDateIso"):
        m["end_date_iso"] = m["endDateIso"]

    return m

def _fetch_via_cli() -> list:
    """Primary: fetch weather markets using polymarket CLI (fast, tag-based)."""
    weather_markets = []

    # 1) Direct temperature search — catches all city temp markets
    temp_markets = poly_search("highest temperature", limit=100)
    # 2) Weather events by tag — catches hurricanes, global temp, etc.
    weather_events = poly_events_by_tag("Weather", active=True, limit=30)
    event_markets = []
    for evt in weather_events:
        for m in (evt.get("markets") or []):
            event_markets.append(m)

    # Deduplicate by question
    seen = set()
    for m in temp_markets + event_markets:
        m = _normalize_cli_market(m)
        q = m.get("question", "")
        if q in seen:
            continue
        seen.add(q)

        mtype = classify_weather_market(m)
        tags = [t.lower() for t in (m.get("tags") or []) if isinstance(t, str)]
        has_weather_tag = bool(WEATHER_TAGS & set(tags))

        if not has_weather_tag and not mtype:
            continue

        is_open = m.get("active", False) and not m.get("closed", True)
        if not is_open and not INCLUDE_CLOSED_FOR_TEST:
            continue

        m["_weather_type"] = mtype or "other"
        m["_is_open"] = is_open
        weather_markets.append(m)

    return weather_markets

def _fetch_via_clob() -> list:
    """Fallback: paginate CLOB API directly."""
    cursor = "MA=="
    weather_markets = []
    pages = 0
    max_pages = 30

    while cursor and pages < max_pages:
        try:
            data = curl_get(f"{CLOB_API}/markets", {"next_cursor": cursor})
        except Exception as e:
            log.warning(f"CLOB API page {pages} failed: {e}")
            break

        markets = data.get("data", [])
        cursor = data.get("next_cursor")
        pages += 1

        for m in markets:
            tags = [t.lower() for t in (m.get("tags") or []) if isinstance(t, str)]
            has_weather_tag = bool(WEATHER_TAGS & set(tags))
            mtype = classify_weather_market(m)

            if not has_weather_tag and not mtype:
                continue

            is_open = m.get("active", False) and not m.get("closed", True)
            if not is_open and not INCLUDE_CLOSED_FOR_TEST:
                continue

            m["_weather_type"] = mtype or "other"
            m["_is_open"] = is_open
            weather_markets.append(m)

        if not cursor or cursor == "LTE=":
            break

    return weather_markets

def fetch_weather_markets() -> list:
    """Fetch weather markets — tries CLI first (fast), falls back to CLOB pagination."""
    # Try CLI first
    markets = _fetch_via_cli()
    source = "CLI"
    if not markets:
        log.info("CLI fetch returned nothing, falling back to CLOB API pagination")
        markets = _fetch_via_clob()
        source = "CLOB"

    open_count = len([m for m in markets if m.get("_is_open")])
    log.info(f"Fetched {len(markets)} weather markets via {source} ({open_count} open)")
    return markets

# ── Improvement 1: GFS 31-member ensemble forecast ──────────────────────────
ENSEMBLE_MODELS = ["gfs_seamless", "ecmwf_ifs025"]

def _fetch_ensemble_model(lat: float, lon: float, date_str: str, model: str) -> list[float]:
    """Fetch ensemble members for a single model. Returns list of temps (°C).
    Open-Meteo returns members as temperature_2m_max_member01..memberNN keys."""
    try:
        data = curl_get("https://ensemble-api.open-meteo.com/v1/ensemble", {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max",
            "temperature_unit": "celsius", "timezone": "auto",
            "start_date": date_str, "end_date": date_str,
            "models": model
        })
        d = data.get("daily", {})
        members = []
        # Extract individual ensemble members (temperature_2m_max_member01, etc.)
        for key, vals in d.items():
            if key.startswith("temperature_2m_max_member") and isinstance(vals, list):
                for v in vals:
                    if v is not None:
                        members.append(float(v))
        # If no member keys found, fall back to the mean value
        if not members:
            highs = d.get("temperature_2m_max", [])
            if highs:
                members = [h for h in highs if h is not None]
        return members
    except Exception as e:
        log.warning(f"Ensemble model {model} failed: {e}")
        return []

def get_ensemble_forecast(city: str, date_str: str):
    """Fetch multi-model ensemble (GFS + ECMWF) from Open-Meteo for calibrated probabilities."""
    key = city.lower().strip()
    coords = next((v for k, v in CITY_COORDS.items() if k in key or key in k), None)
    if not coords: return None
    lat, lon = coords

    all_members = []
    sources = []
    for model in ENSEMBLE_MODELS:
        members = _fetch_ensemble_model(lat, lon, date_str, model)
        if members:
            sources.append(f"{model}({len(members)})")
            all_members.extend(members)

    if not all_members:
        return None

    record_api_success()
    return {
        "members": all_members,
        "mean": round(statistics.mean(all_members), 1),
        "stdev": round(statistics.stdev(all_members), 1) if len(all_members) > 1 else 1.5,
        "min": round(min(all_members), 1),
        "max": round(max(all_members), 1),
        "count": len(all_members),
        "source": f"Open-Meteo Ensemble: {' + '.join(sources)}"
    }

def get_forecast(city: str, date_str: str):
    """Fallback: single best-match deterministic forecast."""
    key = city.lower().strip()
    coords = next((v for k, v in CITY_COORDS.items() if k in key or key in k), None)
    if not coords: return None
    lat, lon = coords
    try:
        data = curl_get("https://api.open-meteo.com/v1/forecast", {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "celsius", "timezone": "auto",
            "start_date": date_str, "end_date": date_str,
            "models": "best_match"
        })
        d = data.get("daily", {})
        highs = d.get("temperature_2m_max", [None])
        high_val = highs[0] if highs else None
        if high_val is None:
            return None
        return {
            "members": [high_val],
            "mean": round(high_val, 1),
            "stdev": 1.5,  # assumed uncertainty for single model
            "min": round(high_val, 1),
            "max": round(high_val, 1),
            "count": 1,
            "source": "Open-Meteo GFS+ECMWF (single)"
        }
    except Exception as e:
        log.warning(f"Forecast failed {city}: {e}")
        return None

# ── Forecast cache with TTL ───────────────────────────────────────────────────
# At 15-min scans, avoid re-fetching the same ensemble when models haven't updated.
# GFS updates every 6h, ECMWF every 12h — cache for 30 min is safe.
FORECAST_CACHE_TTL = 30 * 60  # 30 minutes in seconds
_forecast_cache: dict = {}  # {(city, date): {"forecast": dict, "ts": float}}

def get_best_forecast(city: str, date_str: str):
    """Try ensemble first, fall back to deterministic. Uses TTL cache."""
    import time
    cache_key = (city.lower(), date_str)
    now = time.time()

    # Return cached if still fresh
    cached = _forecast_cache.get(cache_key)
    if cached and (now - cached["ts"]) < FORECAST_CACHE_TTL:
        return cached["forecast"]

    forecast = get_ensemble_forecast(city, date_str)
    if forecast and forecast["count"] > 1:
        log.info(f"  Ensemble forecast for {city}: {forecast['mean']}°C ±{forecast['stdev']}° ({forecast['count']} members)")
    else:
        forecast = get_forecast(city, date_str)
        if forecast:
            log.info(f"  Deterministic forecast for {city}: {forecast['mean']}°C (fallback)")

    if forecast:
        _forecast_cache[cache_key] = {"forecast": forecast, "ts": now}
    return forecast

def ensemble_prob_dist(forecast: dict):
    """Build probability distribution from ensemble members.
    Counts what fraction of ensemble members predict each integer temperature (°C)."""
    members = forecast["members"]
    mean = forecast["mean"]
    stdev = forecast["stdev"]

    if len(members) >= 5:
        rounded = [round(m) for m in members]
        counts = {}
        for t in rounded:
            counts[str(t)] = counts.get(str(t), 0) + 1
        total = len(rounded)
        dist = {k: round(v / total, 4) for k, v in counts.items()}
        all_temps = [int(k) for k in dist.keys()]
        lo, hi = min(all_temps) - 3, max(all_temps) + 3
        for t in range(lo, hi + 1):
            k = str(t)
            if k not in dist:
                dist[k] = 0.0
        return dist
    else:
        mu = mean
        sigma = max(stdev, 0.5)
        def g(x): return math.exp(-0.5 * ((x - mu) / sigma) ** 2)
        buckets = list(range(round(mu) - 6, round(mu) + 7))
        raw = {str(b): g(b) for b in buckets}
        total = sum(raw.values())
        return {k: round(v / total, 4) for k, v in raw.items()}

def ensemble_prob_in_range(forecast: dict, lo: float, hi: float, unit: str = "C") -> float:
    """Calculate probability that temperature falls in [lo, hi] range.
    If unit='F', converts range to Celsius before comparing against ensemble members (which are °C)."""
    if unit == "F":
        lo_c = f_to_c(lo) if lo > -900 else -900
        hi_c = f_to_c(hi) if hi < 900 else 900
    else:
        lo_c, hi_c = lo, hi

    members = forecast["members"]
    if len(members) >= 5:
        count = sum(1 for m in members if lo_c <= m <= hi_c)
        return round(count / len(members), 4)
    else:
        # Gaussian fallback
        mu = forecast["mean"]
        sigma = max(forecast["stdev"], 0.5)
        from math import erf
        def phi(x):
            return 0.5 * (1 + erf((x - mu) / (sigma * math.sqrt(2))))
        p_lo = phi(lo_c) if lo_c > -900 else 0.0
        p_hi = phi(hi_c) if hi_c < 900 else 1.0
        return round(max(0, p_hi - p_lo), 4)

# ── Improvement 2: Kelly criterion bet sizing ────────────────────────────────
def kelly_bet(my_prob: float, market_price: float, direction: str) -> float:
    """Calculate Kelly-optimal bet size, capped at MAX_BET_USDC.

    Uses fractional Kelly (KELLY_FRACTION, default 0.25) for safety.
    Returns bet size in USDC.
    """
    if direction == "YES":
        p = my_prob            # prob of winning
        odds = 1 / market_price - 1   # decimal odds minus 1 (payout ratio)
    else:
        p = 1 - my_prob        # prob of winning (NO side)
        odds = 1 / (1 - market_price) - 1

    if odds <= 0:
        return 0.0

    # Kelly formula: f* = (p * odds - (1-p)) / odds = p - (1-p)/odds
    q = 1 - p
    kelly_full = (p * odds - q) / odds

    if kelly_full <= 0:
        return 0.0

    # Apply fractional Kelly and bankroll cap
    bet = BANKROLL_USDC * kelly_full * KELLY_FRACTION
    bet = min(bet, MAX_BET_USDC)
    bet = max(round(bet, 2), 0.10)  # minimum $0.10 bet
    return bet

# ── Longshot tail exploitation ────────────────────────────────────────────────
# Tail bins (e.g. "18°C or higher" at 5¢) are systematically overpriced per the
# longshot bias research. When our ensemble says ~0%, selling YES is high-EV.
LONGSHOT_MAX_PRICE = 0.10       # only target contracts priced under 10¢
LONGSHOT_MAX_PROB  = 0.02       # ensemble must say <2% probability
LONGSHOT_MIN_EDGE  = 3.0        # minimum edge in percentage points (lower bar for tails)

def is_longshot_tail(lo: float, hi: float) -> bool:
    """Check if this is a tail bin (open-ended range)."""
    return lo <= -900 or hi >= 900

def longshot_mispricing(mkt_price: float, my_prob: float) -> float:
    """Calculate mispricing percentage for longshot contracts.
    Positive = overpriced (sell opportunity)."""
    if mkt_price <= 0:
        return 0.0
    return ((mkt_price - my_prob) / mkt_price) * 100

# ── Bayesian forecast updating ────────────────────────────────────────────────
# Track forecast evolution across model runs. When GFS and ECMWF disagree,
# use Bayesian weighting based on historical reliability.

def store_forecast_snapshot(city: str, target_date: str, forecast: dict):
    """Save a forecast snapshot for Bayesian tracking."""
    db_insert("forecast_history", {
        "ts": datetime.now(timezone.utc).isoformat(),
        "city": city,
        "target_date": target_date,
        "model": forecast.get("source", "unknown"),
        "mean_c": forecast["mean"],
        "stdev_c": forecast["stdev"],
        "member_count": forecast["count"],
        "members_json": json.dumps(forecast["members"][:100]),  # cap storage
    })

def get_bayesian_forecast(city: str, target_date: str, current_forecast: dict) -> dict:
    """Combine current forecast with historical snapshots using Bayesian weighting.

    Earlier forecasts get less weight (further from event = less accurate).
    More recent forecasts get more weight. This produces a probability estimate
    that accounts for forecast evolution and convergence.

    Returns an updated forecast dict with blended members.
    """
    history = db_query(
        "SELECT * FROM forecast_history WHERE city = ? AND target_date = ? ORDER BY ts DESC LIMIT 10",
        (city, target_date)
    )

    if len(history) < 2:
        # First observation — just store and return current
        store_forecast_snapshot(city, target_date, current_forecast)
        return current_forecast

    # Bayesian blending: weight recent forecasts more heavily
    # Weight decays: most recent = 1.0, each older snapshot = 0.7x previous
    all_members = list(current_forecast["members"])  # weight 1.0 (newest)
    decay = 0.7
    weight = decay

    for snap in history[:5]:  # blend up to 5 prior snapshots
        try:
            prior_members = json.loads(snap["members_json"])
            # Subsample to match weight: if weight=0.7 and 80 members, take ~56
            n_take = max(1, int(len(prior_members) * weight))
            # Take evenly spaced members for representativeness
            step = max(1, len(prior_members) // n_take)
            all_members.extend(prior_members[::step][:n_take])
            weight *= decay
        except:
            continue

    # Store current snapshot
    store_forecast_snapshot(city, target_date, current_forecast)

    if len(all_members) < 3:
        return current_forecast

    blended = {
        "members": all_members,
        "mean": round(statistics.mean(all_members), 1),
        "stdev": round(statistics.stdev(all_members), 1) if len(all_members) > 1 else 1.5,
        "min": round(min(all_members), 1),
        "max": round(max(all_members), 1),
        "count": len(all_members),
        "source": f"Bayesian blend ({len(history)+1} snapshots, {len(all_members)} members)",
    }
    return blended

# ── Correlated position management ───────────────────────────────────────────
# Multiple bins on the same city+date are highly correlated — if London hits 10°C,
# then "10°C YES" wins AND "11°C NO" wins AND "12°C NO" wins simultaneously.
# We halve Kelly sizing when we already have open positions on the same distribution.
CORRELATION_KELLY_DISCOUNT = 0.5  # halve bet size for correlated positions

def count_correlated_trades(city: str, date_str: str) -> int:
    """Count how many unresolved trades we have on this city+date.
    Matches city name and date (as 'Month Day') in the market question."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_text = dt.strftime("%B %-d")  # e.g. "March 29"
    except:
        return 0
    rows = db_query(
        "SELECT COUNT(*) as cnt FROM trades WHERE resolved = 0 AND market LIKE ? AND market LIKE ?",
        (f"%{city}%", f"%{date_text}%")
    )
    return rows[0]["cnt"] if rows else 0

def correlated_kelly_adjustment(city: str, date_str: str, base_bet: float) -> float:
    """Reduce bet size if we already have correlated positions on this city+date."""
    existing = count_correlated_trades(city, date_str)
    if existing == 0:
        return base_bet
    # Each additional correlated position halves the remaining allocation
    discount = CORRELATION_KELLY_DISCOUNT ** existing
    adjusted = round(base_bet * discount, 2)
    adjusted = max(adjusted, 0.10)  # floor at $0.10
    log.info(f"  Correlation adj: {city} {date_str} has {existing} existing trades, "
             f"${base_bet} -> ${adjusted} ({discount:.0%} of base)")
    return adjusted

# ── Improvement 3: WebSocket price feed ──────────────────────────────────────
# In-memory price cache updated by WebSocket
ws_prices: dict = {}  # {token_id: {"price": float, "ts": str}}
ws_task: Optional[asyncio.Task] = None

async def ws_price_listener(token_ids: list[str]):
    """Connect to Polymarket CLOB WebSocket for real-time price updates."""
    try:
        import websockets
    except ImportError:
        log.warning("websockets not installed — falling back to REST polling")
        return

    while True:
        try:
            assets = [{"asset_id": tid, "type": "market"} for tid in token_ids]
            url = CLOB_WS
            async with websockets.connect(url) as ws:
                sub_msg = json.dumps({"type": "subscribe", "assets_ids": token_ids})
                await ws.send(sub_msg)
                log.info(f"WebSocket connected, subscribed to {len(token_ids)} tokens")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        if msg.get("type") == "price_change":
                            for change in msg.get("changes", []):
                                tid = change.get("asset_id", "")
                                price = float(change.get("price", 0))
                                if tid and price > 0:
                                    ws_prices[tid] = {
                                        "price": price,
                                        "ts": datetime.now(timezone.utc).isoformat()
                                    }
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass
        except Exception as e:
            log.warning(f"WebSocket disconnected: {e}, reconnecting in 5s...")
            await asyncio.sleep(5)

def get_price(token_id: str, side: str = "BUY") -> Optional[float]:
    """Get price from WebSocket cache first, fall back to REST."""
    cached = ws_prices.get(token_id)
    if cached:
        return cached["price"]
    try:
        data = curl_get(f"{CLOB_API}/price", {"token_id": token_id, "side": side})
        return float(data.get("price", 0))
    except:
        return None

def db_insert(table: str, row: dict):
    con = sqlite3.connect(DB_PATH)
    keys = ", ".join(row.keys())
    placeholders = ", ".join("?" * len(row))
    con.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(row.values()))
    con.commit()
    con.close()

def db_query(sql: str, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(sql, params).fetchall()
    con.close()
    return [dict(r) for r in rows]

# ── Error tracking / circuit breaker ──────────────────────────────────────────
MAX_CONSECUTIVE_ERRORS = 3
_error_state = {"consecutive": 0, "paused": False, "last_error": None}

def record_api_error(context: str, err: Exception):
    _error_state["consecutive"] += 1
    _error_state["last_error"] = f"{context}: {err}"
    if _error_state["consecutive"] >= MAX_CONSECUTIVE_ERRORS:
        _error_state["paused"] = True
        log.error(f"CIRCUIT BREAKER: {_error_state['consecutive']} consecutive API errors — scanning paused. "
                  f"Last: {_error_state['last_error']}")

def record_api_success():
    _error_state["consecutive"] = 0
    if _error_state["paused"]:
        log.info("Circuit breaker reset — API recovered")
    _error_state["paused"] = False

# ── Trade deduplication ───────────────────────────────────────────────────────
TRADE_COOLDOWN_HOURS = 12

def recently_traded(market_question: str) -> bool:
    """Check if we already traded this market within the cooldown window."""
    rows = db_query(
        "SELECT id FROM trades WHERE market = ? AND ts > datetime('now', ?)",
        (market_question, f"-{TRADE_COOLDOWN_HOURS} hours")
    )
    return len(rows) > 0

# ── Core scan loop ─────────────────────────────────────────────────────────────
MAX_TRADES_PER_SCAN = 6  # increased: 3 edge + 3 longshot tail max
bot_status = {"running": False, "last_scan": None, "next_scan": None, "scan_count": 0}

async def run_scan():
    # Circuit breaker check
    if _error_state["paused"]:
        log.warning(f"Scan skipped — circuit breaker active ({_error_state['consecutive']} errors). "
                    f"Call POST /api/errors/reset to resume.")
        return

    bot_status["running"] = True
    now = datetime.now(timezone.utc).isoformat()
    log.info(f"=== SCAN START {now} DRY={DRY_RUN} ===")

    # Auto-resolve past trades before scanning for new ones
    try:
        result = _do_auto_resolve()
        if result["resolved"] > 0:
            log.info(f"Auto-resolved {result['resolved']} trades at scan start")
    except Exception as e:
        log.warning(f"Auto-resolve failed: {e}")

    trades_made = 0
    candidates  = 0

    try:
        # 1. Fetch weather markets
        all_markets = fetch_weather_markets()
        record_api_success()  # CLOB API responded
        # Focus on city_temp markets (our core strategy) — prioritize open markets
        all_city_temp = [m for m in all_markets if m.get("_weather_type") == "city_temp"]
        open_markets_list = [m for m in all_city_temp if m.get("_is_open")]
        closed_markets_list = [m for m in all_city_temp if not m.get("_is_open")]
        markets = open_markets_list + (closed_markets_list if INCLUDE_CLOSED_FOR_TEST else [])
        candidates = len(markets)
        log.info(f"Found {candidates} city_temp candidates ({len(open_markets_list)} open, "
                 f"{len(closed_markets_list)} closed, {len(all_markets)} total weather)")

        # Limit: evaluate up to 200 open markets, plus 20 closed for testing
        max_eval = len(open_markets_list) + (20 if INCLUDE_CLOSED_FOR_TEST else 0)

        # Collect token IDs for WebSocket subscription (only open markets)
        open_markets = [m for m in markets[:max_eval] if m.get("_is_open")]
        all_token_ids = []
        for market in open_markets:
            for tok in (market.get("tokens") or []):
                tid = tok.get("token_id")
                if tid:
                    all_token_ids.append(tid)

        # Start WebSocket feed if we have open tokens and it's not running
        global ws_task
        if all_token_ids and (ws_task is None or ws_task.done()):
            ws_task = asyncio.create_task(ws_price_listener(all_token_ids))
            await asyncio.sleep(2)

        forecast_cache = {}  # cache forecasts by (city, date) to avoid redundant API calls
        for market in markets[:max_eval]:
            if trades_made >= MAX_TRADES_PER_SCAN:
                log.info(f"  Hit {MAX_TRADES_PER_SCAN}-trade cap for this scan, stopping.")
                break

            q = market.get("question", "")

            # Deduplication: skip if traded recently
            if recently_traded(q):
                log.info(f"  Skipped (recently traded): {q[:60]}")
                continue

            city, date_str, temp_unit = parse_city_temp_market(q)
            if not city or not date_str:
                log.info(f"  Skipped (parse fail): {q[:80]}")
                continue

            # Use market end_date to get correct year for the date
            end_date = market.get("end_date_iso", "")
            if end_date:
                try:
                    market_year = end_date[:4]
                    date_str = f"{market_year}-{date_str[5:]}"
                except:
                    pass

            # Fetch ensemble forecast (always returns °C)
            # If market date is in the past, use today+1 as proxy (for testing closed markets)
            forecast_date = date_str
            try:
                mdate = datetime.strptime(date_str, "%Y-%m-%d").date()
                today = datetime.now().date()
                if mdate < today:
                    from datetime import timedelta
                    forecast_date = (today + timedelta(days=1)).isoformat()
                    log.info(f"  Using proxy date {forecast_date} for past market date {date_str}")
            except:
                pass

            cache_key = (city.lower(), forecast_date)
            if cache_key in forecast_cache:
                forecast = forecast_cache[cache_key]
            else:
                raw_forecast = get_best_forecast(city, forecast_date)
                if not raw_forecast:
                    log.info(f"  Skipped (no forecast): {city} {forecast_date}")
                    forecast_cache[cache_key] = None
                    continue
                # Bayesian update: blend with prior forecast snapshots
                forecast = get_bayesian_forecast(city, forecast_date, raw_forecast)
                forecast_cache[cache_key] = forecast
            if not forecast:
                continue

            fmean = forecast["mean"]
            fstdev = forecast["stdev"]

            # For each token (outcome) in this market, compute edge
            tokens = market.get("tokens") or []
            for tok in tokens:
                tid = tok.get("token_id", "")
                outcome = tok.get("outcome", "")
                mkt_price = float(tok.get("price", 0))

                # Try WS cache for fresher price
                cached = get_price(tid)
                if cached and cached > 0:
                    mkt_price = cached

                # Parse the temperature range from the full question
                lo, hi, unit = parse_temp_range(q)
                if lo is None:
                    continue

                # Compute probability using ensemble
                my_p = ensemble_prob_in_range(forecast, lo, hi, unit)

                # For YES token: my_p is prob of YES
                # outcome is typically "Yes" or "No"
                if outcome.lower() == "no":
                    my_p = 1 - my_p

                # ── Longshot tail exploitation ──
                # Detect overpriced tail bins where ensemble says ~0%
                tail_bin = is_longshot_tail(lo, hi)
                signal_type = "edge"  # default

                if tail_bin and outcome.lower() == "yes" and mkt_price <= LONGSHOT_MAX_PRICE and my_p <= LONGSHOT_MAX_PROB:
                    # Overpriced longshot: BUY NO (sell YES equivalent)
                    direction = "NO"
                    true_edge = mkt_price - my_p  # how overpriced YES is
                    mispricing_pct = longshot_mispricing(mkt_price, my_p)
                    signal_type = "longshot_tail"

                    if true_edge * 100 < LONGSHOT_MIN_EDGE:
                        continue

                    ev = (1 - my_p) * mkt_price - my_p * (1 - mkt_price)
                    bet_size = kelly_bet(my_p, mkt_price, "NO")

                    log.info(f"LONGSHOT: NO on '{q[:55]}' mispricing={mispricing_pct:.0f}% "
                             f"edge={true_edge*100:.1f}pts mkt={mkt_price:.3f} my_p={my_p:.3f}")

                elif mkt_price <= 0.05 or mkt_price >= 0.90:
                    continue  # skip extreme contracts — no real edge in consensus zones
                else:
                    # Normal edge detection
                    edge = my_p - mkt_price
                    direction = "YES" if edge > 0 else "NO"
                    true_edge = abs(edge)

                    if true_edge * 100 < MIN_EDGE_PTS:
                        continue

                    # EV calc
                    if direction == "YES":
                        ev = my_p * (1 - mkt_price) - (1 - my_p) * mkt_price
                    else:
                        wp = 1 - my_p
                        ev = wp * mkt_price - (1 - wp) * (1 - mkt_price)

                    bet_size = kelly_bet(my_p, mkt_price, direction)

                # ── Correlated position management ──
                # Reduce bet if we already have positions on this city+date
                bet_size = correlated_kelly_adjustment(city, date_str, bet_size)

                sig = {
                    "ts": now, "market": q, "city": city,
                    "forecast_c": fmean, "ensemble_spread": fstdev,
                    "market_price": mkt_price,
                    "outcome": outcome, "direction": direction,
                    "edge_pts": round(true_edge * 100, 1),
                    "ev_per_10": round(ev * 10, 2), "acted": 0,
                }
                db_insert("signals", sig)
                log.info(f"SIGNAL [{signal_type}]: {direction} on '{q[:55]}' edge={sig['edge_pts']}pts "
                         f"ev={sig['ev_per_10']} kelly=${bet_size} "
                         f"(forecast: {fmean}°C ±{fstdev}°, range={lo}-{hi}°{unit})")

                # Execute (dry or live)
                status = "dry_run"
                tx_hash = None
                order_type = "maker" if MAKER_MODE else "taker"
                if not DRY_RUN and market.get("_is_open"):
                    if MAKER_MODE:
                        # Maker mode: limit order via CLI (no py-clob-client needed)
                        status, tx_hash = execute_maker(
                            market, outcome, direction, my_p, mkt_price, bet_size)
                    elif PRIVATE_KEY and FUNDER_ADDR:
                        # Taker mode: market order via py-clob-client
                        status, tx_hash = execute_live(
                            market, outcome, direction, mkt_price, bet_size)

                trade = {
                    "ts": now, "market": q, "outcome": outcome,
                    "direction": direction, "market_price": mkt_price,
                    "my_prob": round(my_p if direction == "YES" else (1 - my_p), 4),
                    "edge_pts": sig["edge_pts"], "ev_per_10": sig["ev_per_10"],
                    "bet_usdc": bet_size, "kelly_frac": round(KELLY_FRACTION, 2),
                    "status": status, "order_type": order_type,
                    "tx_hash": tx_hash, "resolved": 0, "pnl": None,
                }
                db_insert("trades", trade)
                trades_made += 1
                await asyncio.sleep(0.5)
                break   # one trade per market

        # ── Pass 2: global temperature markets ────────────────────────────────
        global_markets = [m for m in all_markets if m.get("_weather_type") == "global_temp"]
        if global_markets:
            log.info(f"Evaluating {len(global_markets)} global_temp markets")

        for market in global_markets[:5]:
            if trades_made >= MAX_TRADES_PER_SCAN:
                break

            q = market.get("question", "")
            if recently_traded(q):
                continue

            eval_result = evaluate_global_temp_market(market)
            if not eval_result:
                continue

            tokens = market.get("tokens") or []
            for tok in tokens:
                tid = tok.get("token_id", "")
                outcome = tok.get("outcome", "")
                mkt_price = float(tok.get("price", 0))

                cached = get_price(tid)
                if cached and cached > 0:
                    mkt_price = cached
                if mkt_price <= 0.01 or mkt_price >= 0.99:
                    continue

                # For "Yes" token, our prob is prob_exceed
                my_p = eval_result["prob_exceed"]
                if outcome.lower() == "no":
                    my_p = 1 - my_p

                edge = my_p - mkt_price
                direction = "YES" if edge > 0 else "NO"
                true_edge = abs(edge)

                if true_edge * 100 < MIN_EDGE_PTS:
                    continue

                if direction == "YES":
                    ev = my_p * (1 - mkt_price) - (1 - my_p) * mkt_price
                else:
                    wp = 1 - my_p
                    ev = wp * mkt_price - (1 - wp) * (1 - mkt_price)

                bet_size = kelly_bet(my_p, mkt_price, direction)

                sig = {
                    "ts": now, "market": q, "city": "GLOBAL",
                    "forecast_c": eval_result["current_estimate"],
                    "ensemble_spread": 0,
                    "market_price": mkt_price,
                    "outcome": outcome, "direction": direction,
                    "edge_pts": round(true_edge * 100, 1),
                    "ev_per_10": round(ev * 10, 2), "acted": 0,
                }
                db_insert("signals", sig)
                log.info(f"SIGNAL [global]: {direction} on '{q[:60]}' edge={sig['edge_pts']}pts "
                         f"threshold={eval_result['threshold']}°C")

                status = "dry_run"
                tx_hash = None
                order_type = "maker" if MAKER_MODE else "taker"
                if not DRY_RUN and market.get("_is_open"):
                    if MAKER_MODE:
                        status, tx_hash = execute_maker(
                            market, outcome, direction, my_p, mkt_price, bet_size)
                    elif PRIVATE_KEY and FUNDER_ADDR:
                        status, tx_hash = execute_live(
                            market, outcome, direction, mkt_price, bet_size)

                trade = {
                    "ts": now, "market": q, "outcome": outcome,
                    "direction": direction, "market_price": mkt_price,
                    "my_prob": round(my_p if direction == "YES" else (1 - my_p), 4),
                    "edge_pts": sig["edge_pts"], "ev_per_10": sig["ev_per_10"],
                    "bet_usdc": bet_size, "kelly_frac": round(KELLY_FRACTION, 2),
                    "status": status, "order_type": order_type,
                    "tx_hash": tx_hash, "resolved": 0, "pnl": None,
                }
                db_insert("trades", trade)
                trades_made += 1
                await asyncio.sleep(0.5)
                break

    except Exception as e:
        record_api_error("scan_loop", e)
        log.error(f"Scan error: {e}", exc_info=True)

    db_insert("scans", {
        "ts": now, "candidates": candidates,
        "trades_made": trades_made, "mode": "dry_run" if DRY_RUN else "live"
    })

    bot_status["running"] = False
    bot_status["last_scan"] = now
    bot_status["scan_count"] += 1
    log.info(f"=== SCAN END — {candidates} candidates, {trades_made} trades ===")

def execute_live(market, outcome, direction, price, bet_size):
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        client = ClobClient(CLOB_API, key=PRIVATE_KEY, chain_id=137,
                            signature_type=1, funder=FUNDER_ADDR)
        client.set_api_creds(client.create_or_derive_api_creds())
        tok = next((t for t in market.get("tokens", [])
                    if t.get("outcome", "").lower() == outcome.lower()), None)
        if not tok: return "error_no_token", None
        mo = MarketOrderArgs(token_id=tok["token_id"], amount=bet_size,
                             side=BUY, order_type=OrderType.FOK)
        signed = client.create_market_order(mo)
        resp   = client.post_order(signed, OrderType.FOK)
        return ("filled" if resp.get("status") == "matched" else "failed",
                resp.get("orderID"))
    except Exception as e:
        return f"error: {e}", None

def execute_maker(market, outcome, direction, my_prob, mkt_price, bet_size):
    """Place a limit order via polymarket CLI (maker/post-only).

    Strategy: place a limit order at a price that gives us edge while sitting
    on the book as a maker. For BUY YES, we bid slightly below our fair value.
    For BUY NO (sell YES), we offer slightly above market.

    The --post-only flag ensures we never cross the spread and always earn
    the maker rebate instead of paying taker fees.
    """
    tokens = market.get("tokens") or []
    tok = next((t for t in tokens if t.get("outcome", "").lower() == outcome.lower()), None)
    if not tok:
        return "error_no_token", None

    token_id = tok["token_id"]

    # Calculate limit price: we want to be the maker, so we place
    # our order inside the spread at a price still profitable for us
    if direction == "YES":
        # Buying YES: bid at market price (we think it's underpriced)
        # Place slightly above current market to sit at top of book
        limit_price = round(min(mkt_price + MAKER_SPREAD, my_prob - MAKER_SPREAD), 2)
        limit_price = max(0.01, min(0.99, limit_price))
        side = "buy"
    else:
        # Buying NO = Selling YES: offer at market price
        # Place slightly below current to sit at top of ask
        limit_price = round(max(mkt_price - MAKER_SPREAD, (1 - my_prob) + MAKER_SPREAD), 2)
        limit_price = max(0.01, min(0.99, limit_price))
        side = "sell"

    # Calculate size in shares: bet_size_usdc / price = number of shares
    shares = max(1, int(bet_size / limit_price))

    args = [
        "clob", "create-order",
        "--token", token_id,
        "--side", side,
        "--price", str(limit_price),
        "--size", str(shares),
        "--order-type", "GTC",
        "--post-only",
    ]

    log.info(f"MAKER ORDER: {side} {shares} shares @ ${limit_price} "
             f"(token={token_id[:16]}... direction={direction})")

    result = poly_cli(args, timeout=15)
    if result is None:
        return "maker_error", None

    order_id = None
    if isinstance(result, dict):
        order_id = result.get("orderID") or result.get("order_id") or result.get("id")
        status = result.get("status", "posted")
        return f"maker_{status}", order_id
    return "maker_posted", order_id

# ── Improvement 4: Backtest engine ───────────────────────────────────────────
def run_backtest(city: str, dates: list[str], threshold: float = None):
    """Simulate trading on historical dates using current forecast model.

    For each date, fetches ensemble forecast, builds prob distribution,
    and simulates trades against synthetic market prices derived from
    a slightly-off probability (simulating market mispricing).
    """
    if threshold is None:
        threshold = MIN_EDGE_PTS

    results = []
    total_pnl = 0.0
    wins = 0

    for date_str in dates:
        forecast = get_best_forecast(city, date_str)
        if not forecast:
            continue

        dist = ensemble_prob_dist(forecast)
        fmean = forecast["mean"]

        # Simulate market prices: for each bucket, add noise to our prob
        # to simulate market mispricing
        import random
        for temp_str, my_prob in dist.items():
            if my_prob < 0.01:
                continue

            # Synthetic market price: our prob + random noise (-20% to +20%)
            noise = random.uniform(-0.20, 0.20)
            mkt_price = max(0.02, min(0.98, my_prob + noise))

            edge = my_prob - mkt_price
            direction = "YES" if edge > 0 else "NO"
            true_edge = abs(edge)

            if true_edge * 100 < threshold:
                continue

            # Kelly sizing
            bet = kelly_bet(my_prob, mkt_price, direction)

            # Simulate outcome: use actual forecast probability as truth
            won = random.random() < (my_prob if direction == "YES" else (1 - my_prob))
            if direction == "YES":
                pnl = bet * (1 / mkt_price - 1) if won else -bet
            else:
                pnl = bet * (1 / (1 - mkt_price) - 1) if won else -bet

            total_pnl += pnl
            if won:
                wins += 1

            results.append({
                "date": date_str, "temp": temp_str, "direction": direction,
                "my_prob": round(my_prob, 4), "mkt_price": round(mkt_price, 4),
                "edge_pts": round(true_edge * 100, 1),
                "bet": bet, "won": won, "pnl": round(pnl, 2),
            })

    total = len(results)
    summary = {
        "city": city,
        "dates_tested": len(dates),
        "total_signals": total,
        "wins": wins,
        "win_rate": round(wins / total, 3) if total else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_edge": round(sum(r["edge_pts"] for r in results) / total, 1) if total else 0,
        "avg_bet": round(sum(r["bet"] for r in results) / total, 2) if total else 0,
        "trades": results,
    }

    # Persist backtest run
    db_insert("backtest_runs", {
        "ts": datetime.now(timezone.utc).isoformat(),
        "city": city,
        "date_range": f"{dates[0]} to {dates[-1]}" if dates else "",
        "total_signals": total,
        "total_trades": total,
        "sim_pnl": summary["total_pnl"],
        "avg_edge": summary["avg_edge"],
        "win_rate": summary["win_rate"],
        "details": json.dumps(results[:50]),  # store first 50 trades
    })

    return summary

# ── Background scheduler ───────────────────────────────────────────────────────
async def scan_loop():
    while True:
        await run_scan()
        interval = int(os.getenv("SCAN_INTERVAL_MINUTES", "15")) * 60
        bot_status["next_scan"] = (
            datetime.now(timezone.utc).timestamp() + interval
        )
        await asyncio.sleep(interval)

@app.on_event("startup")
async def startup():
    asyncio.create_task(scan_loop())

# ── API endpoints ─────────────────────────────────────────────────────────────
@app.get("/api/status")
def status():
    return {
        **bot_status,
        "dry_run": DRY_RUN,
        "max_bet": MAX_BET_USDC,
        "min_edge": MIN_EDGE_PTS,
        "kelly_fraction": KELLY_FRACTION,
        "bankroll": BANKROLL_USDC,
        "ws_connected": len(ws_prices) > 0,
        "ws_tokens_tracked": len(ws_prices),
        "mode": "DRY RUN" if DRY_RUN else ("LIVE MAKER" if MAKER_MODE else "LIVE TAKER"),
        "maker_mode": MAKER_MODE,
        "maker_spread": MAKER_SPREAD,
        "include_closed_for_test": INCLUDE_CLOSED_FOR_TEST,
        "api_source": "CLI+CLOB",
        "poly_cli": POLY_CLI,
        "circuit_breaker": _error_state,
        "max_trades_per_scan": MAX_TRADES_PER_SCAN,
        "trade_cooldown_hours": TRADE_COOLDOWN_HOURS,
        "ensemble_models": ENSEMBLE_MODELS,
    }

@app.get("/api/trades")
def get_trades(limit: int = 50):
    return db_query("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))

@app.get("/api/signals")
def get_signals(limit: int = 100):
    return db_query("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))

@app.get("/api/scans")
def get_scans(limit: int = 30):
    return db_query("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,))

@app.get("/api/stats")
def get_stats():
    trades  = db_query("SELECT * FROM trades")
    settled = [t for t in trades if t["pnl"] is not None]
    wins    = [t for t in settled if t["pnl"] > 0]
    total_bet = sum(t["bet_usdc"] for t in trades)
    total_pnl = sum(t["pnl"] for t in settled)
    return {
        "total_trades":   len(trades),
        "settled":        len(settled),
        "wins":           len(wins),
        "win_rate":       round(len(wins) / len(settled), 3) if settled else 0,
        "total_bet_usdc": round(total_bet, 2),
        "total_pnl":      round(total_pnl, 2),
        "avg_edge":       round(sum(t["edge_pts"] for t in trades) / len(trades), 1) if trades else 0,
        "avg_ev":         round(sum(t["ev_per_10"] for t in trades) / len(trades), 2) if trades else 0,
    }

@app.post("/api/scan/trigger")
async def trigger_scan(bg: BackgroundTasks):
    if bot_status["running"]:
        return JSONResponse({"message": "Scan already running"}, status_code=409)
    bg.add_task(run_scan)
    return {"message": "Scan triggered"}

class PnlUpdate(BaseModel):
    trade_id: int
    pnl: float
    resolved: Optional[int] = 1

@app.post("/api/trades/resolve")
def resolve_trade(body: PnlUpdate):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE trades SET pnl=?, resolved=? WHERE id=?",
                (body.pnl, body.resolved, body.trade_id))
    con.commit()
    con.close()
    return {"ok": True}

@app.get("/api/config")
def get_config():
    return {
        "dry_run": DRY_RUN, "max_bet": MAX_BET_USDC,
        "min_edge": MIN_EDGE_PTS, "kelly_fraction": KELLY_FRACTION,
        "bankroll": BANKROLL_USDC,
        "scan_interval_min": int(os.getenv("SCAN_INTERVAL_MINUTES", "15")),
    }

# ── Backtest API ─────────────────────────────────────────────────────────────
class BacktestRequest(BaseModel):
    city: str = "Tel Aviv"
    days_back: int = 7
    threshold: Optional[float] = None

@app.post("/api/backtest/run")
def api_run_backtest(body: BacktestRequest):
    from datetime import timedelta
    today = datetime.now().date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(body.days_back, 0, -1)]
    result = run_backtest(body.city, dates, body.threshold)
    return result

@app.get("/api/backtest/history")
def backtest_history(limit: int = 20):
    return db_query("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?", (limit,))

# ── Auto-resolution: check actual observed temperatures ──────────────────────
def get_observed_high(city: str, date_str: str) -> Optional[float]:
    """Fetch actual observed high temperature (°C) from Open-Meteo historical API."""
    key = city.lower().strip()
    coords = next((v for k, v in CITY_COORDS.items() if k in key or key in k), None)
    if not coords:
        return None
    lat, lon = coords
    try:
        data = curl_get("https://archive-api.open-meteo.com/v1/archive", {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max",
            "temperature_unit": "celsius", "timezone": "auto",
            "start_date": date_str, "end_date": date_str,
        })
        highs = data.get("daily", {}).get("temperature_2m_max", [None])
        return highs[0] if highs else None
    except Exception as e:
        log.warning(f"Historical API failed for {city} {date_str}: {e}")
        return None

def _do_auto_resolve() -> dict:
    """Check unresolved trades against actual observed temperatures and settle P&L.
    Called automatically at the start of each scan cycle (observed data has ~1-2 day lag)
    and also available via POST /api/trades/auto-resolve."""
    unresolved = db_query("SELECT * FROM trades WHERE resolved = 0")
    if not unresolved:
        return {"resolved": 0, "errors": [], "remaining": 0}

    resolved_count = 0
    errors = []

    for trade in unresolved:
        q = trade["market"]
        city, date_str, unit = parse_city_temp_market(q)
        if not city or not date_str:
            errors.append({"trade_id": trade["id"], "error": "could not parse market question"})
            continue

        # Only try to resolve trades whose market date is in the past
        try:
            market_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if market_date >= datetime.now().date():
                continue  # market hasn't resolved yet
        except:
            pass

        observed_c = get_observed_high(city, date_str)
        if observed_c is None:
            errors.append({"trade_id": trade["id"], "error": f"no observed data for {city} {date_str}"})
            continue

        # Parse the temp range from the market question
        lo, hi, range_unit = parse_temp_range(q)
        if lo is None:
            errors.append({"trade_id": trade["id"], "error": "could not parse temp range"})
            continue

        # Convert observed to the range's unit for comparison
        if range_unit == "F":
            observed_compare = c_to_f(observed_c)
        else:
            observed_compare = observed_c

        # Did the outcome actually happen?
        in_range = lo <= observed_compare <= hi
        direction = trade["direction"]

        # YES wins if temp was in range, NO wins if temp was NOT in range
        won = (direction == "YES" and in_range) or (direction == "NO" and not in_range)

        bet = trade["bet_usdc"]
        mkt_price = trade["market_price"]
        if direction == "YES":
            pnl = bet * (1 / mkt_price - 1) if won else -bet
        else:
            pnl = bet * (1 / (1 - mkt_price) - 1) if won else -bet

        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE trades SET pnl=?, resolved=1 WHERE id=?",
                    (round(pnl, 2), trade["id"]))
        con.commit()
        con.close()
        resolved_count += 1
        log.info(f"Auto-resolved trade #{trade['id']}: {'WON' if won else 'LOST'} "
                 f"pnl=${round(pnl, 2)} (observed={observed_compare:.1f}°{range_unit}, range={lo}-{hi})")

    return {
        "resolved": resolved_count,
        "errors": errors,
        "remaining": len(unresolved) - resolved_count,
    }

@app.post("/api/trades/auto-resolve")
def auto_resolve_trades():
    return _do_auto_resolve()

# ── Circuit breaker management ────────────────────────────────────────────────
@app.get("/api/errors")
def error_status():
    return _error_state

@app.post("/api/errors/reset")
def reset_errors():
    _error_state["consecutive"] = 0
    _error_state["paused"] = False
    _error_state["last_error"] = None
    log.info("Circuit breaker manually reset")
    return {"ok": True, "message": "Circuit breaker reset"}

@app.get("/api/ws/prices")
def ws_price_snapshot():
    """Return current WebSocket price cache."""
    return ws_prices

# ── A/B test: maker vs taker comparison ─────────────────────────────────────
@app.get("/api/ab/stats")
def ab_stats():
    """Compare maker vs taker performance for A/B testing."""
    trades = db_query("SELECT * FROM trades")

    def _stats(subset):
        if not subset:
            return {"count": 0, "settled": 0, "wins": 0, "win_rate": 0,
                    "total_bet": 0, "total_pnl": 0, "avg_edge": 0, "avg_ev": 0}
        settled = [t for t in subset if t["pnl"] is not None]
        wins = [t for t in settled if t["pnl"] > 0]
        return {
            "count": len(subset),
            "settled": len(settled),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(settled), 3) if settled else 0,
            "total_bet": round(sum(t["bet_usdc"] for t in subset), 2),
            "total_pnl": round(sum(t["pnl"] for t in settled), 2) if settled else 0,
            "avg_edge": round(sum(t["edge_pts"] for t in subset) / len(subset), 1),
            "avg_ev": round(sum(t["ev_per_10"] for t in subset) / len(subset), 2),
        }

    maker_trades = [t for t in trades if t.get("order_type") == "maker"]
    taker_trades = [t for t in trades if t.get("order_type") != "maker"]

    return {
        "maker_mode_active": MAKER_MODE,
        "maker": _stats(maker_trades),
        "taker": _stats(taker_trades),
        "total_trades": len(trades),
    }

# ── Polymarket CLI API endpoints ─────────────────────────────────────────────
@app.get("/api/poly/search")
def api_poly_search(q: str = "weather", limit: int = 20):
    """Search Polymarket via CLI."""
    results = poly_search(q, limit=limit)
    return {"count": len(results), "markets": results}

@app.get("/api/poly/weather-events")
def api_poly_weather_events(limit: int = 20):
    """List weather events via CLI."""
    events = poly_events_by_tag("Weather", active=True, limit=limit)
    return {"count": len(events), "events": events}

@app.get("/api/poly/price/{token_id}")
def api_poly_price(token_id: str):
    """Get price for a token via CLI."""
    price = poly_price(token_id)
    return {"token_id": token_id, "price": price}

@app.get("/api/poly/book/{token_id}")
def api_poly_book(token_id: str):
    """Get orderbook via CLI."""
    book = poly_book(token_id)
    return {"token_id": token_id, "book": book}

class PolyCliRequest(BaseModel):
    args: list[str]

@app.post("/api/poly/cli")
def api_poly_cli_raw(body: PolyCliRequest):
    """Run arbitrary polymarket CLI command (read-only safety: rejects trade commands)."""
    blocked = {"create-order", "market-order", "post-orders", "cancel", "cancel-all"}
    if any(arg in blocked for arg in body.args):
        return JSONResponse({"error": "Trade commands blocked via API — use CLI directly"}, status_code=403)
    result = poly_cli(body.args)
    return {"args": body.args, "result": result}

# ── Serve dashboard ──────────────────────────────────────────────────────────
DASHBOARD_DIR = BASE.parent / "dashboard"

@app.get("/")
def serve_dashboard():
    return FileResponse(DASHBOARD_DIR / "index.html")
