"""
Black-Scholes ATM option pricer for backtesting.

Pricing priority (most accurate first):
  1. NSE bhavcopy daily close       — exact real market price for daily bars
  2. BSM with real IV from bhavcopy — real-IV-calibrated model for intraday bars
     (IV is back-calculated from the day's bhavcopy close using Newton-Raphson)
  3. Live snapshot LTP              — from Angel One 5-min collector
  4. BSM with HV20                  — pure fallback when no real data exists

The key insight: even for intraday intervals, using the *day's real IV* (from
bhavcopy) in the BSM model is far more accurate than historical vol (HV20)
because it captures the actual market-implied vol for that specific day —
including event risk, expiry proximity, IV crush, etc.

Usage (from a strategy's generate()):
    from backtest.option_pricer import add_bsm_premium
    df = add_bsm_premium(df, option_type="CE", symbol="NIFTY")
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────
RISK_FREE_RATE: float = 0.065    # RBI repo rate proxy (annualised)
MIN_IV:         float = 0.05     # 5%  — floor (very low vol is still possible)
MAX_IV:         float = 3.00     # 300% — cap for expiry-day spikes
HV_WINDOW:      int   = 20       # rolling bars for historical vol
MIN_T:          float = 0.5 / 365  # half a day minimum (avoids singularity)

# Strike step sizes (option strikes are only available at these multiples)
# Source: NSE contract specifications
STRIKE_STEP: dict[str, int] = {
    "NIFTY":       50,
    "BANKNIFTY":  100,
    "MIDCPNIFTY":  25,
    "FINNIFTY":    50,
    "SENSEX":     100,
    "BANKEX":     100,
    "DEFAULT":     50,   # safe fallback for most index derivatives
}

# Expiry weekday per index/product type  (0=Mon … 6=Sun)
EXPIRY_WEEKDAY: dict[str, int] = {
    "NIFTY":        3,   # Thursday
    "BANKNIFTY":    2,   # Wednesday
    "MIDCPNIFTY":   0,   # Monday
    "FINNIFTY":     1,   # Tuesday
    "SENSEX":       1,   # Tuesday
    "BANKEX":       0,   # Monday
    "DEFAULT":      3,   # Thursday (most common)
}


# ─────────────────────────────────────────────────────────────────────────────
#  Black-Scholes core
# ─────────────────────────────────────────────────────────────────────────────

def _ncdf(x: float) -> float:
    """Cumulative standard normal distribution — pure Python, no scipy."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes European call price."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)


def bs_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes European put price."""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def _bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """BSM vega: ∂Price/∂σ = S * sqrt(T) * N'(d1).  Same for calls and puts."""
    if T <= 0 or sigma <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    return S * sqrt_T * _npdf(d1)


def bs_atm_premium(
    spot: float,
    strike: float,
    T: float,
    option_type: str = "CE",
    r: float = RISK_FREE_RATE,
    sigma: float = 0.20,
) -> float:
    """BSM premium for a single ATM option (CE / PE / STRADDLE)."""
    ot = option_type.upper()
    if ot in ("CE", "CALL"):
        return bs_call(spot, strike, T, r, sigma)
    if ot in ("PE", "PUT"):
        return bs_put(spot, strike, T, r, sigma)
    return bs_call(spot, strike, T, r, sigma) + bs_put(spot, strike, T, r, sigma)


# ─────────────────────────────────────────────────────────────────────────────
#  Implied Volatility — Newton-Raphson BSM inversion
# ─────────────────────────────────────────────────────────────────────────────

