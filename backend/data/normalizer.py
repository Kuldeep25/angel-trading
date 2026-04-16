"""
Candle data normalizer — converts raw Angel One candle lists into a
clean pandas DataFrame with standard column names and rupee prices.
"""
from __future__ import annotations

from typing import List

import pandas as pd


def normalize(raw_candles: List[List]) -> pd.DataFrame:
    """
    Convert raw Angel One candle data to a normalized DataFrame.

    Angel One candle format:
        [timestamp_str, open_paise, high_paise, low_paise, close_paise, volume]

    Returns
    -------
    pd.DataFrame with columns:
        timestamp (datetime, tz-aware or tz-naive)
        open, high, low, close (float, in rupees)
        volume (int)
    Index is reset integer index.
    """
    if not raw_candles:
        return _empty_df()

    df = pd.DataFrame(
        raw_candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )

    # Parse timestamps (Angel returns ISO-8601 strings in UTC)
    # Convert to IST (UTC+5:30) and strip timezone for clean display
    df["timestamp"] = (
        pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)  # remove tzinfo → naive IST datetime
    )

    # Angel One getCandleData returns prices already in rupees
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)

    # Drop bad rows and sort
    df.dropna(subset=["timestamp", "open", "high", "low", "close"], inplace=True)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)

    return df


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
