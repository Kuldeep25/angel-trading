"""
Options Supertrend Buyer Strategy
───────────────────────────────────
Uses the Supertrend indicator on the underlying price (index futures or
stock) to decide DIRECTION, then buys the ATM option in that direction.

  Bullish supertrend  → buy ATM CE (call)
  Bearish supertrend  → buy ATM PE (put)

Exit when the trend flips (opposite signal) or SL/TSL fires.

Signal conventions (compatible with buyer-only engine):
  signal = 1   → Enter (buy CE or PE depending on direction)
  signal = -1  → Exit current option

Extra columns added by this strategy (picked up by the engine):
  atm_strike   – dynamically computed nearest strike
  atm_premium  – estimated option premium (premium_pct % of strike)
  option_type  – "CE" or "PE"

Works for:
  • Index options (NIFTY, BANKNIFTY) — strike gaps 50 / 100
  • Stock options (any stock) — strike gap auto-scaled from price
  • Daily or intraday bars

Parameters
───────────
atr_period   : int   ATR look-back (default 10)
multiplier   : float Supertrend band multiplier (default 3.0)
premium_pct  : float Estimated ATM premium as % of strike (default 1.5%)
strike_gap   : int|None  Override strike interval; None = auto-detect
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.option_pricer import add_bsm_premium


# ─────────────────────────────────────────────────────────────────────────────
#  Strike-gap helpers
# ─────────────────────────────────────────────────────────────────────────────

def _auto_strike_gap(median_price: float) -> int:
    """
    Auto-detect the standard exchange strike interval from the price level.

    Index products:
        BANKNIFTY ≥ 40 000  → 100
        NIFTY     ≥  8 000  → 50

    Stock options (SEBI-prescribed intervals, approximate):
        price < 250          → 5
        250  ≤ price < 500   → 10
        500  ≤ price < 1000  → 20
        1000 ≤ price < 2500  → 50
        2500 ≤ price < 5000  → 100
        5000+                → 100
    """
    if median_price >= 40_000:
        return 100          # BANKNIFTY
    if median_price >= 8_000:
        return 50           # NIFTY
    if median_price >= 5_000:
        return 100
    if median_price >= 2_500:
        return 100
    if median_price >= 1_000:
        return 50
    if median_price >= 500:
        return 20
    if median_price >= 250:
        return 10
    return 5


def _atm_strike(spot: float, gap: int) -> float:
    """Round spot to the nearest strike interval."""
    return float(round(spot / gap) * gap)


# ─────────────────────────────────────────────────────────────────────────────
#  Supertrend calculation
# ─────────────────────────────────────────────────────────────────────────────

def _supertrend(df: pd.DataFrame, period: int, multiplier: float) -> pd.Series:
    """
    Returns a Series of +1 (bullish) / -1 (bearish) matching df's index.

    Uses Wilder's ATR.  Requires columns: high, low, close.
    """
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder ATR
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    trend     = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    for i in range(len(df)):
        if i < period:
            trend.iloc[i]     = lower_band.iloc[i]
            direction.iloc[i] = 1
            continue

        prev_trend = trend.iloc[i - 1]
        prev_dir   = direction.iloc[i - 1]

        # Compute bands with carry-forward logic
        ub = upper_band.iloc[i]
        lb = lower_band.iloc[i]

        if prev_dir == 1:
            lb = max(lb, prev_trend)
        else:
            ub = min(ub, prev_trend)

        c = close.iloc[i]
        if prev_dir == -1 and c > ub:
            direction.iloc[i] = 1
            trend.iloc[i]     = lb
        elif prev_dir == 1 and c < lb:
            direction.iloc[i] = -1
            trend.iloc[i]     = ub
        else:
            direction.iloc[i] = prev_dir
            trend.iloc[i]     = lb if prev_dir == 1 else ub

    return direction


# ─────────────────────────────────────────────────────────────────────────────
#  Strategy class
# ─────────────────────────────────────────────────────────────────────────────

class Strategy:
    """
    Supertrend-based options buyer.

    When supertrend flips bullish  → buy ATM CE (signal = 1)
    When supertrend flips bearish  → buy ATM PE (signal = 1)
    When trend reverses            → exit current option (signal = -1)
    """

    def __init__(
        self,
        atr_period:  int   = 10,
        multiplier:  float = 3.0,
        strike_gap:  int | None = None,
    ) -> None:
        self.atr_period  = atr_period
        self.multiplier  = multiplier
        self.strike_gap  = strike_gap

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add signal / atm_strike / atm_premium / option_type columns.

        Parameters
        ----------
        df : normalised OHLCV of the underlying (spot, futures, or stock).
             Columns: timestamp, open, high, low, close, volume
        """
        df = df.copy().reset_index(drop=True)

        # Auto-detect strike gap once from the median price
        gap = self.strike_gap or _auto_strike_gap(float(df["close"].median()))

        # Supertrend direction (+1 bullish, -1 bearish)
        direction = _supertrend(df, self.atr_period, self.multiplier)

        # ATM strike per bar
        df["atm_strike"]  = df["close"].apply(lambda c: _atm_strike(c, gap))
        # Map direction → option_type first (needed by BSM pricer for CE vs PE)
        df["option_type"] = direction.map({1: "CE", -1: "PE"}).fillna("CE")
        # BSM pricing — one pass; uses the dominant direction (CE/PE varies per-bar
        # but premium magnitude is similar for ATM calls vs puts, so we price as CE)
        df = add_bsm_premium(df, option_type="CE")

        # ── Generate signals on direction flips ───────────────────────────
        df["signal"] = 0

        prev_dir = direction.iloc[0] if len(direction) else 1

        for i in range(1, len(df)):
            cur_dir = direction.iloc[i]
            if cur_dir != prev_dir:
                # Exit previous trade on same bar
                df.loc[df.index[i], "signal"] = -1
                # On next bar enter new trade (avoid same-bar entry/exit conflict)
                if i + 1 < len(df):
                    df.loc[df.index[i + 1], "signal"] = 1
                    # i+1 will be processed next iteration so skip it
                prev_dir = cur_dir

        # If we never had a first entry, seed one at the first valid bar
        if df["signal"].eq(1).sum() == 0 and len(df) > self.atr_period:
            df.loc[df.index[self.atr_period], "signal"] = 1

        return df
