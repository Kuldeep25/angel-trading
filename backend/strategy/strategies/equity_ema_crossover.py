"""
Equity EMA Crossover Strategy
──────────────────────────────
Uses a fast EMA (9-period) and a slow EMA (21-period) on the close price.

Signal rules:
  BUY  (+1) when fast EMA crosses above slow EMA
  SELL (-1) when fast EMA crosses below slow EMA
  HOLD  (0) otherwise

Works on any timeframe (intraday or daily).
"""
from __future__ import annotations

import pandas as pd


class Strategy:
    """EMA Crossover: fast=9, slow=21."""

    def __init__(self, fast: int = 9, slow: int = 21) -> None:
        self.fast = fast
        self.slow = slow

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Parameters
        ----------
        df : pd.DataFrame
            Normalised OHLCV data with columns:
            [timestamp, open, high, low, close, volume]

        Returns
        -------
        pd.DataFrame
            Original df with additional columns:
              ema_fast, ema_slow, signal (1 / -1 / 0)
        """
        df = df.copy()
        df["ema_fast"] = df["close"].ewm(span=self.fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.slow, adjust=False).mean()

        # Crossover detection
        prev_fast = df["ema_fast"].shift(1)
        prev_slow = df["ema_slow"].shift(1)

        buy_signal  = (df["ema_fast"] > df["ema_slow"]) & (prev_fast <= prev_slow)
        sell_signal = (df["ema_fast"] < df["ema_slow"]) & (prev_fast >= prev_slow)

        df["signal"] = 0
        df.loc[buy_signal,  "signal"] = 1
        df.loc[sell_signal, "signal"] = -1

        return df
