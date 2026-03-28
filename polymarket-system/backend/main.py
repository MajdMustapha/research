"""
Polymarket Niche Bot — FastAPI Backend v2
Improvements: GFS ensemble forecasts, Kelly sizing, WebSocket price feeds, backtest API.

Start with:  uvicorn backend.main:app --reload --port 8000
"""

from fastapi import FastAPI, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

GAMMA_API      = "https://gamma-api.polymarket.com"
CLOB_API       = "https://clob.polymarket.com"
CLOB_WS        = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

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
    "taipei":    (25.0777, 121.2328),  # RCTP Taoyuan
    "seoul":     (37.5583, 126.7906),  # RKSS Gimpo
    "beijing":   (40.0799, 116.6031),
    "shanghai":  (31.1979, 121.3363),
    "tokyo":     (35.5493, 139.7798),
    "dubai":     (25.2532, 55.3657),
    "singapore": (1.3644, 103.9915),
}

def parse_city_date(question: str):
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

# ── Improvement 1: GFS 31-member ensemble forecast ──────────────────────────
def get_ensemble_forecast(city: str, date_str: str):
    """Fetch 31-member GFS ensemble from Open-Meteo for calibrated probabilities."""
    key = city.lower().strip()
    coords = next((v for k, v in CITY_COORDS.items() if k in key or key in k), None)
    if not coords: return None
    lat, lon = coords
    try:
        data = curl_get("https://ensemble-api.open-meteo.com/v1/ensemble", {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max",
            "temperature_unit": "celsius", "timezone": "auto",
            "start_date": date_str, "end_date": date_str,
            "models": "gfs_seamless"
        })
        d = data.get("daily", {})
        highs = d.get("temperature_2m_max", [])
        if not highs:
            return None
        # For ensemble API, we may get a list of member values or a single list
        # Handle both formats
        if isinstance(highs[0], list):
            members = [h for sublist in highs for h in sublist if h is not None]
        else:
            members = [h for h in highs if h is not None]
        if not members:
            return None
        return {
            "members": members,
            "mean": round(statistics.mean(members), 1),
            "stdev": round(statistics.stdev(members), 1) if len(members) > 1 else 1.5,
            "min": round(min(members), 1),
            "max": round(max(members), 1),
            "count": len(members),
            "source": f"Open-Meteo GFS Ensemble ({len(members)} members)"
        }
    except Exception as e:
        log.warning(f"Ensemble forecast failed {city}: {e}")
        return None

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

def get_best_forecast(city: str, date_str: str):
    """Try ensemble first, fall back to deterministic."""
    forecast = get_ensemble_forecast(city, date_str)
    if forecast and forecast["count"] > 1:
        log.info(f"  Ensemble forecast for {city}: {forecast['mean']}°C ±{forecast['stdev']}° ({forecast['count']} members)")
        return forecast
    forecast = get_forecast(city, date_str)
    if forecast:
        log.info(f"  Deterministic forecast for {city}: {forecast['mean']}°C (fallback)")
    return forecast

