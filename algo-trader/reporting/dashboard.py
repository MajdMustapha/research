"""FastAPI dashboard for monitoring the trading bot."""

import logging
import threading
from datetime import date

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="Algo Trader Dashboard")

# Global references set by main.py before starting
_state = {
    "circuit_breaker": None,
    "position_tracker": None,
    "risk_manager": None,
    "trade_logger": None,
    "config": None,
}


def set_state(
    circuit_breaker=None,
    position_tracker=None,
    risk_manager=None,
    trade_logger=None,
    config=None,
):
    """Set references to live components for the dashboard."""
    if circuit_breaker:
        _state["circuit_breaker"] = circuit_breaker
    if position_tracker:
        _state["position_tracker"] = position_tracker
    if risk_manager:
        _state["risk_manager"] = risk_manager
    if trade_logger:
        _state["trade_logger"] = trade_logger
    if config:
        _state["config"] = config


@app.get("/status")
def get_status():
    """Current bot status: circuit breaker state, open positions, daily PnL."""
    cb = _state["circuit_breaker"]
    pt = _state["position_tracker"]
    rm = _state["risk_manager"]

    positions = []
    if pt:
        for p in pt.get_open_positions():
            positions.append({
                "symbol": p.symbol,
                "side": p.side,
                "size": p.size,
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "unrealized_pnl": p.unrealized_pnl,
            })

    return JSONResponse({
        "circuit_breaker": cb.state.value if cb else "UNKNOWN",
        "open_positions": positions,
        "total_unrealized_pnl": pt.total_unrealized_pnl() if pt else 0,
    })


@app.get("/trades")
def get_trades():
    """Recent trades from the database."""
    tl = _state["trade_logger"]
    if not tl:
        return JSONResponse({"trades": []})
    trades = tl.get_trades()
    return JSONResponse({"trades": trades[:50]})


@app.get("/equity")
def get_equity():
    """Daily summary."""
    tl = _state["trade_logger"]
    if not tl:
        return JSONResponse({"summary": {}})
    summary = tl.get_daily_summary()
    return JSONResponse({"summary": summary})


@app.post("/halt")
def halt_trading():
    """Emergency halt."""
    cb = _state["circuit_breaker"]
    if cb:
        cb.halt()
        return JSONResponse({"status": "halted"})
    return JSONResponse({"status": "error", "message": "circuit breaker not available"}, status_code=500)


@app.post("/resume")
def resume_trading():
    """Resume trading after halt."""
    cb = _state["circuit_breaker"]
    if cb:
        cb.resume()
        return JSONResponse({"status": "resumed"})
    return JSONResponse({"status": "error", "message": "circuit breaker not available"}, status_code=500)


def start_dashboard(config: dict):
    """Start dashboard in a background thread."""
    port = config.get("reporting", {}).get("dashboard_port", 8080)

    def run():
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    logger.info(f"Dashboard started on port {port}")
    return thread
