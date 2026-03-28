"""
Polymarket Niche Bot — FastAPI Backend
Runs the scan/analyse/trade loop and serves data to the React dashboard.

Start with:  uvicorn backend.main:app --reload --port 8000
"""

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json, asyncio, logging, os, math, requests, sqlite3

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Polymarket Niche Bot", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
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
PRIVATE_KEY    = os.getenv("POLY_PRIVATE_KEY", "")
FUNDER_ADDR    = os.getenv("POLY_FUNDER_ADDRESS", "")

GAMMA_API      = "https://gamma-api.polymarket.com"
CLOB_API       = "https://clob.polymarket.com"

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
        status      TEXT,
        tx_hash     TEXT,
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
        market_price REAL,
        outcome     TEXT,
        direction   TEXT,
        edge_pts    REAL,
        ev_per_10   REAL,
        acted       INTEGER DEFAULT 0
    );
    """)
    con.commit()
    con.close()

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────
CITY_COORDS = {
    "tel aviv":  (32.0004, 34.8706),   # LLBG Ben Gurion
    "hong kong": (22.3080, 113.9185),  # VHHH HK Airport
    "london":    (51.4775, -0.4614),   # EGLL Heathrow
    "new york":  (40.7772, -73.8726),  # KLGA LaGuardia
    "taipei":    (25.0777, 121.2328),  # RCTP Taoyuan
    "seoul":     (37.5583, 126.7906),  # RKSS Gimpo
    "beijing":   (40.0799, 116.6031),
    "shanghai":  (31.1979, 121.3363),
    "tokyo":     (35.5493, 139.7798),
    "dubai":     (25.2532, 55.3657),
    "singapore": (1.3644, 103.9915),
}

def parse_city_date(question: str):
    import re
    q = question.lower()
    m = re.search(r"highest temperature in (.+?) on ([a-z]+ \d+)", q)
    if not m: return None, None
    city = m.group(1).strip().title()
    try:
        year = datetime.now().year
        dt = datetime.strptime(f"{m.group(2).strip()} {year}", "%B %d %Y")
        return city, dt.strftime("%Y-%m-%d")
    except:
        return None, None

def get_forecast(city: str, date_str: str):
    key = city.lower().strip()
    coords = next((v for k, v in CITY_COORDS.items() if k in key or key in k), None)
    if not coords: return None
    lat, lon = coords
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "celsius", "timezone": "auto",
            "start_date": date_str, "end_date": date_str,
            "models": "best_match"
        }, timeout=15)
        r.raise_for_status()
        d = r.json().get("daily", {})
        highs = d.get("temperature_2m_max", [None])
        return {"high": round(highs[0]) if highs[0] else None,
                "low": round(d.get("temperature_2m_min", [None])[0] or 0),
                "source": "Open-Meteo GFS+ECMWF"}
    except Exception as e:
        log.warning(f"Forecast failed {city}: {e}")
        return None

def prob_dist(forecast_high: int, sigma=1.5):
    mu = forecast_high
    def g(x): return math.exp(-0.5 * ((x - mu) / sigma) ** 2)
    buckets = list(range(mu - 6, mu + 7))
    raw = {str(b): g(b) for b in buckets}
    total = sum(raw.values())
    return {k: round(v / total, 4) for k, v in raw.items()}

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

# ── Core scan loop ─────────────────────────────────────────────────────────────
bot_status = {"running": False, "last_scan": None, "next_scan": None, "scan_count": 0}

async def run_scan():
    bot_status["running"] = True
    now = datetime.now(timezone.utc).isoformat()
    log.info(f"=== SCAN START {now} DRY={DRY_RUN} ===")

    trades_made = 0
    candidates  = 0

    try:
        # 1. Fetch markets
        params = {"tag": "weather", "active": "true", "closed": "false", "limit": 200}
        r = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
        r.raise_for_status()
        markets = [m for m in r.json()
                   if "highest temperature" in m.get("question", "").lower()
                   and float(m.get("volume", 0)) < 50_000
                   and m.get("active", False)]
        markets.sort(key=lambda x: float(x.get("volume", 0)))
        candidates = len(markets)
        log.info(f"Found {candidates} candidate markets")

        for market in markets[:8]:
            q = market.get("question", "")
            city, date_str = parse_city_date(q)
            if not city or not date_str: continue

            # Fetch prices
            prices = {}
            for tok in market.get("tokens", []):
                tid = tok.get("token_id")
                outcome = tok.get("outcome", "")
                if not tid: continue
                try:
                    pr = requests.get(f"{CLOB_API}/price",
                                      params={"token_id": tid, "side": "BUY"}, timeout=8)
                    if pr.status_code == 200:
                        prices[outcome] = float(pr.json().get("price", 0))
                except: pass

            if not prices: continue

            # Forecast
            forecast = get_forecast(city, date_str)
            if not forecast or not forecast.get("high"): continue

            fhigh = forecast["high"]
            dist  = prob_dist(fhigh)

            # Find edges
            import re
            for outcome, mkt_price in prices.items():
                if mkt_price <= 0: continue
                nums = re.findall(r"\d+", outcome)
                if not nums: continue
                my_p = dist.get(nums[0], 0.0)
                edge = my_p - mkt_price

                direction = "YES" if edge > 0 else "NO"
                true_edge = (abs((1 - my_p) - (1 - mkt_price)) if direction == "NO"
                             else abs(edge))

                if true_edge * 100 < MIN_EDGE_PTS: continue

                # EV calc
                if direction == "YES":
                    ev = my_p * (1 - mkt_price) - (1 - my_p) * mkt_price
                else:
                    wp = 1 - my_p
                    ev = wp * mkt_price - (1 - wp) * (1 - mkt_price)

                sig = {
                    "ts": now, "market": q, "city": city,
                    "forecast_c": fhigh, "market_price": mkt_price,
                    "outcome": outcome, "direction": direction,
                    "edge_pts": round(true_edge * 100, 1),
                    "ev_per_10": round(ev * 10, 2), "acted": 0,
                }
                db_insert("signals", sig)
                log.info(f"SIGNAL: {direction} {outcome} edge={sig['edge_pts']}pts ev={sig['ev_per_10']}")

                # Execute (dry or live)
                status = "dry_run"
                tx_hash = None
                if not DRY_RUN and PRIVATE_KEY and FUNDER_ADDR:
                    status, tx_hash = execute_live(market, outcome, direction, mkt_price)

                trade = {
                    "ts": now, "market": q, "outcome": outcome,
                    "direction": direction, "market_price": mkt_price,
                    "my_prob": round(my_p if direction == "YES" else (1 - my_p), 4),
                    "edge_pts": sig["edge_pts"], "ev_per_10": sig["ev_per_10"],
                    "bet_usdc": MAX_BET_USDC, "status": status,
                    "tx_hash": tx_hash, "resolved": 0, "pnl": None,
                }
                db_insert("trades", trade)
                trades_made += 1
                await asyncio.sleep(0.5)
                break   # one trade per market

    except Exception as e:
        log.error(f"Scan error: {e}")

    db_insert("scans", {
        "ts": now, "candidates": candidates,
        "trades_made": trades_made, "mode": "dry_run" if DRY_RUN else "live"
    })

    bot_status["running"] = False
    bot_status["last_scan"] = now
    bot_status["scan_count"] += 1
    log.info(f"=== SCAN END — {trades_made} trades ===")

def execute_live(market, outcome, direction, price):
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
        mo = MarketOrderArgs(token_id=tok["token_id"], amount=MAX_BET_USDC,
                             side=BUY, order_type=OrderType.FOK)
        signed = client.create_market_order(mo)
        resp   = client.post_order(signed, OrderType.FOK)
        return ("filled" if resp.get("status") == "matched" else "failed",
                resp.get("orderID"))
    except Exception as e:
        return f"error: {e}", None

# ── Background scheduler ───────────────────────────────────────────────────────
async def scan_loop():
    while True:
        await run_scan()
        interval = int(os.getenv("SCAN_INTERVAL_MINUTES", "60")) * 60
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
        "mode": "DRY RUN" if DRY_RUN else "LIVE",
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
    return {"dry_run": DRY_RUN, "max_bet": MAX_BET_USDC,
            "min_edge": MIN_EDGE_PTS,
            "scan_interval_min": int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))}
