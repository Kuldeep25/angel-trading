"""
FastAPI routes for Level-Based Options Trading Strategy.

Prefix: /level-strategy
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/level-strategy")


# ─────────────────────────────────────────────────────────────────────────────
#  Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class TVAlertPayload(BaseModel):
    """TradingView webhook payload.

    Minimal form (type auto-detected from current spot price):
        {"symbol": "{{ticker}}", "level": {{plot_0}}}

    'type' is optional — if omitted the engine fetches current LTP and sets:
        level > spot  →  RESISTANCE
        level < spot  →  SUPPORT
    """
    symbol:     Optional[str]   = None
    ticker:     Optional[str]   = None       # TV alternative for symbol
    level:      Optional[float] = None
    price:      Optional[float] = None       # TV alternative for level
    type:       Optional[Literal["SUPPORT", "RESISTANCE"]] = None  # auto-detected if omitted
    alert_type: Optional[str]   = None
    next_level: Optional[float] = None
    timestamp:  Optional[str]   = None
    # Allow any extra fields from TV
    model_config = {"extra": "allow"}


class LevelBacktestRequest(BaseModel):
    symbol:          str
    from_date:       str
    to_date:         str
    levels:          List[Dict[str, Any]]
    config_override: Optional[Dict[str, Any]] = None
    exchange:        str = "NSE"
    interval:        str = "FIVE_MINUTE"
    instrument_type: str = "equity"


# ─────────────────────────────────────────────────────────────────────────────
#  Alert endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/alert", tags=["Level Strategy"])
def receive_alert(payload: TVAlertPayload):
    """Receive a TradingView webhook alert and store as an active S/R level."""
    from level_strategy.engine import add_alert
    try:
        result = add_alert(payload.model_dump())
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Alert error: {exc}")


@router.get("/alerts", tags=["Level Strategy"])
def list_alerts():
    """Return all active S/R levels."""
    from level_strategy.engine import active_levels
    return {"alerts": active_levels, "count": len(active_levels)}


@router.delete("/alert/{alert_id}", tags=["Level Strategy"])
def delete_alert(alert_id: str):
    """Remove an active level by ID."""
    from level_strategy.engine import remove_alert
    removed = remove_alert(alert_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return {"status": "removed", "alert_id": alert_id}


# ─────────────────────────────────────────────────────────────────────────────
#  Config endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/config", tags=["Level Strategy"])
def get_config():
    """Return current strategy configuration."""
    from level_strategy.config import load_config
    return load_config()


@router.put("/config", tags=["Level Strategy"])
def update_config(cfg: Dict[str, Any]):
    """Persist updated configuration."""
    from level_strategy.config import save_config
    try:
        save_config(cfg)
        return {"status": "saved"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Config save error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
#  Trade endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trades/active", tags=["Level Strategy"])
def active_trades():
    """Return all currently open trades."""
    from level_strategy.trade_manager import trade_manager
    return {
        "trades": [t.to_dict() for t in trade_manager.active_trades],
        "count":  len(trade_manager.active_trades),
    }


@router.get("/trades/history", tags=["Level Strategy"])
def trade_history(limit: int = Query(default=100, ge=1, le=1000)):
    """Return closed trade history (most recent first)."""
    from level_strategy.trade_manager import trade_manager
    history = trade_manager.trade_history[:limit]
    return {"trades": [t.to_dict() for t in history], "count": len(history)}


@router.post("/trades/exit/{trade_id}", tags=["Level Strategy"])
def manual_exit(trade_id: str):
    """Manually exit an open trade at current market price."""
    from level_strategy.trade_manager import trade_manager
    from level_strategy.engine import _get_ltp

    trade = trade_manager.get_active(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Active trade '{trade_id}' not found.")

    # Get current LTP
    ltp = _get_ltp(trade.token)
    if ltp is None or ltp <= 0:
        raise HTTPException(status_code=503, detail="Could not fetch current LTP from Angel One.")

    try:
        from execution.engine import place_order
        resp = place_order(
            symbol           = trade.option_symbol,
            token            = trade.token,
            exchange         = "NFO",
            transaction_type = "SELL",
            quantity         = trade.quantity,
            paper            = trade.paper,
            ltp              = ltp,
            order_tag        = f"LS_MANUAL_{trade_id}",
        )
        exit_order_id = str(resp.get("data", {}).get("orderid") or resp.get("order_id", "manual"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Exit order failed: {exc}")

    closed = trade_manager.close_trade(trade_id, ltp, "manual", exit_order_id)
    return {"status": "exited", "trade": closed.to_dict() if closed else {}}


# ─────────────────────────────────────────────────────────────────────────────
#  Monitor endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/summary", tags=["Level Strategy"])
def get_summary():
    """Return PnL summary and monitor thread status."""
    from level_strategy.trade_manager import trade_manager
    from level_strategy.engine import is_running, _monitor_paper
    return {
        **trade_manager.summary(),
        "monitor_running": is_running(),
        "paper":           _monitor_paper,
    }


@router.post("/start", tags=["Level Strategy"])
def start_monitor(paper: bool = Query(default=True)):
    """Start the background monitor thread."""
    from level_strategy.engine import start_monitor as _start
    return _start(paper=paper)


@router.post("/stop", tags=["Level Strategy"])
def stop_monitor():
    """Stop the background monitor thread."""
    from level_strategy.engine import stop_monitor as _stop
    return _stop()


# ─────────────────────────────────────────────────────────────────────────────
#  Backtest endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/backtest", tags=["Level Strategy"])
def run_backtest(req: LevelBacktestRequest):
    """Run a historical backtest for the level strategy."""
    from level_strategy.backtester import run_backtest as _bt
    try:
        result = _bt(
            symbol          = req.symbol,
            from_date       = req.from_date,
            to_date         = req.to_date,
            levels          = req.levels,
            config_override = req.config_override,
            exchange        = req.exchange,
            interval        = req.interval,
            instrument_type = req.instrument_type,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest error: {exc}")
