"""
Backtester for Level-Based Options Trading Strategy.

Replays historical OHLCV data candle-by-candle applying the same signal
logic as engine.py, but entirely in pandas (no live API calls for prices).
Option premiums are estimated using option_pricer.add_bsm_premium().
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np

from level_strategy.config import DEFAULT_CONFIG

logger = logging.getLogger(__name__)


def run_backtest(
    symbol: str,
    from_date: str,
    to_date: str,
    levels: List[Dict[str, Any]],
    config_override: Optional[Dict[str, Any]] = None,
    exchange: str = "NSE",
    interval: str = "FIVE_MINUTE",
    instrument_type: str = "equity",
) -> Dict[str, Any]:
    """
    Replay historical data to evaluate the level strategy.

    Parameters
    ----------
    symbol        : str   e.g. "NIFTY"
    from_date     : str   "YYYY-MM-DD"
    to_date       : str   "YYYY-MM-DD"
    levels        : list  Each item: {"level": 23500, "type": "RESISTANCE", "next_level": 23600}
    config_override : dict  Any config keys to override for this backtest run

    Returns
    -------
    dict with keys: trades, metrics, equity_curve
    """
    cfg = DEFAULT_CONFIG.copy()
    if config_override:
        cfg.update(config_override)

    if not levels:
        return {"error": "no_levels", "message": "No alerts found for this symbol. Please add at least one S/R level first."}

    # ── 1. Fetch historical candles ───────────────────────────────────────────
    try:
        from angel.symbols import get_token
        from data.engine import fetch_historical
        from data.normalizer import normalise

        fetch_exchange = exchange.upper()
        token = get_token(symbol, fetch_exchange)
        if not token and instrument_type in ("futures", "options"):
            from angel.symbols import get_nearest_futures_token
            res = get_nearest_futures_token(symbol)
            token = res[0] if res else None
        if not token:
            # Try NSE as fallback
            token = get_token(symbol, "NSE")
        if not token:
            return {"error": f"No instrument token found for symbol '{symbol}' on exchange '{fetch_exchange}'"}

        use_interval = interval or cfg.get("confirmation_tf", "FIVE_MINUTE")
        from_str = f"{from_date} 09:15"
        to_str   = f"{to_date} 15:30"
        raw = fetch_historical(token, fetch_exchange, use_interval, from_str, to_str)
        if not raw:
            return {"error": "No historical data returned from Angel One"}

        df = normalise(raw).reset_index(drop=True)
    except Exception as exc:
        return {"error": f"Data fetch error: {exc}"}

    if len(df) < 52:
        return {"error": "Insufficient data: need at least 52 bars (EMA50 + warm-up)"}

    # ── 2. Pre-compute indicators ─────────────────────────────────────────────
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    # Option premium via BSM pricer (adds atm_premium column)
    try:
        from backtest.option_pricer import add_bsm_premium
        df_ce = add_bsm_premium(df.copy(), option_type="CE", symbol=symbol)
        df_pe = add_bsm_premium(df.copy(), option_type="PE", symbol=symbol)
        df["atm_ce_premium"] = df_ce["atm_premium"]
        df["atm_pe_premium"] = df_pe["atm_premium"]
    except Exception as exc:
        logger.warning("BSM premium computation failed: %s — using 0", exc)
        df["atm_ce_premium"] = 0.0
        df["atm_pe_premium"] = 0.0

    # ── 3. Simulated trade loop ───────────────────────────────────────────────
    sim_trades: List[Dict[str, Any]] = []
    open_trade: Optional[Dict[str, Any]] = None
    daily_trade_count: Dict[str, int] = {}

    try:
        limit_t = datetime.strptime(cfg["trade_time_limit"], "%H:%M").time()
    except Exception:
        limit_t = datetime.strptime("15:15", "%H:%M").time()

    max_per_day = int(cfg.get("max_trades_per_day", 3))

    for i in range(51, len(df) - 1):
        row      = df.iloc[i]
        next_row = df.iloc[i + 1]

        try:
            ts   = pd.to_datetime(row["timestamp"])
            date_str = ts.date().isoformat()
        except Exception:
            continue

        # ── Manage open trade ─────────────────────────────────────────────
        if open_trade:
            ot     = open_trade["option_type"]
            close  = float(row["close"])
            high   = float(row["high"])
            low    = float(row["low"])
            opt_p  = float(row["atm_ce_premium"] if ot == "CE" else row["atm_pe_premium"])
            level  = open_trade["level"]
            tsl    = open_trade["tsl_level"]

            exit_price  = None
            exit_reason = None

            # Force exit at time limit
            if ts.time() >= limit_t:
                exit_price  = opt_p
                exit_reason = "time_limit"

            # SL check
            elif cfg["sl_mode"] == "level_break":
                if ot == "CE" and close < level:
                    exit_price  = opt_p
                    exit_reason = "sl"
                elif ot == "PE" and close > level:
                    exit_price  = opt_p
                    exit_reason = "sl"
            elif opt_p <= open_trade["sl"]:
                exit_price  = open_trade["sl"]
                exit_reason = "sl"

            # Target check
            if exit_reason is None:
                if cfg["target_mode"] == "next_level" and open_trade["next_level"] > 0:
                    nl = open_trade["next_level"]
                    if ot == "CE" and high >= nl:
                        exit_price  = opt_p
                        exit_reason = "target"
                    elif ot == "PE" and low <= nl:
                        exit_price  = opt_p
                        exit_reason = "target"
                elif opt_p >= open_trade["target"]:
                    exit_price  = open_trade["target"]
                    exit_reason = "target"

            # TSL update + check
            if exit_reason is None and cfg["use_tsl"]:
                if cfg["tsl_mode"] == "percent":
                    new_tsl = round(opt_p * (1 - cfg["tsl_percent"] / 100.0), 2)
                else:
                    new_tsl = round(opt_p - cfg["tsl_points"], 2)
                if new_tsl > tsl:
                    open_trade["tsl_level"] = new_tsl
                if opt_p <= open_trade["tsl_level"]:
                    exit_price  = open_trade["tsl_level"]
                    exit_reason = "tsl"

            # Opposite signal
            if exit_reason is None and cfg.get("exit_on_opposite_signal"):
                ema20 = float(row["ema20"])
                ema50 = float(row["ema50"])
                trend = "UP" if ema20 > ema50 else ("DOWN" if ema20 < ema50 else "NEUTRAL")
                if ot == "CE" and trend == "DOWN":
                    exit_price  = opt_p
                    exit_reason = "opposite_signal"
                elif ot == "PE" and trend == "UP":
                    exit_price  = opt_p
                    exit_reason = "opposite_signal"

            if exit_price is not None and exit_reason:
                raw_pnl = (exit_price - open_trade["entry_price"]) * open_trade["quantity"]
                sim_trades.append({
                    **open_trade,
                    "exit_price":  round(exit_price, 2),
                    "exit_reason": exit_reason,
                    "exit_time":   str(ts),
                    "pnl":         round(raw_pnl, 2),
                    "net_pnl":     round(raw_pnl, 2),
                })
                open_trade = None

        # ── Look for new entry (only if no open trade) ────────────────────
        if open_trade is None:
            # Daily trade limit
            if max_per_day > 0 and daily_trade_count.get(date_str, 0) >= max_per_day:
                continue
            if ts.time() >= limit_t:
                continue

            ema20 = float(row["ema20"])
            ema50 = float(row["ema50"])
            close = float(row["close"])

            for alert in levels:
                level      = float(alert.get("level", 0))
                next_level = float(alert.get("next_level", 0))
                if level <= 0:
                    continue

                # Trend filter
                if cfg["use_trend_filter"]:
                    trend = "UP" if ema20 > ema50 else ("DOWN" if ema20 < ema50 else "NEUTRAL")
                    if trend == "NEUTRAL":
                        continue
                    if close > level and trend != "UP":
                        continue
                    if close < level and trend != "DOWN":
                        continue

                if close > level:
                    option_type = "CE"
                elif close < level:
                    option_type = "PE"
                else:
                    continue

                # Entry price = next bar open (next-candle entry — same as existing engine)
                entry_price = float(next_row["atm_ce_premium"] if option_type == "CE" else next_row["atm_pe_premium"])
                if entry_price <= 0:
                    continue

                # SL
                if cfg["sl_mode"] == "level_break":
                    sl = level
                else:
                    sl = round(entry_price * (1 - cfg["sl_percent"] / 100.0), 2)

                # Target
                if cfg["target_mode"] == "next_level" and next_level > 0:
                    target = next_level
                else:
                    risk   = abs(entry_price - sl) if cfg["sl_mode"] != "level_break" else entry_price * 0.15
                    target = round(entry_price + risk * cfg["risk_reward"], 2)

                # Quantity
                lot_size = 50 if "NIFTY" in symbol.upper() else (15 if "BANK" in symbol.upper() else 1)
                try:
                    from angel.symbols import get_lot_size
                    ls = get_lot_size(symbol)
                    if ls > 1:
                        lot_size = ls
                except Exception:
                    pass

                if cfg["quantity_mode"] == "auto" and cfg["capital"] > 0:
                    lots = max(1, int(cfg["capital"] / (entry_price * lot_size)))
                else:
                    lots = max(1, int(cfg["fixed_lots"]))
                quantity = lots * lot_size

                open_trade = {
                    "id":          f"bt_{i}",
                    "symbol":      symbol,
                    "option_type": option_type,
                    "entry_price": round(entry_price, 2),
                    "sl":          sl,
                    "target":      target,
                    "tsl_level":   sl,
                    "quantity":    quantity,
                    "lot_size":    lot_size,
                    "lots":        lots,
                    "level":       level,
                    "next_level":  next_level,
                    "entry_time":  str(pd.to_datetime(next_row["timestamp"])),
                    "paper":       True,
                    "status":      "open",
                }
                daily_trade_count[date_str] = daily_trade_count.get(date_str, 0) + 1
                break  # one trade per bar

    # Close any trade still open at end of data
    if open_trade:
        last = df.iloc[-1]
        ot   = open_trade["option_type"]
        ep   = float(last["atm_ce_premium"] if ot == "CE" else last["atm_pe_premium"])
        raw_pnl = (ep - open_trade["entry_price"]) * open_trade["quantity"]
        sim_trades.append({
            **open_trade,
            "exit_price":  round(ep, 2),
            "exit_reason": "end_of_data",
            "exit_time":   str(pd.to_datetime(last["timestamp"])),
            "pnl":         round(raw_pnl, 2),
            "net_pnl":     round(raw_pnl, 2),
        })

    # ── 4. Metrics ────────────────────────────────────────────────────────────
    metrics = _compute_metrics(sim_trades, float(cfg["capital"]))

    # ── 5. Equity curve ───────────────────────────────────────────────────────
    equity = float(cfg["capital"])
    equity_curve = [["start", round(equity, 2)]]
    for t in sim_trades:
        equity += t["net_pnl"]
        equity_curve.append([t["exit_time"], round(equity, 2)])

    return {
        "trades":       sim_trades,
        "metrics":      metrics,
        "equity_curve": equity_curve,
        "total_bars":   len(df),
        "symbol":       symbol,
        "from_date":    from_date,
        "to_date":      to_date,
    }


def _compute_metrics(trades: List[Dict], capital: float) -> Dict[str, Any]:
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "max_drawdown": 0, "sharpe": 0,
            "avg_pnl": 0, "best_trade": 0, "worst_trade": 0,
        }
    pnls  = [t["net_pnl"] for t in trades]
    wins  = sum(1 for p in pnls if p > 0)
    total = len(pnls)

    # Max drawdown
    equity, peak, max_dd = capital, capital, 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Sharpe
    sharpe = 0.0
    if total > 1:
        mean = sum(pnls) / total
        var  = sum((p - mean) ** 2 for p in pnls) / (total - 1)
        std  = math.sqrt(var) if var > 0 else 0.0
        sharpe = round(mean / std * math.sqrt(252), 2) if std > 0 else 0.0

    return {
        "total_trades": total,
        "wins":         wins,
        "losses":       total - wins,
        "win_rate":     round(wins / total * 100, 1),
        "total_pnl":    round(sum(pnls), 2),
        "avg_pnl":      round(sum(pnls) / total, 2),
        "best_trade":   round(max(pnls), 2),
        "worst_trade":  round(min(pnls), 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe":       sharpe,
    }
