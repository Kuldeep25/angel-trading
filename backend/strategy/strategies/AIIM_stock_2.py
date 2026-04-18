import pandas as pd
import numpy as np

class Strategy:

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:

        df = df.copy().reset_index(drop=True)

        # Time
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['date'] = df['timestamp'].dt.date
        df['hour'] = df['timestamp'].dt.hour
        df['minute'] = df['timestamp'].dt.minute

        df['signal'] = 0

        position = None
        entry_price = 0
        sl = 0
        target = 0

        current_day = None
        range_high = None
        range_low = None
        orb_captured = False

        for i in range(len(df)):

            row = df.iloc[i]
            date = row['date']
            hour = row['hour']
            minute = row['minute']
            high = row['high']
            low = row['low']
            close = row['close']

            # Reset daily
            if current_day != date:
                current_day = date
                range_high = None
                range_low = None
                orb_captured = False
                position = None

            # Capture 15-min ORB (9:15–9:30)
            if not orb_captured:
                if hour == 9 and minute == 30:

                    first_15 = df[
                        (df['date'] == date) &
                        (df['timestamp'] >= pd.Timestamp(str(date) + " 09:15:00")) &
                        (df['timestamp'] <= pd.Timestamp(str(date) + " 09:30:00"))
                    ]

                    if len(first_15) > 0:
                        range_high = first_15['high'].max()
                        range_low = first_15['low'].min()
                        orb_captured = True

                continue

            # Trade only till 11 AM
            if hour > 11:
                continue

            if range_high is None:
                continue

            risk = range_high - range_low

            # ===== ENTRY (CLOSE CONFIRMATION) =====
            if position is None:

                # BUY only if CLOSE breaks above
                if close > range_high:
                    entry_price = close
                    sl = entry_price - risk
                    target = entry_price + (2 * risk)

                    df.loc[i, 'signal'] = 1
                    position = "LONG"

                # SELL only if CLOSE breaks below
                elif close < range_low:
                    entry_price = close
                    sl = entry_price + risk
                    target = entry_price - (2 * risk)

                    df.loc[i, 'signal'] = -1
                    position = "SHORT"

            # ===== EXIT =====
            else:

                if position == "LONG":
                    if close <= sl or close >= target:
                        df.loc[i, 'signal'] = 0
                        position = None

                elif position == "SHORT":
                    if close >= sl or close <= target:
                        df.loc[i, 'signal'] = 0
                        position = None

        return df