def ensemble_prob_dist(forecast: dict):
    """Build probability distribution from ensemble members.
    Counts what fraction of ensemble members predict each integer temperature."""
    members = forecast["members"]
    mean = forecast["mean"]
    stdev = forecast["stdev"]

    if len(members) >= 5:
        # Empirical distribution from ensemble members
        rounded = [round(m) for m in members]
        counts = {}
        for t in rounded:
            counts[str(t)] = counts.get(str(t), 0) + 1
        total = len(rounded)
        dist = {k: round(v / total, 4) for k, v in counts.items()}
        # Extend to cover nearby temps with small probabilities
        all_temps = [int(k) for k in dist.keys()]
        lo, hi = min(all_temps) - 3, max(all_temps) + 3
        for t in range(lo, hi + 1):
            k = str(t)
            if k not in dist:
                dist[k] = 0.0
        return dist
    else:
        # Gaussian fallback using ensemble mean/stdev
        mu = mean
        sigma = max(stdev, 0.5)
        def g(x): return math.exp(-0.5 * ((x - mu) / sigma) ** 2)
        buckets = list(range(round(mu) - 6, round(mu) + 7))
        raw = {str(b): g(b) for b in buckets}
        total = sum(raw.values())
        return {k: round(v / total, 4) for k, v in raw.items()}

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
        params = {"tag": "weather", "active": "true", "closed": "false", "limit": "200"}
        raw = curl_get(f"{GAMMA_API}/markets", params)
        markets = [m for m in raw
                   if "highest temperature" in m.get("question", "").lower()
                   and float(m.get("volume", 0)) < 50_000
                   and m.get("active", False)]
        markets.sort(key=lambda x: float(x.get("volume", 0)))
        candidates = len(markets)
        log.info(f"Found {candidates} candidate markets")

        # Collect token IDs for WebSocket subscription
        all_token_ids = []
        for market in markets[:8]:
            for tok in market.get("tokens", []):
                tid = tok.get("token_id")
                if tid:
                    all_token_ids.append(tid)

        # Start WebSocket feed if we have tokens and it's not running
        global ws_task
        if all_token_ids and (ws_task is None or ws_task.done()):
            ws_task = asyncio.create_task(ws_price_listener(all_token_ids))
            await asyncio.sleep(2)  # give WS time to connect

        for market in markets[:8]:
            q = market.get("question", "")
            city, date_str = parse_city_date(q)
            if not city or not date_str: continue

            # Fetch prices (WS cache → REST fallback)
            prices = {}
            for tok in market.get("tokens", []):
                tid = tok.get("token_id")
                outcome = tok.get("outcome", "")
                if not tid: continue
                price = get_price(tid)
                if price and price > 0:
                    prices[outcome] = price

            if not prices: continue

            # Ensemble forecast (Improvement 1)
            forecast = get_best_forecast(city, date_str)
            if not forecast: continue

            fmean = forecast["mean"]
            fstdev = forecast["stdev"]
            dist = ensemble_prob_dist(forecast)

            # Find edges
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

                # Kelly sizing (Improvement 2)
                bet_size = kelly_bet(my_p, mkt_price, direction)

                sig = {
                    "ts": now, "market": q, "city": city,
                    "forecast_c": fmean, "ensemble_spread": fstdev,
                    "market_price": mkt_price,
                    "outcome": outcome, "direction": direction,
                    "edge_pts": round(true_edge * 100, 1),
                    "ev_per_10": round(ev * 10, 2), "acted": 0,
                }
                db_insert("signals", sig)
                log.info(f"SIGNAL: {direction} {outcome} edge={sig['edge_pts']}pts "
                         f"ev={sig['ev_per_10']} kelly=${bet_size} "
                         f"(ensemble: {forecast['count']} members, spread={fstdev}°)")

                # Execute (dry or live)
                status = "dry_run"
                tx_hash = None
                if not DRY_RUN and PRIVATE_KEY and FUNDER_ADDR:
                    status, tx_hash = execute_live(market, outcome, direction, mkt_price, bet_size)

                trade = {
                    "ts": now, "market": q, "outcome": outcome,
                    "direction": direction, "market_price": mkt_price,
                    "my_prob": round(my_p if direction == "YES" else (1 - my_p), 4),
                    "edge_pts": sig["edge_pts"], "ev_per_10": sig["ev_per_10"],
                    "bet_usdc": bet_size, "kelly_frac": round(KELLY_FRACTION, 2),
                    "status": status,
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
        "kelly_fraction": KELLY_FRACTION,
        "bankroll": BANKROLL_USDC,
        "ws_connected": len(ws_prices) > 0,
        "ws_tokens_tracked": len(ws_prices),
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
    return {
        "dry_run": DRY_RUN, "max_bet": MAX_BET_USDC,
        "min_edge": MIN_EDGE_PTS, "kelly_fraction": KELLY_FRACTION,
        "bankroll": BANKROLL_USDC,
        "scan_interval_min": int(os.getenv("SCAN_INTERVAL_MINUTES", "60")),
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

@app.get("/api/ws/prices")
def ws_price_snapshot():
    """Return current WebSocket price cache."""
    return ws_prices
