"""
Live execution engine — routes orders to Angel One SmartAPI or
the paper trading simulator, depending on mode.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from angel.client import angel_client
from execution.paper_trading import paper_engine

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def place_order(
    symbol: str,
    token: str,
    exchange: str,
    transaction_type: str,   # "BUY" / "SELL"
    quantity: int,
    order_type: str = "MARKET",
    product_type: str = "INTRADAY",
    price: float = 0.0,
    trigger_price: float = 0.0,
    variety: str = "NORMAL",
    duration: str = "DAY",
    order_tag: str = "",
    paper: bool = False,
    ltp: float = 0.0,
) -> Dict[str, Any]:
    """
    Place a buy or sell order.

    Parameters
    ----------
    paper : bool
        If True, routes to the in-memory paper engine.
        If False, sends to Angel One SmartAPI.
    ltp   : float
        Current market price — used as fill price for paper orders.
    """
    if paper:
        fill_price = ltp if ltp > 0 else price
        return paper_engine.place_order(
            symbol=symbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            price=fill_price,
            product_type=product_type,
            order_tag=order_tag,
        )

    # ── Live order ───────────────────────────────────────────────────────────
    params: Dict[str, Any] = {
        "variety":         variety,
        "tradingsymbol":   symbol,
        "symboltoken":     token,
        "transactiontype": transaction_type.upper(),
        "exchange":        exchange.upper(),
        "ordertype":       order_type.upper(),
        "producttype":     product_type.upper(),
        "duration":        duration.upper(),
        "quantity":        str(quantity),
        "price":           str(round(price, 2)),
        "triggerprice":    str(round(trigger_price, 2)),
    }
    if order_tag:
        params["ordertag"] = order_tag[:20]

    try:
        resp = angel_client.smart_api.placeOrder(params)
        logger.info("Live order placed: %s", resp)
        return resp or {}
    except Exception as exc:
        logger.error("placeOrder error: %s", exc)
        raise


def modify_order(
    order_id: str,
    variety: str,
    order_type: str,
    product_type: str,
    duration: str,
    price: float = 0.0,
    quantity: Optional[int] = None,
    trigger_price: float = 0.0,
) -> Dict[str, Any]:
    """Modify a pending live order."""
    params: Dict[str, Any] = {
        "variety":      variety,
        "orderid":      order_id,
        "ordertype":    order_type.upper(),
        "producttype":  product_type.upper(),
        "duration":     duration.upper(),
        "price":        str(round(price, 2)),
        "triggerprice": str(round(trigger_price, 2)),
    }
    if quantity is not None:
        params["quantity"] = str(quantity)
    try:
        resp = angel_client.smart_api.modifyOrder(params)
        logger.info("Order modified: %s → %s", order_id, resp)
        return resp or {}
    except Exception as exc:
        logger.error("modifyOrder error for %s: %s", order_id, exc)
        raise


def cancel_order(order_id: str, variety: str = "NORMAL") -> Dict[str, Any]:
    """Cancel a pending live order."""
    try:
        resp = angel_client.smart_api.cancelOrder(variety, order_id)
        logger.info("Order cancelled: %s → %s", order_id, resp)
        return resp or {}
    except Exception as exc:
        logger.error("cancelOrder error for %s: %s", order_id, exc)
        raise


def place_sl_order(
    symbol: str,
    token: str,
    exchange: str,
    transaction_type: str,
    quantity: int,
    trigger_price: float,
    limit_price: float,
    product_type: str = "INTRADAY",
    paper: bool = False,
    ltp: float = 0.0,
) -> Dict[str, Any]:
    """
    Place a stop-loss limit order.
    In paper mode, treated as a regular fill (SL managed externally).
    """
    return place_order(
        symbol=symbol,
        token=token,
        exchange=exchange,
        transaction_type=transaction_type,
        quantity=quantity,
        order_type="STOPLOSS_LIMIT",
        product_type=product_type,
        price=limit_price,
        trigger_price=trigger_price,
        variety="STOPLOSS",
        paper=paper,
        ltp=ltp,
    )


def place_gtt_trailing_sl(
    symbol: str,
    token: str,
    exchange: str,
    transaction_type: str,
    quantity: int,
    trail_jump: float,
    last_ltp: float,
    product_type: str = "INTRADAY",
) -> Dict[str, Any]:
    """
    Create a GTT (Good-Till-Triggered) trailing stop-loss rule on Angel One.
    trail_jump = the gap between LTP and trigger in rupees.
    """
    trigger_price = (
        round(last_ltp - trail_jump, 2) if transaction_type.upper() == "BUY"
        else round(last_ltp + trail_jump, 2)
    )
    params = {
        "tradingsymbol": symbol,
        "symboltoken":   token,
        "exchange":      exchange.upper(),
        "producttype":   product_type.upper(),
        "transactiontype": transaction_type.upper(),
        "price":         str(trigger_price),
        "qty":           str(quantity),
        "disclosedqty":  str(quantity),
        "triggerprice":  str(trigger_price),
        "timeperiod":    "365",
    }
    try:
        resp = angel_client.smart_api.gttCreateRule(params)
        logger.info("GTT trailing SL created: %s", resp)
        return resp or {}
    except Exception as exc:
        logger.error("gttCreateRule error: %s", exc)
        raise


def get_order_book() -> Dict[str, Any]:
    """Fetch full order book from Angel One."""
    try:
        return angel_client.smart_api.getOrderBook() or {}
    except Exception as exc:
        logger.error("getOrderBook error: %s", exc)
        return {}


def get_trade_book() -> Dict[str, Any]:
    """Fetch executed trades from Angel One."""
    try:
        return angel_client.smart_api.getTradeBook() or {}
    except Exception as exc:
        logger.error("getTradeBook error: %s", exc)
        return {}
