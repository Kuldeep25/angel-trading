"""
Futures Opening Range Breakout (ORB) Strategy
──────────────────────────────────────────────
Captures the opening range (first N candles) and trades the breakout.

Signal rules:
  BUY  (+1) on the first candle whose close breaks above the opening range high
  SELL (-1) on the first candle whose close breaks below the opening range low
  HOLD  (0) otherwise

Suitable for intraday futures (NIFTY FUT, BANKNIFTY FUT, etc.) on 5-min or 15-min bars.
"""
from __future__ import annotations

import pandas as pd


class Strategy:
    """Opening Range Breakout for futures (default: 6-bar range = 30 min on 5-min chart)."""

    def __init__(self, range_bars: int = 6) -> None:
        self.range_bars = range_bars

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Parameters
        ----------
        df : pd.DataFrame
            Normalised intraday OHLCV data with columns:
            [timestamp, open, high, low, close, volume]
            Data should cover a single trading day or multiple days.

        Returns
        -------
        pd.DataFrame with added columns:
            or_high, or_low, signal (1 / -1 / 0)
        """
        df = df.copy()
        df["signal"] = 0
        df["or_high"] = float("nan")
        df["or_low"]  = float("nan")

        if len(df) < self.range_bars + 1:
            return df

        # Group by date so the range resets each trading day
        df["_date"] = pd.to_datetime(df["timestamp"]).dt.date

        result_parts: list[pd.DataFrame] = []
        for _date, day_df in df.groupby("_date"):
            day_df = day_df.copy().reset_index(drop=True)
            if len(day_df) <= self.range_bars:
                result_parts.append(day_df)
                continue

            # Opening range
            or_high = day_df["high"].iloc[: self.range_bars].max()
            or_low  = day_df["low"].iloc[: self.range_bars].min()
            day_df["or_high"] = or_high
            day_df["or_low"]  = or_low

            # Breakout signals only after the opening range is complete
            traded = False
            for i in range(self.range_bars, len(day_df)):
                if traded:
                    break
                row_close = day_df.loc[i, "close"]
                if row_close > or_high:
                    day_df.loc[i, "signal"] = 1
                    traded = True
                elif row_close < or_low:
                    day_df.loc[i, "signal"] = -1
                    traded = True

            result_parts.append(day_df)

        out = pd.concat(result_parts, ignore_index=True)
        out.drop(columns=["_date"], inplace=True)
        return out