def implied_vol(
    market_price: float,
    S: float,
    K: float,
    T: float,
    option_type: str = "CE",
    r: float = RISK_FREE_RATE,
    tol: float = 1e-5,
    max_iter: int = 100,
    sigma0: float = 0.30,
) -> Optional[float]:
    """
    Back-calculate implied volatility from a market option price using
    Newton-Raphson iteration.

    Returns annualised IV (e.g. 0.18 for 18%), or None if no convergence.

    This is the key function that lets us use *real market IV* from bhavcopy
    data when pricing intraday bars — much more accurate than HV20.
    """
    if market_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None

    # Intrinsic value bounds check
    ot = option_type.upper()
    intrinsic = max(S - K, 0.0) if ot in ("CE", "CALL") else max(K - S, 0.0)
    if ot == "STRADDLE":
        intrinsic = 0.0
    if market_price < intrinsic:
        return None   # arbitrage-free violation — bad data

    sigma = sigma0
    for _ in range(max_iter):
        if ot in ("CE", "CALL"):
            price = bs_call(S, K, T, r, sigma)
        elif ot in ("PE", "PUT"):
            price = bs_put(S, K, T, r, sigma)
        else:
            price = bs_call(S, K, T, r, sigma) + bs_put(S, K, T, r, sigma)

        diff = price - market_price
        if abs(diff) < tol:
            return max(min(sigma, MAX_IV), MIN_IV)

        vega = _bs_vega(S, K, T, r, sigma)
        if abs(vega) < 1e-8:
            # Vega too small — switch to bisection step
            sigma = sigma * 0.5 + 0.01
            continue

        sigma -= diff / vega
        sigma = max(min(sigma, MAX_IV), MIN_IV)

    # Return best estimate even if tolerance not met
    return max(min(sigma, MAX_IV), MIN_IV)


# ─────────────────────────────────────────────────────────────────────────────
#  Expiry calendar helpers
# ─────────────────────────────────────────────────────────────────────────────

def _expiry_weekday(symbol: str) -> int:
    """Return the weekday (0=Mon … 6=Sun) on which the weekly expiry falls."""
    sym = symbol.upper()
    for key, wd in EXPIRY_WEEKDAY.items():
        if key in sym:
            return wd
    return EXPIRY_WEEKDAY["DEFAULT"]


def atm_strike_for(spot: float, symbol: str = "") -> int:
    """
    Round spot price to the nearest valid ATM strike for the given symbol.

    Real NSE option strikes are multiples of a fixed step (NIFTY=50,
    BANKNIFTY=100, etc.).  Using raw spot as the strike causes bhavcopy
    lookups to fail and BSM to be slightly off-the-money.

    Example:
        atm_strike_for(22456.78, "NIFTY")  → 22450
        atm_strike_for(51234.00, "BANKNIFTY") → 51200
    """
    sym = symbol.upper()
    step = STRIKE_STEP.get("DEFAULT", 50)
    for key, s in STRIKE_STEP.items():
        if key != "DEFAULT" and key in sym:
            step = s
            break
    return int(round(spot / step) * step)


