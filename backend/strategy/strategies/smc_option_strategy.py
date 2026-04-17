import pandas as pd
import numpy as np

def _atm_strike(price, gap=100):
    return round(price / gap) * gap

def atr(df, period=14):
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

class Strategy:

    def __init__(self, premium_pct=0.012, strike_gap=100):
        self.premium_pct = premium_pct
        self.strike_gap = strike_gap

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:

        df = df.copy().reset_index(drop=True)

        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df['date'] = df['timestamp'].dt.date
            df['hour'] = df['timestamp'].dt.hour
        else:
            df['date'] = 0
            df['hour'] = 10

        df['ema20'] = df['close'].ewm(span=20).mean()
        df['ema50'] = df['close'].ewm(span=50).mean()
        df['atr'] = atr(df)

        df['atm_strike'] = df['close'].apply(lambda x: _atm_strike(x, self.strike_gap))
        df['atm_premium'] = (df['atm_strike'] * self.premium_pct).round(2)

        df['signal'] = 0
        df['option_type'] = None

        position = None
        entry_price = 0
        traded_day = None

        for i in range(50, len(df)):

            date = df['date'].iloc[i]
            hour = df['hour'].iloc[i]

            # BEST TIME ONLY
            if hour < 9 or hour > 11:
                continue

            close = df['close'].iloc[i]
            open_ = df['open'].iloc[i]
            prev_close = df['close'].iloc[i-1]

            prev_high = df['high'].iloc[i-1]
            prev_low = df['low'].iloc[i-1]

            atr_now = df['atr'].iloc[i]

            ema20 = df['ema20'].iloc[i]
            ema50 = df['ema50'].iloc[i]

            # STRONG TREND
            if abs(ema20 - ema50) < 0.5 * atr_now:
                continue

            # ONE TRADE PER DAY
            if traded_day == date:
                continue

            # STRONG MOMENTUM ONLY
            if abs(close - open_) < 1.0 * atr_now:
                continue

            if abs(close - prev_close) < 0.5 * atr_now:
                continue

            # ===== ENTRY =====
            if position is None:

                if close > prev_high and ema20 > ema50:
                    df.loc[i, 'signal'] = 1
                    df.loc[i, 'option_type'] = "CE"
                    position = "CE"
                    entry_price = close
                    traded_day = date

                elif close < prev_low and ema20 < ema50:
                    df.loc[i, 'signal'] = 1
                    df.loc[i, 'option_type'] = "PE"
                    position = "PE"
                    entry_price = close
                    traded_day = date

            # ===== EXIT =====
            else:

                move = close - entry_price if position == "CE" else entry_price - close

                # BIG WINNER
                if move > 4.0 * atr_now:
                    df.loc[i, 'signal'] = -1
                    position = None

                # CONTROLLED LOSS
                elif move < -1.2 * atr_now:
                    df.loc[i, 'signal'] = -1
                    position = None

        return df