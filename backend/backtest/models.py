"""Shared dataclasses for the backtest package (avoids circular imports)."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    symbol: str
    side: str          # "BUY" / "SELL"
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    exit_reason: str   # "SIGNAL" / "SL" / "TSL" / "EOD"
    atm_strike: float = 0.0   # 0 = not an options trade
    option_type: str  = ""    # "CE" / "PE" / "STRADDLE" / ""
    charges: float = 0.0      # total statutory + brokerage charges for the round trip
    net_pnl: float = 0.0      # pnl - charges
