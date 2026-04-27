"""
Core engine for Level-Based Options Trading Strategy.

Flow:
  1. TradingView sends a webhook alert → add_alert()
  2. Background monitor thread (every 60 s) calls:
       check_signals()  — look for new entry conditions on active levels
       check_trades()   — monitor open trades for SL / target / TSL hits
  3. Entries and exits route through execution.engine.place_order()
     (paper=True  → in-memory simulator,  paper=False → Angel One live)
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional

import pandas as pd

from level_strategy.config import load_config
from level_strategy.trade_manager import Trade, trade_manager

logger = logging.getLogger(__name__)

# Alerts persisted so they survive server restarts
_ALERTS_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "level_strategy_alerts.json")
)

# ── In-memory alert store ─────────────────────────────────────────────────────
active_levels: List[Dict[str, Any]] = []
_alerts_lock = threading.Lock()

# ── Monitor thread state ──────────────────────────────────────────────────────
_monitor_thread: Optional[threading.Thread] = None
_monitor_stop   = threading.Event()
_monitor_paper  = True


# ─────────────────────────────────────────────────────────────────────────────
#  Alert management
# ─────────────────────────────────────────────────────────────────────────────

def _load_alerts() -> None:
    global active_levels
    if os.path.exists(_ALERTS_FILE):
        try:
            with open(_ALERTS_FILE, "r", encoding="utf-8") as f:
                active_levels = json.load(f)
        except Exception:
            active_levels = []


def _save_alerts() -> None:
    os.makedirs(os.path.dirname(_ALERTS_FILE), exist_ok=True)
    with open(_ALERTS_FILE, "w", encoding="utf-8") as f:
        json.dump(active_levels, f, indent=2)


def add_alert(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accept a TradingView webhook payload and store as an active level.
    Handles field-name variations gracefully.

    Minimal TV payload (type auto-detected from current spot price):
        {"symbol": "NIFTY", "level": 23500}

    If 'type' is omitted the engine fetches the current LTP for the symbol:
        level > spot  →  RESISTANCE (price is below, level is overhead)
        level < spot  →  SUPPORT    (price is above, level is below)

    Deduplicates: ignores if a level within ±0.5% of an existing one already
    exists for the same symbol.
    """
    # Normalise field names (handle TV payload variations)
    symbol     = str(payload.get("symbol") or payload.get("ticker") or "NIFTY").upper()
    level      = float(payload.get("level") or payload.get("price") or payload.get("value") or 0)
    next_level = float(payload.get("next_level") or payload.get("next_price") or 0)

    raw_ts = payload.get("timestamp") or payload.get("time") or payload.get("alert_time") or ""
    try:
        ts = pd.to_datetime(raw_ts).strftime("%Y-%m-%d %H:%M:%S") if raw_ts else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if level <= 0:
        raise ValueError("'level' must be a positive price value.")

    # Auto-determine type from current spot price if not provided
    explicit_type = payload.get("type") or payload.get("alert_type") or payload.get("side")
    if explicit_type:
        alert_type = str(explicit_type).upper()
    else:
        # Fetch current spot price for the symbol
        spot = None
        try:
            from angel.symbols import get_token
            token = get_token(symbol, "NSE")
            if token:
                spot = _get_ltp(token, "NSE")
        except Exception:
            pass

        if spot and spot > 0:
            alert_type = "RESISTANCE" if level > spot else "SUPPORT"
        else:
            # Fallback: cannot fetch spot — default RESISTANCE and note it
            alert_type = "RESISTANCE"
            logger.warning(
                "Could not fetch spot price for %s to auto-determine type. "
                "Defaulting to RESISTANCE. Pass 'type' explicitly to override.",
                symbol,
            )

    logger.info(
        "Alert type %s for %s @ %.2f (spot=%s, explicit=%s)",
        alert_type, symbol, level,
        "fetched" if not explicit_type else "n/a",
        bool(explicit_type),
    )

    # Dedup check
    with _alerts_lock:
        for existing in active_levels:
            if existing["symbol"] == symbol:
                if abs(existing["level"] - level) / level < 0.005:
                    return {"status": "duplicate", "alert_id": existing["id"]}

        alert = {
            "id":         str(uuid.uuid4())[:8],
            "symbol":     symbol,
            "level":      level,
            "type":       alert_type,
            "next_level": next_level,
            "timestamp":  ts,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        active_levels.append(alert)
        _save_alerts()

    logger.info("Level alert added: %s %s @ %.2f (%s)", symbol, alert_type, level, alert["id"])
    return {"status": "ok", "alert_id": alert["id"], "type_detected": alert_type}


def remove_alert(alert_id: str) -> bool:
    with _alerts_lock:
        for i, a in enumerate(active_levels):
            if a["id"] == alert_id:
                active_levels.pop(i)
                _save_alerts()
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  Indicator helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_candles(symbol: str, interval: str, n_bars: int = 100) -> Optional[pd.DataFrame]:
    """Fetch last n_bars of OHLCV from Angel One for the underlying index/equity."""
    try:
        from angel.symbols import get_token
        from data.engine import fetch_historical
        from data.normalizer import normalise

        token = get_token(symbol, "NSE")
        if not token:
            # Try NFO futures token as fallback for indices
            from angel.symbols import get_nearest_futures_token
            res = get_nearest_futures_token(symbol)
            if res:
                token = res[0]
            else:
                logger.warning("No token found for symbol: %s", symbol)
                return None

        now  = datetime.now()
        # Fetch enough history: 2 trading days for most intervals
        from_dt = now.replace(hour=9, minute=15, second=0) - pd.Timedelta(days=5)
        from_str = from_dt.strftime("%Y-%m-%d %H:%M")
        to_str   = now.strftime("%Y-%m-%d %H:%M")

        raw = fetch_historical(token, "NSE", interval, from_str, to_str)
        if not raw:
            return None
        df = normalise(raw)
        return df.tail(n_bars).reset_index(drop=True)
    except Exception as exc:
        logger.error("_fetch_candles error (%s): %s", symbol, exc)
        return None


def _compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _determine_trend(df: pd.DataFrame) -> str:
    """Return 'UP', 'DOWN', or 'NEUTRAL' based on EMA20/EMA50."""
    if df is None or len(df) < 51:
        return "NEUTRAL"
    ema20 = _compute_ema(df["close"], 20).iloc[-1]
    ema50 = _compute_ema(df["close"], 50).iloc[-1]
    if ema20 > ema50:
        return "UP"
    elif ema20 < ema50:
        return "DOWN"
    return "NEUTRAL"


def _last_closed_candle(df: pd.DataFrame) -> Optional[pd.Series]:
    """Return the last fully closed candle (second-to-last row)."""
    if df is None or len(df) < 2:
        return None
    return df.iloc[-2]


# ─────────────────────────────────────────────────────────────────────────────
#  Option token resolver
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_option_token(
    symbol: str,
    spot: float,
    option_type: str,    # "CE" | "PE"
    entry_mode: str,     # "ATM" | "ITM1" | "OTM1"
    strike_offset: int,  # 0 = ATM, +1 = one step away, etc.
) -> Optional[Dict[str, Any]]:
    """
    Find the nearest expiry option contract token from instrument master.
    Returns dict with: token, symbol, strike, expiry, lot_size  or None.
    """
    try:
        from angel.symbols import get_option_chain, get_lot_size
        from backtest.option_pricer import atm_strike_for, STRIKE_STEP

        atm = atm_strike_for(spot, symbol)
        step = STRIKE_STEP.get(symbol.upper(), STRIKE_STEP.get("DEFAULT", 50))

        # Adjust strike for entry_mode and offset
        if entry_mode == "ITM1":
            # ITM for CE = strike below ATM; ITM for PE = strike above ATM
            raw_offset = -1 if option_type == "CE" else 1
        elif entry_mode == "OTM1":
            raw_offset = 1 if option_type == "CE" else -1
        else:
            raw_offset = 0
        raw_offset += strike_offset
        target_strike = atm + raw_offset * step

        chain = get_option_chain(symbol)
        if not chain:
            return None

        # Sort by expiry ascending, find nearest active
        today = date.today()
        valid = []
        for c in chain:
            if c.get("optiontype", "").upper() != option_type.upper():
                continue
            try:
                from datetime import datetime as _dt
                exp = _dt.strptime(c["expiry"], "%d%b%Y").date()
                if exp < today:
                    continue
            except Exception:
                continue
            if abs(float(c.get("strike", 0)) - target_strike) < (step * 0.6):
                valid.append((exp, c))

        if not valid:
            # Fallback: find closest strike in nearest expiry
            by_expiry: Dict = {}
            for c in chain:
                if c.get("optiontype", "").upper() != option_type.upper():
                    continue
                try:
                    from datetime import datetime as _dt2
                    exp2 = _dt2.strptime(c["expiry"], "%d%b%Y").date()
                    if exp2 < today:
                        continue
                    by_expiry.setdefault(exp2, []).append(c)
                except Exception:
                    continue
            if not by_expiry:
                return None
            nearest_exp = min(by_expiry.keys())
            candidates = by_expiry[nearest_exp]
            best = min(candidates, key=lambda c: abs(float(c.get("strike", 0)) - target_strike))
            valid = [(nearest_exp, best)]

        valid.sort(key=lambda x: x[0])
        exp_date, contract = valid[0]
        lot_size = int(contract.get("lotsize", 1)) or get_lot_size(symbol)
        return {
            "token":     contract["token"],
            "symbol":    contract["symbol"],
            "strike":    float(contract.get("strike", target_strike)),
            "expiry":    contract["expiry"],
            "lot_size":  lot_size,
            "exchange":  "NFO",
        }
    except Exception as exc:
        logger.error("_resolve_option_token error: %s", exc)
        return None


def _get_ltp(token: str, exchange: str = "NFO") -> Optional[float]:
    """Fetch latest traded price via Angel One quote API."""
    try:
        from angel.client import angel_client
        resp = angel_client.smart_api.ltpData(exchange, "", token)
        if resp and resp.get("data"):
            return float(resp["data"].get("ltp", 0)) or None
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Signal detection
# ─────────────────────────────────────────────────────────────────────────────

def check_signals(paper: bool = True) -> None:
    """
    Scan all active levels for entry conditions.
    Called every 60 s by the monitor thread.
    """
    cfg = load_config()
    if not active_levels:
        return

    # Market hours guard
    now = datetime.now()
    try:
        limit_t = datetime.strptime(cfg["trade_time_limit"], "%H:%M").time()
        if now.time() >= limit_t:
            return
    except Exception:
        pass
    if not (now.weekday() < 5 and
            datetime.strptime("09:15", "%H:%M").time() <= now.time()):
        return

    max_trades = int(cfg.get("max_trades_per_day", 3))
    if max_trades > 0 and trade_manager.trades_today() >= max_trades:
        return

    for alert in list(active_levels):
        symbol     = alert["symbol"]
        level      = float(alert["level"])
        next_level = float(alert.get("next_level", 0))

        # Skip if there's already an open trade for this level
        if trade_manager.has_open_trade_for_level(symbol, level):
            continue

        # Fetch confirmation-timeframe candles
        df_conf = _fetch_candles(symbol, cfg["confirmation_tf"], n_bars=100)
        if df_conf is None or len(df_conf) < 2:
            continue

        candle = _last_closed_candle(df_conf)
        if candle is None:
            continue

        close = float(candle["close"])
        spot  = close  # use last confirmed close as spot

        # Trend filter
        if cfg["use_trend_filter"]:
            trend = _determine_trend(df_conf)
            if trend == "NEUTRAL":
                continue
            # Long (CE) only when UP; Short (PE) only when DOWN
            if close > level and trend != "UP":
                continue
            if close < level and trend != "DOWN":
                continue

        # Entry condition
        if close > level:
            option_type = "CE"
        elif close < level:
            option_type = "PE"
        else:
            continue

        # Resolve option contract
        contract = _resolve_option_token(
            symbol, spot, option_type,
            cfg["entry_mode"], int(cfg["strike_offset"])
        )
        if not contract:
            logger.warning("No option contract found for %s %s @ %.0f", symbol, option_type, spot)
            continue

        # Get option LTP for entry
        ltp = _get_ltp(contract["token"])
        if ltp is None or ltp <= 0:
            logger.warning("Could not get LTP for %s, skipping entry", contract["symbol"])
            continue

        # Calculate SL
        if cfg["sl_mode"] == "level_break":
            sl = level  # exit if underlying closes back through level
        else:
            sl = round(ltp * (1 - cfg["sl_percent"] / 100.0), 2)

        # Calculate target
        if cfg["target_mode"] == "next_level" and next_level > 0:
            target = next_level
        else:
            risk = abs(ltp - sl) if cfg["sl_mode"] != "level_break" else ltp * 0.15
            target = round(ltp + risk * cfg["risk_reward"], 2)

        # Quantity
        lot_size = contract["lot_size"]
        if cfg["quantity_mode"] == "auto" and cfg["capital"] > 0:
            lots = max(1, int(cfg["capital"] / (ltp * lot_size)))
        else:
            lots = max(1, int(cfg["fixed_lots"]))
        quantity = lots * lot_size

        # Place entry order
        try:
            from execution.engine import place_order
            resp = place_order(
                symbol       = contract["symbol"],
                token        = contract["token"],
                exchange     = "NFO",
                transaction_type = "BUY",
                quantity     = quantity,
                paper        = paper,
                ltp          = ltp,
                order_tag    = f"LS_{alert['id']}",
            )
            order_id = str(resp.get("data", {}).get("orderid") or resp.get("order_id", "paper"))
        except Exception as exc:
            logger.error("Entry order failed for %s: %s", contract["symbol"], exc)
            continue

        # Record trade
        trade = Trade(
            alert_id     = alert["id"],
            symbol       = symbol,
            option_symbol= contract["symbol"],
            token        = contract["token"],
            option_type  = option_type,
            strike       = contract["strike"],
            expiry       = contract["expiry"],
            entry_price  = ltp,
            sl           = sl,
            target       = target,
            tsl_level    = sl,
            quantity     = quantity,
            lot_size     = lot_size,
            lots         = lots,
            level        = level,
            next_level   = next_level,
            paper        = paper,
            entry_time   = now.strftime("%Y-%m-%d %H:%M:%S"),
            order_id     = order_id,
        )
        trade_manager.add_trade(trade)
        logger.info(
            "ENTRY %s %s %s %s @ %.2f | SL %.2f | TGT %.2f | qty %d",
            symbol, option_type, contract["symbol"], contract["expiry"],
            ltp, sl, target, quantity
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Trade monitoring
# ─────────────────────────────────────────────────────────────────────────────

def check_trades(paper: bool = True) -> None:
    """
    Monitor open trades for SL / target / TSL / time-limit hits.
    Called every 60 s by the monitor thread.
    """
    cfg = load_config()
    if not trade_manager.active_trades:
        return

    now = datetime.now()
    try:
        limit_t = datetime.strptime(cfg["trade_time_limit"], "%H:%M").time()
        force_exit_time = now.time() >= limit_t
    except Exception:
        force_exit_time = False

    for trade in list(trade_manager.active_trades):
        # Get current option LTP
        opt_ltp = _get_ltp(trade.token)
        if opt_ltp is None or opt_ltp <= 0:
            continue

        # Also get underlying LTP for level-break SL check
        underlying_ltp: Optional[float] = None
        if cfg["sl_mode"] == "level_break" or cfg.get("exit_on_opposite_signal"):
            underlying_ltp = None
            try:
                from angel.symbols import get_token
                und_token = get_token(trade.symbol, "NSE")
                if und_token:
                    underlying_ltp = _get_ltp(und_token)
            except Exception:
                pass

        exit_price  = opt_ltp
        exit_reason = None

        # ── Force exit at time limit ──────────────────────────────────────
        if force_exit_time:
            exit_reason = "time_limit"

        # ── SL check ─────────────────────────────────────────────────────
        elif cfg["sl_mode"] == "level_break" and underlying_ltp:
            # CE: exit if underlying closes BELOW the level
            # PE: exit if underlying closes ABOVE the level
            if trade.option_type == "CE" and underlying_ltp < trade.level:
                exit_reason = "sl"
            elif trade.option_type == "PE" and underlying_ltp > trade.level:
                exit_reason = "sl"
        elif cfg["sl_mode"] == "percent":
            if opt_ltp <= trade.sl:
                exit_reason = "sl"

        # ── Target check ─────────────────────────────────────────────────
        if exit_reason is None:
            if cfg["target_mode"] == "next_level" and underlying_ltp and trade.next_level > 0:
                if trade.option_type == "CE" and underlying_ltp >= trade.next_level:
                    exit_reason = "target"
                elif trade.option_type == "PE" and underlying_ltp <= trade.next_level:
                    exit_reason = "target"
            elif opt_ltp >= trade.target:
                exit_reason = "target"

        # ── TSL update ────────────────────────────────────────────────────
        if exit_reason is None and cfg["use_tsl"]:
            new_tsl = trade.tsl_level
            if cfg["tsl_mode"] == "percent":
                new_tsl = round(opt_ltp * (1 - cfg["tsl_percent"] / 100.0), 2)
            else:
                new_tsl = round(opt_ltp - cfg["tsl_points"], 2)
            # Only ratchet up (never lower the TSL)
            if new_tsl > trade.tsl_level:
                trade.tsl_level = new_tsl
                trade_manager.persist()
            # Check if TSL hit
            if opt_ltp <= trade.tsl_level:
                exit_reason = "tsl"

        # ── Opposite signal exit ──────────────────────────────────────────
        if exit_reason is None and cfg.get("exit_on_opposite_signal") and underlying_ltp:
            df_conf = _fetch_candles(trade.symbol, cfg["confirmation_tf"], n_bars=100)
            if df_conf is not None and len(df_conf) >= 51:
                trend = _determine_trend(df_conf)
                if trade.option_type == "CE" and trend == "DOWN":
                    exit_reason = "opposite_signal"
                elif trade.option_type == "PE" and trend == "UP":
                    exit_reason = "opposite_signal"

        # ── Execute exit ──────────────────────────────────────────────────
        if exit_reason:
            try:
                from execution.engine import place_order
                resp = place_order(
                    symbol           = trade.option_symbol,
                    token            = trade.token,
                    exchange         = "NFO",
                    transaction_type = "SELL",
                    quantity         = trade.quantity,
                    paper            = trade.paper,
                    ltp              = exit_price,
                    order_tag        = f"LS_EXIT_{trade.id}",
                )
                exit_order_id = str(resp.get("data", {}).get("orderid") or resp.get("order_id", "paper"))
            except Exception as exc:
                logger.error("Exit order failed for trade %s: %s", trade.id, exc)
                exit_order_id = "error"

            trade_manager.close_trade(
                trade.id, exit_price, exit_reason, exit_order_id
            )
            logger.info(
                "EXIT %s %s @ %.2f | reason=%s | pnl=%.2f",
                trade.option_symbol, trade.option_type, exit_price,
                exit_reason, (exit_price - trade.entry_price) * trade.quantity
            )


# ─────────────────────────────────────────────────────────────────────────────
#  Monitor thread
# ─────────────────────────────────────────────────────────────────────────────

def _monitor_loop(paper: bool) -> None:
    logger.info("Level strategy monitor started (paper=%s)", paper)
    while not _monitor_stop.is_set():
        try:
            check_signals(paper)
            check_trades(paper)
        except Exception as exc:
            logger.error("Monitor loop error: %s", exc)
        _monitor_stop.wait(timeout=60)
    logger.info("Level strategy monitor stopped.")


def start_monitor(paper: bool = True) -> Dict[str, Any]:
    global _monitor_thread, _monitor_paper
    if _monitor_thread and _monitor_thread.is_alive():
        return {"status": "already_running", "paper": _monitor_paper}
    _monitor_stop.clear()
    _monitor_paper = paper
    _monitor_thread = threading.Thread(
        target=_monitor_loop, args=(paper,), daemon=True, name="LevelStratMonitor"
    )
    _monitor_thread.start()
    return {"status": "started", "paper": paper}


def stop_monitor() -> Dict[str, Any]:
    global _monitor_thread
    if not _monitor_thread or not _monitor_thread.is_alive():
        return {"status": "not_running"}
    _monitor_stop.set()
    _monitor_thread.join(timeout=5)
    _monitor_thread = None
    return {"status": "stopped"}


def is_running() -> bool:
    return bool(_monitor_thread and _monitor_thread.is_alive())


# Load persisted alerts on import
_load_alerts()
