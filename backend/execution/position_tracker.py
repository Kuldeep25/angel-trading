"""
Position tracker — unified view of live + paper positions with real-time PnL.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from angel.client import angel_client
from execution.paper_trading import paper_engine


def _strategy_symbols() -> dict:
    """Lazy import to avoid circular dependency."""
    try:
        from api.routes.live import get_strategy_symbols  # noqa: PLC0415
        return get_strategy_symbols()
    except Exception:
        return {}

logger = logging.getLogger(__name__)


def get_all_positions() -> Dict[str, Any]:
    """
    Return combined live + paper positions with PnL.

    Returns
    -------
    {
        "live":  [{symbol, net_qty, avg_price, ltp, unrealised_pnl, ...}],
        "paper": [{symbol, net_qty, avg_price, ltp, unrealised_pnl, ...}],
        "live_pnl":  float,
        "paper_pnl": float,
    }
    """
    strategy_map    = _strategy_symbols()
    live_positions  = _fetch_live_positions()
    paper_positions = paper_engine.get_positions()
    paper_pnl       = paper_engine.total_pnl()

    # Tag each live position
    for p in live_positions:
        sym = p.get("symbol", "")
        if sym in strategy_map:
            p["source"] = "strategy"
            p["strategy_key"] = strategy_map[sym]
        else:
            p["source"] = "manual"
            p["strategy_key"] = None

    live_pnl = sum(
        float(p.get("unrealised_pnl", 0)) + float(p.get("realised_pnl", 0))
        for p in live_positions
    )

    return {
        "live":      live_positions,
        "paper":     paper_positions,
        "live_pnl":  round(live_pnl, 2),
        "paper_pnl": round(paper_pnl, 2),
    }


def get_live_positions() -> List[Dict[str, Any]]:
    return _fetch_live_positions()


def get_paper_positions() -> List[Dict[str, Any]]:
    return paper_engine.get_positions()


def refresh_ltp_for_paper(symbol: str, ltp: float) -> None:
    """Update LTP for a paper position (call on each tick)."""
    paper_engine.update_ltp(symbol, ltp)


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_live_positions() -> List[Dict[str, Any]]:
    """Fetch net positions from Angel One and normalise field names."""
    if not angel_client.is_connected:
        return []
    try:
        resp = angel_client.smart_api.position()
        if not resp or not resp.get("status"):
            return []
        raw_positions = resp.get("data", {})
        # API returns {"net": [...], "day": [...]}
        net_positions = raw_positions.get("net", []) if isinstance(raw_positions, dict) else []
        return [_normalise_position(p) for p in net_positions]
    except Exception as exc:
        logger.error("getPosition error: %s", exc)
        return []


def _normalise_position(p: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Angel One position dict to a consistent schema."""
    qty = int(p.get("netqty", 0))
    avg = float(p.get("netprice", 0)) / 100.0   # paise → rupees
    ltp = float(p.get("ltp", 0)) / 100.0
    unrealised = round((ltp - avg) * qty, 2) if qty != 0 else 0.0
    realised    = float(p.get("realised", 0)) / 100.0

    return {
        "symbol":           p.get("tradingsymbol", ""),
        "exchange":         p.get("exchange", ""),
        "product_type":     p.get("producttype", ""),
        "net_qty":          qty,
        "avg_price":        round(avg, 2),
        "ltp":              round(ltp, 2),
        "unrealised_pnl":   unrealised,
        "realised_pnl":     round(realised, 2),
        "token":            p.get("symboltoken", ""),
    }
