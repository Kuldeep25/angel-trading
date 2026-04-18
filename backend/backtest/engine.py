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
from backtest.charges import compute_charges
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
    instrument_type: str = "equity"   # equity | futures | options
    interval: str = "ONE_DAY"
    from_date: str = ""   # "YYYY-MM-DD HH:MM"
    to_date: str = ""     # "YYYY-MM-DD HH:MM"
    capital: float = 100_000.0
    sl_pct: float = 2.0    # stop-loss %          (0 = disabled)
    tsl_pct: float = 0.0   # trailing stop-loss %  (0 = disabled)
    target_pct: float = 0.0  # profit target %     (0 = disabled)
    position_size_pct: float = 95.0  # % of capital to deploy per trade
    slippage_pct: float = 0.05       # slippage % per fill (both entry and exit)
    position_sizing: str = "compounding"   # "compounding" | "fixed"
    # compounding: deploy position_size_pct of *running* capital each trade
    # fixed:       deploy position_size_pct of *initial* capital each trade (like Streak/AlgoTest)
    max_trades_per_day: int = 0      # 0 = unlimited (Streak default = unlimited unless set)
    intraday_squareoff: bool = True  # Force close all positions at 15:15 IST for intraday intervals
    allow_reentry: bool = True       # False = no new entry after an exit on the same day


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
    # For futures/options the user picks NFO/BFO/MCX as exchange for lot size
    # purposes, but the underlying price data lives on a *spot* exchange.
    # Resolve the data-fetch exchange separately.
    itype = cfg.instrument_type.lower()
    deriv_exchange = cfg.exchange.upper()   # NFO | BFO | MCX (or NSE/BSE for equity)
    data_exchange  = cfg.exchange
    if itype in ("futures", "options") and deriv_exchange in ("NFO", "BFO", "MCX"):
        if deriv_exchange == "BFO":
            # SENSEX / BANKEX / BSE-listed index derivatives → spot data on BSE
            data_exchange = "BSE"
        elif deriv_exchange == "MCX":
            # MCX commodities — data is fetched from MCX itself
            data_exchange = "MCX"
        else:  # NFO
            # NIFTY / BANKNIFTY / stocks → NSE spot first, then BSE
            data_exchange = "NSE"
            if get_token(cfg.symbol, "NSE") is None and get_token(cfg.symbol, "BSE") is not None:
                data_exchange = "BSE"

    token = get_token(cfg.symbol, data_exchange)
    resolved_symbol   = cfg.symbol
    resolved_exchange = data_exchange

    if token is None:
        raise ValueError(
            f"Symbol '{cfg.symbol}' not found on exchange '{data_exchange}'. "
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
        all_futures = get_all_futures_tokens(cfg.symbol, deriv_exchange if itype in ("futures", "options") else "")
        if not all_futures:
            all_futures = []
            fut = get_nearest_futures_token(cfg.symbol, deriv_exchange if itype in ("futures", "options") else "")
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
                    resolved_exchange = deriv_exchange if deriv_exchange in ("NFO", "BFO", "MCX") else "NFO"
                    logger.info(
                        "Continuous futures: %d bars total from %d contracts",
                        len(df), len(frames),
                    )

    if df.empty:
        if itype in ("futures", "options"):
            raise ValueError(
                f"No historical data returned for '{cfg.symbol}' ({cfg.interval}). "
                f"For options/futures backtest, the engine fetches data from continuous futures "
                f"contracts ({deriv_exchange}). Possible causes: "
                "(1) Angel One session expired — restart the backend, "
                "(2) the symbol has no active futures contracts, "
                "(3) the date range has no data for this interval "
                f"(FIVE_MINUTE max = 100 days, ONE_HOUR max = 400 days)."
            )
        raise ValueError(
            f"No historical data returned for {cfg.symbol} ({cfg.exchange}, {cfg.interval}). "
            "Angel One does not provide historical data for index tokens (e.g. NIFTY/BANKNIFTY). "
            "Use an equity stock (e.g. RELIANCE on NSE), an ETF (e.g. NIFTYBEES on NSE), "
            "or switch Instrument Type to Futures/Options."
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

    signal_counts = df["signal"].value_counts().to_dict()
    logger.info("Signal distribution after generate(): %s", signal_counts)

    # ── 4. Simulate trades ──────────────────────────────────────────────────
    # Lot size rules:
    #   equity  → always 1 (buy any number of shares)
    #   futures → NFO lot size of the underlying (e.g. NIFTY=65, RELIANCE=500)
    #   options → same NFO lot size as futures
    if itype == "equity":
        lot_size = 1
    else:
        # Look up lot size from the derivative exchange (NFO, BFO, or MCX).
        lot_size = get_lot_size(resolved_symbol, deriv_exchange)
        if lot_size <= 1:
            lot_size = get_lot_size(cfg.symbol, deriv_exchange)
        # Final NFO fallback for safety (covers stocks that only have NFO contracts)
        if lot_size <= 1 and deriv_exchange != "NFO":
            lot_size = get_lot_size(cfg.symbol, "NFO")
        if lot_size <= 1:
            lot_size = 1   # absolute fallback
    logger.info("Lot size for %s (%s) [%s]: %d", resolved_symbol, resolved_exchange, itype, lot_size)
    trades   = _simulate(df, cfg, lot_size)

    # ── 5. Compute metrics ──────────────────────────────────────────────────
    summary, equity_curve = compute_metrics(trades, cfg.capital, cfg.instrument_type)

    return {
        "config": {
            "strategy": cfg.strategy_name,
            "symbol": cfg.symbol,
            "resolved_symbol": resolved_symbol,
            "exchange": cfg.exchange,
            "instrument_type": cfg.instrument_type,
            "interval": cfg.interval,
            "from_date": cfg.from_date,
            "to_date": cfg.to_date,
            "actual_from": str(df["timestamp"].iloc[0]) if not df.empty else cfg.from_date,
            "actual_to":   str(df["timestamp"].iloc[-1]) if not df.empty else cfg.to_date,
            "capital": cfg.capital,
            "lot_size": lot_size,
            "slippage_pct": cfg.slippage_pct,
            "position_sizing": cfg.position_sizing,
            "max_trades_per_day": cfg.max_trades_per_day,
            "intraday_squareoff": cfg.intraday_squareoff,
            "allow_reentry": cfg.allow_reentry,
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


def _apply_slippage(price: float, side: str, slippage_pct: float) -> float:
    """
    Apply slippage to a fill price.
    BUY fills pay slightly more; SELL fills receive slightly less.
    """
    if slippage_pct <= 0:
        return price
    factor = 1 + slippage_pct / 100 if side == "BUY" else 1 - slippage_pct / 100
    return round(price * factor, 2)


def _opt_premium_at_spot(
    row: pd.Series,
    spot: float,
    opt_type: str,
    dte: int = 7,
) -> float:
    """
    Estimate option premium at a given spot price using BSM.
    Used to approximate intrabar option high/low from underlying high/low.
    """
    try:
        from backtest.option_pricer import (
            bs_atm_premium, _hist_vol_series, RISK_FREE_RATE, MIN_T
        )
        import math
        # Use the bar's own sigma from hist_vol column if available
        sigma = float(row.get("hist_vol", 0.20))
        if math.isnan(sigma) or sigma <= 0:
            sigma = 0.20
        strike = float(row.get("atm_strike", spot))
        T = max(dte / 365.0, MIN_T)
        return bs_atm_premium(spot, strike, T, opt_type, RISK_FREE_RATE, sigma)
    except Exception:
        # Fallback: scale from close premium by ratio of spots
        close_spot    = float(row["close"])
        close_premium = float(row.get("atm_premium", close_spot))
        if close_spot > 0:
            return close_premium * (spot / close_spot)
        return close_premium


def _intrabar_exit(
    row: pd.Series,
    position: int,
    entry_price: float,
    high_since_entry: float,
    low_since_entry: float,
    sl_pct: float,
    tsl_pct: float,
    target_pct: float,
    is_opt: bool,
    opt_type: str,
    dte: int,
) -> tuple[Optional[str], float]:
    """
    Check if SL / TSL / Target was breached *within* a candle using OHLC.

    Returns (exit_reason, fill_price) or (None, 0.0) if no breach.

    Rules (same as Streak / AlgoTest):
    • For equity/futures: compare candle high & low directly
    • For options:        estimate option high & low via BSM at underlying extremes
    • If both SL and Target hit same candle → SL wins (pessimistic / conservative)
    • Fill price = exact SL/Target price (not candle close)
    """
    bar_high = float(row.get("high", row["close"]))
    bar_low  = float(row.get("low",  row["close"]))

    if is_opt:
        # For long options the option price moves with the underlying.
        # CE: gains when spot rises, loses when spot falls → worst from spot_low
        # PE: gains when spot falls, loses when spot rises → worst from spot_high
        # STRADDLE: combined premium; approximate with the larger of the two extremes
        ot = opt_type.upper()
        if ot in ("CE", "CALL"):
            opt_bar_high = _opt_premium_at_spot(row, bar_high, "CE", dte)
            opt_bar_low  = _opt_premium_at_spot(row, bar_low,  "CE", dte)
        elif ot in ("PE", "PUT"):
            opt_bar_high = _opt_premium_at_spot(row, bar_low,  "PE", dte)  # low spot → high PE
            opt_bar_low  = _opt_premium_at_spot(row, bar_high, "PE", dte)  # high spot → low PE
        else:  # STRADDLE
            ce_high = _opt_premium_at_spot(row, bar_high, "CE", dte)
            ce_low  = _opt_premium_at_spot(row, bar_low,  "CE", dte)
            pe_high = _opt_premium_at_spot(row, bar_low,  "PE", dte)
            pe_low  = _opt_premium_at_spot(row, bar_high, "PE", dte)
            opt_bar_high = ce_high + pe_high
            opt_bar_low  = ce_low  + pe_low
        bar_high, bar_low = opt_bar_high, opt_bar_low

    if position > 0:   # long
        sl_price  = entry_price * (1 - sl_pct / 100)   if sl_pct     > 0 else None
        tsl_price = high_since_entry * (1 - tsl_pct / 100) if tsl_pct > 0 else None
        tgt_price = entry_price * (1 + target_pct / 100) if target_pct > 0 else None

        hit_sl  = sl_price  is not None and bar_low  <= sl_price
        hit_tsl = tsl_price is not None and bar_low  <= tsl_price
        hit_tgt = tgt_price is not None and bar_high >= tgt_price

        # Pessimistic: if SL/TSL and Target both hit same candle → SL first
        if (hit_sl or hit_tsl) and hit_tgt:
            # Use whichever SL is tighter
            if hit_sl and hit_tsl:
                return ("SL", sl_price) if sl_price >= tsl_price else ("TSL", tsl_price)
            return ("SL", sl_price) if hit_sl else ("TSL", tsl_price)
        if hit_tgt:
            return ("TARGET", tgt_price)
        if hit_sl and hit_tsl:
            return ("SL", sl_price) if sl_price >= tsl_price else ("TSL", tsl_price)
        if hit_sl:
            return ("SL", sl_price)
        if hit_tsl:
            return ("TSL", tsl_price)

    elif position < 0:   # short (futures / equity short)
        sl_price  = entry_price * (1 + sl_pct / 100)   if sl_pct     > 0 else None
        tsl_price = low_since_entry  * (1 + tsl_pct / 100) if tsl_pct > 0 else None
        tgt_price = entry_price * (1 - target_pct / 100) if target_pct > 0 else None

        hit_sl  = sl_price  is not None and bar_high >= sl_price
        hit_tsl = tsl_price is not None and bar_high >= tsl_price
        hit_tgt = tgt_price is not None and bar_low  <= tgt_price

        if (hit_sl or hit_tsl) and hit_tgt:
            if hit_sl and hit_tsl:
                return ("SL", sl_price) if sl_price <= tsl_price else ("TSL", tsl_price)
            return ("SL", sl_price) if hit_sl else ("TSL", tsl_price)
        if hit_tgt:
            return ("TARGET", tgt_price)
        if hit_sl and hit_tsl:
            return ("SL", sl_price) if sl_price <= tsl_price else ("TSL", tsl_price)
        if hit_sl:
            return ("SL", sl_price)
        if hit_tsl:
            return ("TSL", tsl_price)

    return (None, 0.0)


_INTRADAY_INTERVALS = {
    "ONE_MINUTE", "THREE_MINUTE", "FIVE_MINUTE", "TEN_MINUTE",
    "FIFTEEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR",
}
_SQUAREOFF_HOUR   = 15
_SQUAREOFF_MINUTE = 15   # 15:15 IST — matches Streak / Zerodha / AlgoTest default


def _bar_time(ts: str) -> Optional[tuple]:
    """Extract (date_str, hour, minute) from a timestamp string, or None."""
    try:
        # Handles "2024-06-01 09:15:00", "2024-06-01T09:15:00", "2024-06-01 09:15"
        clean = ts.replace("T", " ").split(".")[0].strip()
        parts = clean.split(" ")
        date_part = parts[0]
        if len(parts) > 1:
            hm = parts[1].split(":")
            return date_part, int(hm[0]), int(hm[1])
        return date_part, None, None
    except Exception:
        return None


def _is_squareoff_bar(ts: str) -> bool:
    """True if this bar is at or after 15:15 IST."""
    t = _bar_time(ts)
    if not t or t[1] is None:
        return False
    _, h, m = t
    return (h > _SQUAREOFF_HOUR) or (h == _SQUAREOFF_HOUR and m >= _SQUAREOFF_MINUTE)


def _simulate(
    df: pd.DataFrame,
    cfg: BacktestConfig,
    lot_size: int,
) -> List[Trade]:
    """
    Simulate trades matching Streak / AlgoTest methodology:

    1. NEXT-CANDLE ENTRY    — signal on candle[i].close → fill at candle[i+1].open
    2. INTRABAR SL/TARGET   — OHLC-based, fill at exact SL/Target price
    3. PESSIMISTIC RULE     — SL wins if both SL and Target hit same candle
    4. SLIPPAGE             — applied to every entry and exit fill
    5. INTRADAY SQUARE-OFF  — all positions force-closed at 15:15 IST for
                              intraday intervals (matches Streak/Zerodha behaviour)
    6. FIXED POSITION SIZE  — option to use initial capital for sizing (not running)
    7. MAX TRADES PER DAY   — hard cap; no new entries once limit hit for that day
    8. NO RE-ENTRY          — optional: skip new entries after an exit on same day
    """
    trades: List[Trade] = []
    capital          = cfg.capital
    initial_capital  = cfg.capital   # for fixed sizing mode
    position         = 0
    entry_price      = 0.0
    entry_time       = ""
    entry_strike     = 0.0
    entry_opt_type   = ""
    entry_dte        = 7
    high_since_entry = 0.0
    low_since_entry  = 0.0

    # Per-day trackers
    current_day:       str = ""
    trades_today:      int = 0
    exited_today:      bool = False

    is_intraday = cfg.interval.upper() in _INTRADAY_INTERVALS
    n = len(df)

    def _close_position(ts: str, price: float, reason: str) -> None:
        nonlocal position, capital, high_since_entry, low_since_entry
        side_exit = "SELL" if position > 0 else "BUY"
        fill_px = _apply_slippage(price, side_exit, cfg.slippage_pct)
        pnl = _calc_pnl(position, entry_price, fill_px)
        trades.append(Trade(
            entry_time=entry_time, exit_time=ts,
            symbol=cfg.symbol,
            side="BUY" if position > 0 else "SELL",
            entry_price=entry_price, exit_price=fill_px,
            quantity=abs(position), pnl=pnl, exit_reason=reason,
            atm_strike=entry_strike, option_type=entry_opt_type,
        ))
        capital += pnl
        position = 0
        high_since_entry = 0.0
        low_since_entry  = 0.0

    for i in range(n):
        row    = df.iloc[i]
        signal = int(row.get("signal", 0))
        close  = float(row["close"])
        ts     = str(row["timestamp"])

        is_opt, premium_close, strike_row, opt_type_row = _opt_fields(row)
        if cfg.instrument_type.lower() == "equity":
            is_opt, premium_close, strike_row, opt_type_row = False, close, 0.0, ""

        trade_price = premium_close if is_opt else close
        dte_row     = int(row.get("dte", 7)) if is_opt else 0

        # ── Day boundary reset ──────────────────────────────────────────────
        bar_info = _bar_time(ts)
        day = bar_info[0] if bar_info else ts[:10]
        if day != current_day:
            current_day  = day
            trades_today = 0
            exited_today = False

        # ── 1. Intraday square-off at 15:15 ────────────────────────────────
        if position != 0 and is_intraday and _is_squareoff_bar(ts):
            _close_position(ts, trade_price, "SQUAREOFF")
            exited_today = True
            trades_today += 1
            continue   # skip signal processing for this bar

        # ── 2. Intrabar SL / Target check for open positions ───────────────
        if position != 0:
            reason, fill_px_raw = _intrabar_exit(
                row, position, entry_price,
                high_since_entry, low_since_entry,
                cfg.sl_pct, cfg.tsl_pct, cfg.target_pct,
                is_opt, entry_opt_type, entry_dte,
            )
            if reason:
                _close_position(ts, fill_px_raw, reason)
                exited_today = True
                trades_today += 1

        # ── 3. Update trailing high / low ──────────────────────────────────
        if position != 0:
            ref = trade_price
            if position > 0:
                high_since_entry = max(high_since_entry, ref)
            else:
                low_since_entry  = min(low_since_entry,  ref)

        # ── 4. Strategy exit signal (close-based, if not already stopped) ──
        if signal in (-1, -2) and position > 0:
            _close_position(ts, trade_price, "SIGNAL")
            exited_today = True
            trades_today += 1

        # ── 5. Entry signal → fill at NEXT candle's open ───────────────────
        can_enter = (
            position == 0
            and signal in (1, 2)
            and (cfg.max_trades_per_day == 0 or trades_today < cfg.max_trades_per_day)
            and (cfg.allow_reentry or not exited_today)
            and (not is_intraday or not _is_squareoff_bar(ts))  # don't enter at/after 15:15
        )
        if can_enter:
            if i + 1 < n:
                next_row  = df.iloc[i + 1]
                next_ts   = str(next_row["timestamp"])
                # Don't enter if next bar is already a square-off bar
                if is_intraday and _is_squareoff_bar(next_ts):
                    pass
                else:
                    next_open = float(next_row.get("open", next_row["close"]))
                    if is_opt:
                        next_is_opt, next_prem, _, _ = _opt_fields(next_row)
                        fill_raw = next_prem if next_is_opt else next_open
                    else:
                        fill_raw = next_open

                    fill_px = _apply_slippage(fill_raw, "BUY", cfg.slippage_pct)
                    # Fixed sizing: always size from initial capital (like Streak default)
                    size_capital = initial_capital if cfg.position_sizing == "fixed" else capital
                    qty = _position_size(size_capital, fill_px, lot_size, cfg.position_size_pct)
                    if qty > 0:
                        position         = qty
                        entry_price      = fill_px
                        entry_time       = next_ts
                        entry_strike     = strike_row
                        entry_opt_type   = opt_type_row
                        entry_dte        = dte_row
                        high_since_entry = fill_px
                        low_since_entry  = fill_px

    # ── 6. Close any open position at end of data ──────────────────────────
    if position != 0 and n > 0:
        last_row = df.iloc[-1]
        ts = str(last_row["timestamp"])
        is_opt_eod, eod_price, _, _ = _opt_fields(last_row)
        raw_price = eod_price if is_opt_eod else float(last_row["close"])
        _close_position(ts, raw_price, "EOD")

    return trades


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

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
        "charges":      round(t.charges, 2),
        "net_pnl":      round(t.net_pnl, 2),
        "exit_reason":  t.exit_reason,
    }
    if t.atm_strike:
        d["atm_strike"]  = t.atm_strike
        d["option_type"] = t.option_type
    return d
