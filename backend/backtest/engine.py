"""
Backtest engine — simulates a strategy over historical data with
stop-loss, trailing stop-loss, and position sizing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Dict, List, Optional

import pandas as pd

from angel.symbols import get_token, get_lot_size, get_nearest_futures_token, get_all_futures_tokens
from backtest.metrics import compute_metrics
from backtest.models import Trade
from data.engine import fetch_historical
from data.normalizer import normalize
from strategy.loader import load_strategy
from strategy.manager import get_strategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    strategy_name: str
    symbol: str
    exchange: str = "NSE"
    interval: str = "ONE_DAY"
    from_date: str = ""   # "YYYY-MM-DD HH:MM"
    to_date: str = ""     # "YYYY-MM-DD HH:MM"
    capital: float = 100_000.0
    sl_pct: float = 2.0    # stop-loss %
    tsl_pct: float = 0.0   # trailing stop-loss % (0 = disabled)
    position_size_pct: float = 95.0  # % of capital to deploy per trade


# Trade is defined in backtest.models to avoid circular imports
# Re-export for backwards compatibility
from backtest.models import Trade as Trade  # noqa: F401


def run_backtest(cfg: BacktestConfig) -> Dict[str, Any]:
    """
    Run a full backtest for the given configuration.

    Returns
    -------
    {
        "config": {...},
        "summary": {total_return, max_drawdown, win_rate, sharpe_ratio, total_trades},
        "equity_curve": [[timestamp, equity], ...],
        "trades": [{...}, ...]
    }
    """
    # ── 1. Resolve symbol token ─────────────────────────────────────────────
    token = get_token(cfg.symbol, cfg.exchange)
    resolved_symbol   = cfg.symbol
    resolved_exchange = cfg.exchange

    if token is None:
        raise ValueError(
            f"Symbol '{cfg.symbol}' not found on exchange '{cfg.exchange}'. "
            "Make sure the instrument master is loaded."
        )

    # ── 2. Fetch & normalize historical data ────────────────────────────────
    raw = fetch_historical(
        symboltoken=token,
        exchange=resolved_exchange,
        interval=cfg.interval,
        from_date=cfg.from_date,
        to_date=cfg.to_date,
    )
    df = normalize(raw)

    # Auto-fallback: index/stock tokens may return no OHLCV data.
    # For options strategies, fetch a continuous price series from all futures
    # contracts that overlap the requested date range and stitch them together.
    if df.empty:
        all_futures = get_all_futures_tokens(cfg.symbol)
        if not all_futures:
            all_futures = []
            fut = get_nearest_futures_token(cfg.symbol)
            if fut:
                all_futures = [(fut[0], fut[1], fut[2], None)]

        if all_futures:
            from_dt = _parse_date(cfg.from_date) if cfg.from_date else None
            to_dt   = _parse_date(cfg.to_date)   if cfg.to_date   else None

            frames: list = []
            for fut_token, fut_symbol, fut_exchange, exp_date in all_futures:
                # Skip contracts that expire before our start date
                if from_dt and exp_date and exp_date < from_dt.date():
                    continue
                # Skip contracts that start after our end date (rough heuristic:
                # futures are listed ~6 months before expiry)
                raw_seg = fetch_historical(
                    symboltoken=fut_token,
                    exchange=fut_exchange,
                    interval=cfg.interval,
                    from_date=cfg.from_date,
                    to_date=cfg.to_date,
                )
                seg = normalize(raw_seg)
                if not seg.empty:
                    frames.append((exp_date, fut_symbol, seg))
                    logger.info(
                        "Continuous futures: fetched %d bars from %s",
                        len(seg), fut_symbol,
                    )

            if frames:
                # Stitch: sort by expiry, deduplicate timestamps (keep earliest contract)
                frames.sort(key=lambda x: x[0] if x[0] else date.max)
                seen_ts: set = set()
                merged_rows: list = []
                for _, sym, seg in frames:
                    for _, r in seg.iterrows():
                        ts = str(r["timestamp"])
                        if ts not in seen_ts:
                            seen_ts.add(ts)
                            merged_rows.append(r)
                if merged_rows:
                    df = pd.DataFrame(merged_rows).sort_values("timestamp").reset_index(drop=True)
                    resolved_symbol   = frames[0][1]  # first (nearest expiry) contract name
                    resolved_exchange = "NFO"
                    logger.info(
                        "Continuous futures: %d bars total from %d contracts",
                        len(df), len(frames),
                    )

    if df.empty:
        raise ValueError(
            f"No historical data returned for {cfg.symbol} ({cfg.exchange}, {cfg.interval}). "
            "Angel One does not provide historical data for index tokens (e.g. NIFTY/BANKNIFTY). "
            "Use an equity stock (e.g. RELIANCE on NSE), an ETF (e.g. NIFTYBEES on NSE), "
            "or a futures contract (e.g. NIFTY26MAY26FUT on NFO)."
        )

    logger.info("Backtest data: %d candles for %s %s", len(df), resolved_symbol, cfg.interval)

    # ── 3. Load strategy and generate signals ───────────────────────────────
    strategy_record = get_strategy(cfg.strategy_name)
    if strategy_record is None:
        raise KeyError(f"Strategy '{cfg.strategy_name}' not registered.")

    strategy = load_strategy(strategy_record["file_path"])
    df = strategy.generate(df)

    if "signal" not in df.columns:
        raise ValueError(
            "Strategy's generate() must return a DataFrame with a 'signal' column."
        )

    # ── 4. Simulate trades ──────────────────────────────────────────────────
    # Use resolved symbol/exchange so NIFTY→NIFTY28APR26FUT gives lot=65 not 1
    lot_size = get_lot_size(resolved_symbol, resolved_exchange)
    if lot_size <= 1:
        # Further fallback: look up by underlying name (handles equity options)
        lot_size = get_lot_size(cfg.symbol, cfg.exchange)
    logger.info("Lot size for %s (%s): %d", resolved_symbol, resolved_exchange, lot_size)
    trades   = _simulate(df, cfg, lot_size)

    # ── 5. Compute metrics ──────────────────────────────────────────────────
    summary, equity_curve = compute_metrics(trades, cfg.capital)

    return {
        "config": {
            "strategy": cfg.strategy_name,
            "symbol": cfg.symbol,
            "resolved_symbol": resolved_symbol,
            "exchange": cfg.exchange,
            "interval": cfg.interval,
            "from_date": cfg.from_date,
            "to_date": cfg.to_date,
            "actual_from": str(df["timestamp"].iloc[0]) if not df.empty else cfg.from_date,
            "actual_to":   str(df["timestamp"].iloc[-1]) if not df.empty else cfg.to_date,
            "capital": cfg.capital,
            "lot_size": lot_size,
        },
        "summary": summary,
        "equity_curve": equity_curve,
        "trades": [_trade_to_dict(t) for t in trades],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Simulation loop
# ─────────────────────────────────────────────────────────────────────────────

def _opt_fields(row: pd.Series) -> tuple[bool, float, float, str]:
    """Return (is_options, atm_premium, atm_strike, option_type) from a df row."""
    has = "atm_strike" in row.index and pd.notna(row.get("atm_strike"))
    if not has:
        return False, float(row["close"]), 0.0, ""
    return (
        True,
        float(row.get("atm_premium", 0.0)),
        float(row.get("atm_strike", 0.0)),
        str(row.get("option_type", "STRADDLE")),
    )


def _simulate(
    df: pd.DataFrame,
    cfg: BacktestConfig,
    lot_size: int,
) -> List[Trade]:
    trades: List[Trade] = []
    capital = cfg.capital
    position = 0          # +qty = long, -qty = short
    entry_price  = 0.0
    entry_time   = ""
    entry_strike = 0.0
    entry_opt_type = ""
    high_since_entry = 0.0
    low_since_entry  = 0.0

    for i, row in df.iterrows():
        signal = int(row.get("signal", 0))
        price  = float(row["close"])
        ts     = str(row["timestamp"])

        is_opt, trade_price_row, strike_row, opt_type_row = _opt_fields(row)

        # ── Check stop-loss / trailing stop-loss for open positions ─────────
        if position != 0:
            check_price = trade_price_row if is_opt else price
            exit_reason = _check_sl_tsl(
                position, check_price, entry_price,
                high_since_entry, low_since_entry,
                cfg.sl_pct, cfg.tsl_pct,
            )
            if exit_reason:
                pnl = _calc_pnl(position, entry_price, check_price)
                trades.append(Trade(
                    entry_time=entry_time, exit_time=ts,
                    symbol=cfg.symbol,
                    side="BUY" if position > 0 else "SELL",
                    entry_price=entry_price, exit_price=check_price,
                    quantity=abs(position), pnl=pnl, exit_reason=exit_reason,
                    atm_strike=entry_strike, option_type=entry_opt_type,
                ))
                capital += pnl
                position = 0

        # ── Update trailing reference prices ────────────────────────────────
        _trail = trade_price_row if is_opt else price
        if position > 0:
            high_since_entry = max(high_since_entry, _trail)
        elif position < 0:
            low_since_entry = min(low_since_entry, _trail)

        # ── Process strategy signal (BUYER ONLY — long positions only) ─────
        entry_signal = signal in (1, 2)
        exit_signal  = signal in (-1, -2)

        if entry_signal and position == 0:
            t_price = trade_price_row if is_opt else price
            qty = _position_size(capital, t_price, lot_size, cfg.position_size_pct)
            if qty > 0:
                position        = qty
                entry_price     = t_price
                entry_time      = ts
                entry_strike    = strike_row
                entry_opt_type  = opt_type_row
                high_since_entry = t_price

        elif exit_signal and position > 0:
            x_price = trade_price_row if is_opt else price
            pnl = _calc_pnl(position, entry_price, x_price)
            trades.append(Trade(
                entry_time=entry_time, exit_time=ts,
                symbol=cfg.symbol, side="BUY",
                entry_price=entry_price, exit_price=x_price,
                quantity=abs(position), pnl=pnl, exit_reason="SIGNAL",
                atm_strike=entry_strike, option_type=entry_opt_type,
            ))
            capital += pnl
            position = 0

    # ── Close any open position at end of data ───────────────────────────────
    if position != 0 and len(df) > 0:
        last_row = df.iloc[-1]
        ts  = str(last_row["timestamp"])
        is_opt_eod, eod_price, _, _ = _opt_fields(last_row)
        final_price = eod_price if is_opt_eod else float(last_row["close"])
        pnl = _calc_pnl(position, entry_price, final_price)
        trades.append(Trade(
            entry_time=entry_time, exit_time=ts,
            symbol=cfg.symbol,
            side="BUY" if position > 0 else "SELL",
            entry_price=entry_price, exit_price=final_price,
            quantity=abs(position), pnl=pnl, exit_reason="EOD",
            atm_strike=entry_strike, option_type=entry_opt_type,
        ))

    return trades


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _check_sl_tsl(
    position: int,
    price: float,
    entry_price: float,
    high_since_entry: float,
    low_since_entry: float,
    sl_pct: float,
    tsl_pct: float,
) -> Optional[str]:
    if position > 0:
        # Fixed SL
        if sl_pct > 0 and price <= entry_price * (1 - sl_pct / 100):
            return "SL"
        # Trailing SL
        if tsl_pct > 0 and price <= high_since_entry * (1 - tsl_pct / 100):
            return "TSL"
    elif position < 0:
        # Fixed SL
        if sl_pct > 0 and price >= entry_price * (1 + sl_pct / 100):
            return "SL"
        # Trailing SL
        if tsl_pct > 0 and price >= low_since_entry * (1 + tsl_pct / 100):
            return "TSL"
    return None


def _calc_pnl(position: int, entry_price: float, exit_price: float) -> float:
    return (exit_price - entry_price) * position


def _position_size(
    capital: float, price: float, lot_size: int, size_pct: float
) -> int:
    if price <= 0:
        return 0
    max_value = capital * size_pct / 100
    qty = int(max_value / price)
    # Round down to nearest lot
    if lot_size > 1:
        qty = (qty // lot_size) * lot_size
    return max(qty, 0)


def _parse_date(date_str: str) -> datetime:
    """Parse a date string in YYYY-MM-DD or YYYY-MM-DD HH:MM format."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return datetime.strptime(date_str.strip()[:10], "%Y-%m-%d")


def _fmt_ts(ts: str) -> str:
    """Convert ISO timestamp string to clean IST display (YYYY-MM-DD or YYYY-MM-DD HH:MM)."""
    try:
        dt = pd.Timestamp(ts)
        # Strip seconds and microseconds for cleanliness
        if dt.hour == 0 and dt.minute == 0:
            return dt.strftime("%Y-%m-%d")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def _trade_to_dict(t: Trade) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "entry_time":   _fmt_ts(t.entry_time),
        "exit_time":    _fmt_ts(t.exit_time),
        "symbol":       t.symbol,
        "side":         t.side,
        "entry_price":  round(t.entry_price, 2),
        "exit_price":   round(t.exit_price,  2),
        "quantity":     t.quantity,
        "pnl":          round(t.pnl, 2),
        "exit_reason":  t.exit_reason,
    }
    if t.atm_strike:
        d["atm_strike"]  = t.atm_strike
        d["option_type"] = t.option_type
    return d
