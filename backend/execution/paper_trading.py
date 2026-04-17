"""
Paper trading simulator — in-memory order and position management.
No real orders are sent to Angel One.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PaperTradingEngine:
    """Thread-safe, in-memory paper trading simulator."""

    def __init__(self) -> None:
        self._lock      = threading.Lock()
        self._positions: Dict[str, Dict[str, Any]] = {}  # key = symbol
        self._orders:    List[Dict[str, Any]] = []
        self._order_id_seq = 1

    # ── Order operations ─────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        exchange: str,
        transaction_type: str,    # "BUY" / "SELL"
        quantity: int,
        price: float,             # use current market price (simulated fill)
        product_type: str = "INTRADAY",
        order_tag: str = "",
    ) -> Dict[str, Any]:
        """Simulate a market order fill at `price`."""
        with self._lock:
            order_id = f"PAPER-{self._order_id_seq:06d}"
            self._order_id_seq += 1

            order = {
                "orderid":          order_id,
                "symbol":           symbol,
                "exchange":         exchange,
                "transaction_type": transaction_type.upper(),
                "quantity":         quantity,
                "fill_price":       price,
                "product_type":     product_type,
                "order_tag":        order_tag,
                "status":           "COMPLETE",
                "timestamp":        _now(),
            }
            self._orders.append(order)

            self._update_position(symbol, exchange, transaction_type, quantity, price, product_type)
            logger.info("Paper order filled: %s %s %s @ %.2f x%d", order_id, transaction_type, symbol, price, quantity)
            return order

    def cancel_order(self, order_id: str) -> bool:
        """Paper orders are immediately filled, so cancellation is a no-op."""
        logger.warning("Paper orders are filled immediately; cancel is not applicable for %s", order_id)
        return False

    def get_orders(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._orders)

    # ── Position operations ──────────────────────────────────────────────────

    def get_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._positions.values())

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._positions.get(symbol)

    def update_ltp(self, symbol: str, ltp: float) -> None:
        """Update the LTP for an open position to recalculate unrealised PnL."""
        with self._lock:
            pos = self._positions.get(symbol)
            if pos and pos["net_qty"] != 0:
                pos["ltp"] = ltp
                pos["unrealised_pnl"] = round(
                    (ltp - pos["avg_price"]) * pos["net_qty"], 2
                )

    def exit_position(self, symbol: str, ltp: float) -> Optional[Dict[str, Any]]:
        """
        Exit the full open position for a symbol at `ltp`.
        Returns the closing order dict, or None if no position.
        """
        with self._lock:
            pos = self._positions.get(symbol)
            if not pos or pos["net_qty"] == 0:
                return None
            qty  = abs(pos["net_qty"])
            side = "SELL" if pos["net_qty"] > 0 else "BUY"
        return self.place_order(
            symbol=symbol,
            exchange=pos["exchange"],
            transaction_type=side,
            quantity=qty,
            price=ltp,
            product_type=pos.get("product_type", "INTRADAY"),
            order_tag="EXIT",
        )

    def exit_all_positions(self, ltp_map: Dict[str, float]) -> List[Dict[str, Any]]:
        """Exit all open positions. ltp_map = {symbol: ltp}."""
        results = []
        with self._lock:
            open_symbols = [s for s, p in self._positions.items() if p["net_qty"] != 0]
        for symbol in open_symbols:
            ltp = ltp_map.get(symbol, 0.0)
            if ltp > 0:
                order = self.exit_position(symbol, ltp)
                if order:
                    results.append(order)
        return results

    def total_pnl(self) -> float:
        """Sum of realised + unrealised PnL across all positions."""
        with self._lock:
            return round(
                sum(p["realised_pnl"] + p.get("unrealised_pnl", 0.0)
                    for p in self._positions.values()),
                2,
            )

    def reset(self) -> None:
        """Clear all paper positions and orders (use carefully)."""
        with self._lock:
            self._positions.clear()
            self._orders.clear()
            self._order_id_seq = 1
        logger.info("Paper trading engine reset.")

    # ── Internal ─────────────────────────────────────────────────────────────

    def _update_position(
        self,
        symbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        price: float,
        product_type: str,
    ) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            pos = {
                "symbol":           symbol,
                "exchange":         exchange,
                "net_qty":          0,
                "avg_price":        0.0,
                "ltp":              price,
                "realised_pnl":     0.0,
                "unrealised_pnl":   0.0,
                "product_type":     product_type,
                "last_updated":     _now(),
            }
            self._positions[symbol] = pos

        prev_qty   = pos["net_qty"]
        prev_price = pos["avg_price"]
        sign       = 1 if transaction_type.upper() == "BUY" else -1
        qty_delta  = sign * quantity

        new_qty = prev_qty + qty_delta

        if new_qty == 0:
            # Position closed — realise PnL
            if prev_qty > 0:   # was long
                pos["realised_pnl"] += round((price - prev_price) * prev_qty, 2)
            elif prev_qty < 0: # was short
                pos["realised_pnl"] += round((prev_price - price) * abs(prev_qty), 2)
            pos["avg_price"]      = 0.0
            pos["unrealised_pnl"] = 0.0
        elif (prev_qty >= 0 and qty_delta > 0) or (prev_qty <= 0 and qty_delta < 0):
            # Adding to existing position — update average price
            total_cost  = prev_price * abs(prev_qty) + price * abs(qty_delta)
            pos["avg_price"] = total_cost / abs(new_qty)
        else:
            # Partial close
            if prev_qty > 0:
                realised = (price - prev_price) * abs(qty_delta)
            else:
                realised = (prev_price - price) * abs(qty_delta)
            pos["realised_pnl"] += round(realised, 2)
            # avg_price stays the same for remaining portion

        pos["net_qty"]      = new_qty
        pos["ltp"]          = price
        pos["unrealised_pnl"] = round((price - pos["avg_price"]) * new_qty, 2) if new_qty != 0 else 0.0
        pos["last_updated"] = _now()


def _now() -> str:
    return datetime.utcnow().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
#  Position Guard Engine — automated SL / Target / Trailing-SL monitor
# ─────────────────────────────────────────────────────────────────────────────

class PositionGuardEngine:
    """
    Background thread that watches open positions every 5 seconds and
    fires an exit order when SL / Target / Trailing-SL condition is met.
    Works for both paper and live positions.
    """

    def __init__(self) -> None:
        self._lock   = threading.Lock()
        self._guards: Dict[str, Dict[str, Any]] = {}  # key = symbol
        self._thread: Optional[threading.Thread] = None
        self._stop   = threading.Event()

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="guard-monitor"
        )
        self._thread.start()
        logger.info("Position guard monitor started.")

    def stop(self) -> None:
        self._stop.set()

    # ── Public API ─────────────────────────────────────────────────────

    def set_guard(
        self,
        symbol: str,
        exchange: str,
        paper: bool,
        sl_price: float = 0.0,
        target_price: float = 0.0,
        tsl_pct: float = 0.0,
    ) -> Dict[str, Any]:
        guard = {
            "symbol":       symbol,
            "exchange":     exchange,
            "paper":        paper,
            "sl_price":     sl_price,
            "target_price": target_price,
            "tsl_pct":      tsl_pct,
            "tsl_high":     0.0,
            "triggered":    False,
        }
        with self._lock:
            self._guards[symbol] = guard
        logger.info(
            "Guard set: %s  SL=%.2f  Target=%.2f  TSL=%.1f%%",
            symbol, sl_price, target_price, tsl_pct,
        )
        return {k: v for k, v in guard.items() if k != "tsl_high"}

    def remove_guard(self, symbol: str) -> bool:
        with self._lock:
            return self._guards.pop(symbol, None) is not None

    def get_guards(self) -> Dict[str, Any]:
        with self._lock:
            return {
                k: {fk: fv for fk, fv in v.items() if fk != "tsl_high"}
                for k, v in self._guards.items()
            }

    # ── Monitor loop ────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._check_all()
            except Exception as exc:
                logger.exception("Guard monitor error: %s", exc)
            self._stop.wait(5)

    def _check_all(self) -> None:
        with self._lock:
            symbols = list(self._guards.keys())
        for symbol in symbols:
            with self._lock:
                guard = self._guards.get(symbol)
            if not guard or guard["triggered"]:
                continue
            ltp = self._get_ltp(symbol, guard["exchange"], guard["paper"])
            if ltp <= 0:
                continue

            # Update TSL high-watermark
            if guard["tsl_pct"] > 0:
                with self._lock:
                    if ltp > guard["tsl_high"]:
                        guard["tsl_high"] = ltp
                    tsl_trigger = guard["tsl_high"] * (1 - guard["tsl_pct"] / 100)
                if guard["tsl_high"] > 0 and ltp <= tsl_trigger:
                    logger.info("TSL triggered: %s @ %.2f (high=%.2f)", symbol, ltp, guard["tsl_high"])
                    self._exit(guard, ltp, "TSL")
                    continue

            # Fixed SL
            if guard["sl_price"] > 0 and ltp <= guard["sl_price"]:
                logger.info("SL triggered: %s @ %.2f", symbol, ltp)
                self._exit(guard, ltp, "SL")
                continue

            # Target
            if guard["target_price"] > 0 and ltp >= guard["target_price"]:
                logger.info("Target triggered: %s @ %.2f", symbol, ltp)
                self._exit(guard, ltp, "Target")

    def _get_ltp(self, symbol: str, exchange: str, paper: bool) -> float:
        if paper:
            pos = paper_engine.get_position(symbol)
            return float(pos["ltp"]) if pos else 0.0
        try:
            from options.engine import get_spot_price
            return get_spot_price(symbol.split("-")[0], exchange) or 0.0
        except Exception:
            return 0.0

    def _exit(self, guard: Dict[str, Any], ltp: float, reason: str) -> None:
        symbol = guard["symbol"]
        with self._lock:
            guard["triggered"] = True
        try:
            if guard["paper"]:
                paper_engine.exit_position(symbol, ltp)
                logger.info("Guard exit (paper) %s @ %.2f  reason=%s", symbol, ltp, reason)
            else:
                from execution.engine import place_order
                from execution.position_tracker import _fetch_live_positions
                live = _fetch_live_positions()
                pos = next((p for p in live if p["symbol"] == symbol), None)
                if pos and pos["net_qty"] != 0:
                    place_order(
                        symbol=symbol,
                        token=pos.get("token", ""),
                        exchange=guard["exchange"],
                        transaction_type="SELL" if pos["net_qty"] > 0 else "BUY",
                        quantity=abs(pos["net_qty"]),
                        paper=False,
                        ltp=ltp,
                    )
                logger.info("Guard exit (live) %s @ %.2f  reason=%s", symbol, ltp, reason)
        except Exception as exc:
            logger.exception("Guard exit error for %s: %s", symbol, exc)
        finally:
            with self._lock:
                self._guards.pop(symbol, None)


guard_engine = PositionGuardEngine()


# Module-level singleton
paper_engine = PaperTradingEngine()
