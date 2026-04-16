"""
Live / paper trading control routes.
A very simple in-process scheduler: each strategy runs on a background thread
that polls on the configured interval.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from angel.symbols import get_token, get_lot_size
from api.models.request_models import LiveStartRequest
from data.engine import fetch_historical
from data.normalizer import normalize
from execution.engine import place_order
from execution.position_tracker import get_all_positions, refresh_ltp_for_paper
from options.engine import get_spot_price
from strategy.loader import load_strategy
from strategy.manager import get_strategy

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Running strategy registry ─────────────────────────────────────────────
_running: Dict[str, Dict[str, Any]] = {}
_running_lock = threading.Lock()


@router.post("/live/start")
def start_trading(req: LiveStartRequest):
    key = f"{req.strategy_name}:{req.symbol}"
    with _running_lock:
        if key in _running:
            raise HTTPException(status_code=409, detail=f"Strategy '{key}' already running.")

    strategy_record = get_strategy(req.strategy_name)
    if strategy_record is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{req.strategy_name}' not found.")

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_trading_loop,
        args=(req, strategy_record, stop_event),
        daemon=True,
        name=f"trader-{key}",
    )

    with _running_lock:
        _running[key] = {
            "key": key,
            "strategy": req.strategy_name,
            "symbol": req.symbol,
            "exchange": req.exchange,
            "paper": req.paper,
            "started_at": datetime.utcnow().isoformat(),
            "_stop": stop_event,
            "_thread": thread,
        }
    thread.start()
    logger.info("Trading started: %s (paper=%s)", key, req.paper)
    return {"status": "started", "key": key, "paper": req.paper}


@router.post("/live/stop")
def stop_trading(strategy_name: str, symbol: str):
    key = f"{strategy_name}:{symbol}"
    with _running_lock:
        entry = _running.get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No running instance for '{key}'.")

    entry["_stop"].set()
    with _running_lock:
        _running.pop(key, None)
    logger.info("Trading stopped: %s", key)
    return {"status": "stopped", "key": key}


@router.get("/live/status")
def trading_status():
    with _running_lock:
        return [
            {k: v for k, v in entry.items() if not k.startswith("_")}
            for entry in _running.values()
        ]


# ── Background trading loop ───────────────────────────────────────────────

_INTERVAL_SECONDS: Dict[str, int] = {
    "ONE_MINUTE":     60,
    "THREE_MINUTE":   180,
    "FIVE_MINUTE":    300,
    "TEN_MINUTE":     600,
    "FIFTEEN_MINUTE": 900,
    "THIRTY_MINUTE":  1800,
    "ONE_HOUR":       3600,
    "ONE_DAY":        86400,
}


def _trading_loop(
    req: LiveStartRequest,
    strategy_record: Dict[str, Any],
    stop_event: threading.Event,
) -> None:
    strategy = load_strategy(strategy_record["file_path"])
    token    = get_token(req.symbol, req.exchange)
    lot_size = get_lot_size(req.symbol, req.exchange)
    interval_sec = _INTERVAL_SECONDS.get(req.interval.upper(), 60)

    # Keep a rolling window of candles
    candle_df = None
    in_position = False

    while not stop_event.is_set():
        try:
            now  = datetime.now()
            from_dt = now.strftime("%Y-%m-%d") + " 09:15"
            to_dt   = now.strftime("%Y-%m-%d %H:%M")

            from data.engine import fetch_historical
            raw = fetch_historical(
                symboltoken=token,
                exchange=req.exchange,
                interval=req.interval,
                from_date=from_dt,
                to_date=to_dt,
            )
            candle_df = normalize(raw)
            if candle_df.empty:
                logger.warning("No candles for %s", req.symbol)
                stop_event.wait(interval_sec)
                continue

            df_with_signals = strategy.generate(candle_df)
            last_signal = int(df_with_signals["signal"].iloc[-1])

            ltp = get_spot_price(req.symbol, req.exchange) or float(candle_df["close"].iloc[-1])
            qty = max(1, int(req.capital * 0.95 / ltp / lot_size) * lot_size) if lot_size > 1 \
                else max(1, int(req.capital * 0.95 / ltp))

            if last_signal == 1 and not in_position:
                place_order(
                    symbol=req.symbol,
                    token=token or "",
                    exchange=req.exchange,
                    transaction_type="BUY",
                    quantity=qty,
                    order_type="MARKET",
                    product_type="INTRADAY",
                    paper=req.paper,
                    ltp=ltp,
                )
                in_position = True
                logger.info("[%s] BUY signal executed @ %.2f", req.symbol, ltp)

            elif last_signal == -1 and in_position:
                place_order(
                    symbol=req.symbol,
                    token=token or "",
                    exchange=req.exchange,
                    transaction_type="SELL",
                    quantity=qty,
                    order_type="MARKET",
                    product_type="INTRADAY",
                    paper=req.paper,
                    ltp=ltp,
                )
                in_position = False
                logger.info("[%s] SELL signal executed @ %.2f", req.symbol, ltp)

        except Exception as exc:
            logger.exception("Trading loop error for %s: %s", req.symbol, exc)

        stop_event.wait(interval_sec)
