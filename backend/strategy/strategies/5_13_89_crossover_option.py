import pandas as pd
import numpy as np


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _auto_strike_gap(price: float) -> int:
    if price >= 40_000: return 100   # BANKNIFTY
    if price >= 8_000:  return 50    # NIFTY
    if price >= 5_000:  return 100
    if price >= 2_500:  return 100
    if price >= 1_000:  return 50
    if price >= 500:    return 20
    if price >= 250:    return 10
    return 5


def _atm(spot: float, gap: int) -> float:
    return float(round(spot / gap) * gap)


class Strategy:
    """
    5-13-89 EMA Crossover Options Strategy.
    Generates CE/PE signals on 5-min NIFTY futures data.
    Entries when EMA5 crosses EMA13 in direction of EMA89 trend + RSI filter.
    Exits when price recrosses EMA5.
    Uses estimated ATM premium for position sizing (same as supertrend strategy).
    """

    # Default risk parameters (overrideable from the backtest form)
    sl_pct     = 0.0   # rely on EMA5 trailing exit, not fixed SL
    tsl_pct    = 0.0
    target_pct = 0.0
    premium_pct = 0.015  # 1.5% of ATM strike as estimated premium

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy().reset_index(drop=True)

        # ── Indicators ───────────────────────────────────────────────────────
        df["ema5"]  = df["close"].ewm(span=5,  adjust=False).mean()
        df["ema13"] = df["close"].ewm(span=13, adjust=False).mean()
        df["ema89"] = df["close"].ewm(span=89, adjust=False).mean()
        df["rsi"]   = _rsi(df["close"], 14)
        df["atr"]   = _atr(df, 14)

        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df["hour"] = df["timestamp"].dt.hour

        # ── ATM strike + estimated premium (enables options position sizing) ─
        gap = _auto_strike_gap(float(df["close"].median()))
        df["atm_strike"]  = df["close"].apply(lambda c: _atm(c, gap))
        df["atm_premium"] = (df["atm_strike"] * self.premium_pct).round(2)

        df["signal"]      = 0
        df["option_type"] = ""

        position  = None   # "CE" | "PE" | None
        atr_ma50  = df["atr"].rolling(50).mean()

        for i in range(20, len(df)):
            hour = df["hour"].iloc[i]

            # Time filter: only trade 9:15–15:00
            if hour < 9 or hour > 15:
                continue

            # Volatility filter: skip low-volatility candles
            atr_mean = atr_ma50.iloc[i]
            if pd.isna(atr_mean) or df["atr"].iloc[i] < atr_mean:
                continue

            ema5  = df["ema5"].iloc[i]
            ema13 = df["ema13"].iloc[i]
            ema89 = df["ema89"].iloc[i]
            r     = df["rsi"].iloc[i]

            prev_ema5  = df["ema5"].iloc[i - 1]
            prev_ema13 = df["ema13"].iloc[i - 1]
            close      = df["close"].iloc[i]

            # ── ENTRY ────────────────────────────────────────────────────────
            if position is None:

                # CALL: EMA5 crosses above EMA13, trend up (EMA5>13>89), RSI bullish
                if (
                    ema5 > ema13 > ema89
                    and prev_ema5 <= prev_ema13
                    and r > 52
                ):
                    df.at[i, "signal"]      = 1
                    df.at[i, "option_type"] = "CE"
                    position = "CE"

                # PUT: EMA5 crosses below EMA13, trend down (EMA5<13<89), RSI bearish
                elif (
                    ema5 < ema13 < ema89
                    and prev_ema5 >= prev_ema13
                    and r < 48
                ):
                    df.at[i, "signal"]      = 1
                    df.at[i, "option_type"] = "PE"
                    position = "PE"

            # ── EXIT ─────────────────────────────────────────────────────────
            else:
                if position == "CE" and close < ema5:
                    df.at[i, "signal"] = -1
                    position = None

                elif position == "PE" and close > ema5:
                    df.at[i, "signal"] = -1
                    position = None

        return df