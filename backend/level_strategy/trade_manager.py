"""
Trade data model and manager for Level-Based Options Trading Strategy.
Persists trades to data/level_strategy_trades.json.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

_DATA_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "level_strategy_trades.json")
)

_lock = threading.Lock()


@dataclass
class Trade:
    id: str                     = field(default_factory=lambda: str(uuid.uuid4())[:8])
    alert_id: str               = ""
    symbol: str                 = ""
    option_symbol: str          = ""   # e.g. NIFTY28APR26C23500
    token: str                  = ""   # NFO instrument token
    option_type: str            = ""   # CE | PE
    strike: float               = 0.0
    expiry: str                 = ""
    entry_price: float          = 0.0
    sl: float                   = 0.0
    target: float               = 0.0
    tsl_level: float            = 0.0  # current trailing SL (updated as trade moves)
    quantity: int               = 0
    lot_size: int               = 1
    lots: int                   = 1
    level: float                = 0.0  # the S/R level that triggered this trade
    next_level: float           = 0.0
    status: str                 = "open"   # open | closed
    exit_price: float           = 0.0
    exit_reason: str            = ""   # sl | target | tsl | manual | time_limit | opposite_signal
    pnl: float                  = 0.0
    net_pnl: float              = 0.0
    paper: bool                 = True
    entry_time: str             = ""
    exit_time: str              = ""
    order_id: str               = ""
    exit_order_id: str          = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Trade":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


class TradeManager:
    """Thread-safe trade store backed by JSON."""

    def __init__(self) -> None:
        self.active_trades: List[Trade]  = []
        self.trade_history: List[Trade]  = []
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(_DATA_FILE):
            return
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.active_trades  = [Trade.from_dict(t) for t in data.get("active_trades", [])]
            self.trade_history  = [Trade.from_dict(t) for t in data.get("trade_history", [])]
        except Exception:
            pass

    def persist(self) -> None:
        os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
        with open(_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "active_trades": [t.to_dict() for t in self.active_trades],
                    "trade_history": [t.to_dict() for t in self.trade_history],
                },
                f,
                indent=2,
            )

    # ── Mutations ─────────────────────────────────────────────────────────────

    def add_trade(self, trade: Trade) -> None:
        with _lock:
            self.active_trades.append(trade)
            self.persist()

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        exit_order_id: str = "",
        charges: float = 0.0,
    ) -> Optional[Trade]:
        with _lock:
            for i, t in enumerate(self.active_trades):
                if t.id == trade_id:
                    t.exit_price    = round(exit_price, 2)
                    t.exit_reason   = exit_reason
                    t.exit_order_id = exit_order_id
                    t.exit_time     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    t.status        = "closed"
                    # PnL: (exit - entry) × lots × lot_size, sign depends on CE/PE
                    raw_pnl = (t.exit_price - t.entry_price) * t.quantity
                    t.pnl     = round(raw_pnl, 2)
                    t.net_pnl = round(raw_pnl - charges, 2)
                    self.trade_history.insert(0, t)
                    self.active_trades.pop(i)
                    self.persist()
                    return t
        return None

    def get_active(self, trade_id: str) -> Optional[Trade]:
        for t in self.active_trades:
            if t.id == trade_id:
                return t
        return None

    def trades_today(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        return sum(
            1 for t in self.active_trades + self.trade_history
            if t.entry_time.startswith(today)
        )

    def has_open_trade_for_level(self, symbol: str, level: float) -> bool:
        for t in self.active_trades:
            if t.symbol == symbol and abs(t.level - level) < 1.0:
                return True
        return False

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        all_closed = self.trade_history
        total      = len(all_closed)
        wins       = sum(1 for t in all_closed if t.net_pnl > 0)
        total_pnl  = round(sum(t.net_pnl for t in all_closed), 2)
        win_rate   = round(wins / total * 100, 1) if total > 0 else 0.0

        # Max drawdown from equity curve
        equity, peak, max_dd = 0.0, 0.0, 0.0
        for t in reversed(all_closed):
            equity += t.net_pnl
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # Sharpe (simplified, daily PnL series)
        import math
        pnls = [t.net_pnl for t in all_closed]
        sharpe = 0.0
        if len(pnls) > 1:
            mean = sum(pnls) / len(pnls)
            variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
            std = math.sqrt(variance) if variance > 0 else 0.0
            sharpe = round(mean / std * math.sqrt(252), 2) if std > 0 else 0.0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "max_drawdown": round(max_dd, 2),
            "sharpe": sharpe,
            "active_count": len(self.active_trades),
        }


# Singleton
trade_manager = TradeManager()
