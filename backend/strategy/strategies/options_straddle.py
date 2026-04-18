"""
Options ATM Straddle Strategy
──────────────────────────────
Buys an ATM straddle (CE + PE at the same ATM strike) at the start of each
session and exits at a fixed target or stop on the combined premium.

Signal conventions
  signal = 2  → Enter straddle (buy CE + PE)
  signal = -2 → Exit straddle
  signal = 0  → Hold

ATM strike is dynamically computed as the nearest multiple of `strike_gap`
to the underlying close (50 for NIFTY, 100 for BANKNIFTY).
The estimated straddle premium = atm_strike × premium_pct (default 1.5%).

Suitable for NIFTY / BANKNIFTY weekly options on 15-min or daily bars.
"""
from __future__ import annotations

import pandas as pd

from backtest.option_pricer import add_bsm_premium


def _atm_strike(spot: float, gap: int) -> float:
    """Round spot to the nearest strike interval."""
    return round(spot / gap) * gap


class Strategy:
    """
    ATM Straddle entry at market open; exit at combined premium target/stop.

    Parameters
    ----------
    entry_bar     : int   – bar index of entry within each day (0 = open bar)
    target_pct    : float – exit when underlying moves this % beyond entry
    stoploss_pct  : float – exit when underlying reverses this % from entry
    strike_gap    : int   – strike interval (50 for NIFTY, 100 for BANKNIFTY)
    premium_pct   : float – ATM straddle premium as % of strike (approx 1.5%)
    """

    def __init__(
        self,
        entry_bar: int = 0,
        target_pct: float = 0.30,
        stoploss_pct: float = 0.20,
        strike_gap: int | None = None,   # None = auto-detect from price level
    ) -> None:
        self.entry_bar    = entry_bar
        self.target_pct   = target_pct
        self.stoploss_pct = stoploss_pct
        self.strike_gap   = strike_gap   # resolved in generate()

    def _resolve_gap(self, median_close: float) -> int:
        """Auto-detect strike interval from typical price level."""
        if self.strike_gap is not None:
            return self.strike_gap
        if median_close >= 40_000:   # BANKNIFTY range
            return 100
        if median_close >= 10_000:   # NIFTY range
            return 50
        return 50

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        gap = self._resolve_gap(float(df["close"].median()))

        df["signal"]     = 0
        df["atm_strike"] = df["close"].apply(lambda s: _atm_strike(s, gap))
        # BSM pricing: accounts for theta decay + historical volatility
        df = add_bsm_premium(df, option_type="STRADDLE")

        df["_date"] = pd.to_datetime(df["timestamp"]).dt.date

        result_parts: list[pd.DataFrame] = []
        for _date, day_df in df.groupby("_date"):
            day_df = day_df.copy().reset_index(drop=True)

            if len(day_df) <= self.entry_bar:
                result_parts.append(day_df)
                continue

            entry_idx    = self.entry_bar
            entry_spot   = day_df.loc[entry_idx, "close"]
            entry_strike = day_df.loc[entry_idx, "atm_strike"]
            day_df.loc[entry_idx, "signal"] = 2

            # Exit when underlying moves ±target/stoploss% from entry spot
            in_trade    = True
            target_spot = entry_spot * (1 + self.target_pct)
            stop_spot   = entry_spot * (1 - self.stoploss_pct)

            for i in range(entry_idx + 1, len(day_df)):
                if not in_trade:
                    break
                c = day_df.loc[i, "close"]
                if c >= target_spot or c <= stop_spot:
                    day_df.loc[i, "signal"] = -2
                    in_trade = False

            # Auto-exit at end of day if still open
            if in_trade:
                last_idx = len(day_df) - 1
                if day_df.loc[last_idx, "signal"] == 0:
                    day_df.loc[last_idx, "signal"] = -2

            result_parts.append(day_df)

        out = pd.concat(result_parts, ignore_index=True)
        out.drop(columns=["_date"], inplace=True)
        return out