def _days_to_weekly_expiry(trade_date: date, expiry_wd: int) -> int:
    """
    Number of calendar days from trade_date to the next (or same-day) weekly
    expiry.  Returns at least 1 to avoid zero-T singularities.
    """
    days_ahead = (expiry_wd - trade_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7   # already on expiry day → next week's expiry
    return max(days_ahead, 1)


def _dte_from_expiry(trade_date: date, expiry_iso: str) -> int:
    """
    Compute exact DTE from real expiry date string (YYYY-MM-DD).
    Returns at least 1.
    """
    try:
        exp = date.fromisoformat(expiry_iso)
        dte = (exp - trade_date).days
        return max(dte, 1)
    except Exception:
        return 7


# ─────────────────────────────────────────────────────────────────────────────
#  Historical volatility (HV20) — fallback only
# ─────────────────────────────────────────────────────────────────────────────

def _hist_vol_series(close: pd.Series, window: int = HV_WINDOW) -> pd.Series:
    """
    Annualised historical volatility from rolling log-returns.
    Used only when no real bhavcopy IV is available.
    """
    log_ret = np.log(close / close.shift(1))
    hv = log_ret.rolling(window).std() * math.sqrt(252)
    hv = hv.clip(lower=MIN_IV, upper=MAX_IV)
    hv = hv.bfill().ffill().fillna(0.20)
    return hv


# ─────────────────────────────────────────────────────────────────────────────
#  IV resolution — real IV cache with fallback
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_iv(
    symbol: str,
    date_str: str,       # "YYYY-MM-DD"
    expiry: str,         # "YYYY-MM-DD" from bhavcopy
    spot: float,
    strike: float,
    T: float,
    option_type: str,    # "CE" | "PE" | "STRADDLE"
    hv_fallback: float,  # HV20 to use if real IV fails
    bhavcopy_available: bool,
) -> tuple[float, str]:
    """
    Resolve the best available IV for a given date/symbol.

    Returns (iv, source) where source is one of:
        "iv_bhavcopy"   — back-calculated from real bhavcopy close
        "hv20"          — historical vol fallback
    """
    if not bhavcopy_available or not symbol:
        return hv_fallback, "hv20"

    try:
        from options.bhavcopy_db import get_atm_iv, store_atm_iv, get_atm_ohlc

        ot = option_type.upper()
        # CE and PE both resolve to CE IV for the cache key (simpler, similar magnitude)
        cache_ot = "CE" if ot in ("CE", "CALL", "STRADDLE") else "PE"

        # Check persistent IV cache first
        cached_iv = get_atm_iv(symbol.upper(), date_str, expiry, cache_ot)
        if cached_iv is not None:
            return float(cached_iv), "iv_bhavcopy"

        # Not cached yet — compute from bhavcopy close
        row_ohlc = get_atm_ohlc(symbol.upper(), date_str, expiry, spot, cache_ot)
        if row_ohlc and row_ohlc.get("close") and float(row_ohlc["close"]) > 0:
            market_price = float(row_ohlc["close"])
            real_strike  = float(row_ohlc.get("strike", strike))
            # For straddle: use CE+PE combined
            if ot == "STRADDLE":
                from options.bhavcopy_db import get_atm_ohlc as _ohlc
                ce_row = _ohlc(symbol.upper(), date_str, expiry, spot, "CE")
                pe_row = _ohlc(symbol.upper(), date_str, expiry, spot, "PE")
                if ce_row and pe_row:
                    market_price = (ce_row.get("close") or 0) + (pe_row.get("close") or 0)
                    real_strike  = float(ce_row.get("strike", strike))

            iv = implied_vol(market_price, spot, real_strike, T, cache_ot, RISK_FREE_RATE)
            if iv is not None:
                store_atm_iv(symbol.upper(), date_str, expiry, iv, cache_ot)
                return iv, "iv_bhavcopy"

    except Exception:
        pass

    return hv_fallback, "hv20"


# ─────────────────────────────────────────────────────────────────────────────
#  Main entry-point used by strategies
# ─────────────────────────────────────────────────────────────────────────────

def add_bsm_premium(
    df: pd.DataFrame,
    option_type: str = "CE",
    symbol: str = "",
    r: float = RISK_FREE_RATE,
    hv_window: int = HV_WINDOW,
) -> pd.DataFrame:
    """
    Add option `atm_premium` column to the dataframe.

    4-tier pricing priority:
      1. Bhavcopy daily close        — exact real close for daily bars
      2. BSM with real IV (bhavcopy) — real-IV model for intraday bars
      3. Live snapshot LTP           — from Angel One 5-min collector
      4. BSM with HV20               — pure fallback, no real data

    Adds / overwrites columns:
        atm_premium     – option premium (real or BSM)
        hist_vol        – IV/HV used (NaN when tier-1 exact close used)
        dte             – days-to-expiry (real from bhavcopy when available)
        premium_source  – "bhavcopy" | "bsm_real_iv" | "snapshot" | "bsm_hv"
    """
    try:
        from options.snapshot_db import get_nearest_premium, get_straddle_premium
        _snapshot_available = True
    except Exception:
        _snapshot_available = False

    try:
        from options.bhavcopy_db import get_atm_ohlc, get_nearest_expiry
        _bhavcopy_available = True
    except Exception:
        _bhavcopy_available = False

    df = df.copy()
    expiry_wd = _expiry_weekday(symbol)
    hv_series = _hist_vol_series(df["close"], hv_window)
    ot_upper  = option_type.upper()

    premiums: list[float] = []
    dtes:     list[int]   = []
    sources:  list[str]   = []
    hv_out:   list[float] = []

    # Per-bar IV cache: (symbol, date, expiry, ot) → iv
    # Avoids repeating Newton-Raphson for each intraday bar in the same day
    _iv_day_cache: dict[str, tuple[float, str, str]] = {}
    # key = "SYM|DATE" → (iv, iv_source, expiry)

    for idx, row in df.iterrows():
        spot   = float(row["close"])
        # Auto-round ATM strike if not pre-computed by the strategy.
        # Real NSE option strikes are multiples of STRIKE_STEP, so using raw
        # spot fails bhavcopy DB lookups and makes BSM slightly off-the-money.
        if "atm_strike" in row.index and pd.notna(row["atm_strike"]) and float(row["atm_strike"]) > 0:
            strike = float(row["atm_strike"])
        else:
            strike = float(atm_strike_for(spot, symbol))
        i_loc  = idx if isinstance(idx, int) else df.index.get_loc(idx)
        hv     = float(hv_series.iloc[i_loc])

        try:
            ts       = pd.to_datetime(row["timestamp"])
            date_str = ts.date().isoformat()          # "YYYY-MM-DD"
            ts_iso   = ts.isoformat(timespec="seconds")
        except Exception:
            date_str = ""
            ts_iso   = ""

        # ── Resolve expiry & DTE ───────────────────────────────────────────
        real_expiry: Optional[str] = None
        if _bhavcopy_available and date_str and symbol:
            try:
                real_expiry = get_nearest_expiry(symbol.upper(), date_str)
            except Exception:
                pass

        if real_expiry:
            dte = _dte_from_expiry(ts.date(), real_expiry) if date_str else 7
        else:
            dte = _days_to_weekly_expiry(ts.date(), expiry_wd) if date_str else 7

        T = max(dte / 365.0, MIN_T)

        # ── Tier 1: Bhavcopy daily close (exact, for daily bars) ──────────
        real_ltp: Optional[float] = None
        if _bhavcopy_available and date_str and real_expiry:
            try:
                row_ohlc = get_atm_ohlc(symbol.upper(), date_str, real_expiry, spot, ot_upper)
                if row_ohlc and row_ohlc.get("close"):
                    real_ltp = float(row_ohlc["close"])
            except Exception:
                pass

        if real_ltp is not None:
            premiums.append(round(real_ltp, 2))
            sources.append("bhavcopy")
            hv_out.append(float("nan"))
            dtes.append(dte)
            continue

        # ── Tier 2: BSM with real IV back-calculated from bhavcopy ────────
        # Use per-day IV cache to avoid repeated Newton-Raphson
        cache_key = f"{symbol}|{date_str}"
        if cache_key in _iv_day_cache:
            real_iv, iv_source, cached_expiry = _iv_day_cache[cache_key]
        else:
            expiry_for_iv = real_expiry or ""
            real_iv, iv_source = _resolve_iv(
                symbol, date_str, expiry_for_iv, spot, strike, T,
                ot_upper, hv, _bhavcopy_available,
            )
            _iv_day_cache[cache_key] = (real_iv, iv_source, expiry_for_iv)

        if iv_source == "iv_bhavcopy":
            premium = bs_atm_premium(spot, strike, T, ot_upper, r, real_iv)
            premiums.append(round(premium, 2))
            sources.append("bsm_real_iv")
            hv_out.append(real_iv)
            dtes.append(dte)
            continue

        # ── Tier 3: Live snapshot LTP (intraday collector) ─────────────────
        if _snapshot_available and ts_iso and symbol:
            try:
                if ot_upper == "STRADDLE":
                    snap_ltp = get_straddle_premium(symbol.upper(), strike, ts_iso)
                else:
                    snap_ltp = get_nearest_premium(symbol.upper(), strike, ot_upper, ts_iso)
                if snap_ltp is not None:
                    premiums.append(round(snap_ltp, 2))
                    sources.append("snapshot")
                    hv_out.append(float("nan"))
                    dtes.append(dte)
                    continue
            except Exception:
                pass

        # ── Tier 4: BSM with HV20 (pure fallback) ─────────────────────────
        premium = bs_atm_premium(spot, strike, T, ot_upper, r, hv)
        premiums.append(round(premium, 2))
        sources.append("bsm_hv")
        hv_out.append(hv)
        dtes.append(dte)

    df["atm_premium"]    = premiums
    df["hist_vol"]       = hv_out
    df["dte"]            = dtes
    df["premium_source"] = sources
    # Ensure atm_strike column always exists with the rounded value
    if "atm_strike" not in df.columns:
        df["atm_strike"] = [atm_strike_for(float(r["close"]), symbol) for _, r in df.iterrows()]
    return df
