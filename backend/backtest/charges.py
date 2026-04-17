"""
Indian brokerage & statutory charge calculator for backtesting.
Based on Angel One SmartAPI fee structure (as of 2026) + NSE/BSE/SEBI levies.

Segments covered:
  equity   – intraday (default) or delivery
  futures  – F&O futures (NFO / BFO / MCX)
  options  – F&O options (NFO / BFO)

Charges computed for a complete round trip (1 entry order + 1 exit order):
  Brokerage, STT, Exchange transaction charges, SEBI charges, GST, Stamp duty.
"""
from __future__ import annotations
from typing import Any, Dict


def compute_charges(
    instrument_type: str,
    quantity: int,
    entry_price: float,
    exit_price: float,
    product_type: str = "INTRADAY",
) -> Dict[str, Any]:
    """
    Return per-trade charge breakdown for one round-trip.

    Parameters
    ----------
    instrument_type : "equity" | "futures" | "options"
    quantity        : number of shares / lots / contracts
    entry_price     : fill price on entry
    exit_price      : fill price on exit
    product_type    : "INTRADAY" or "DELIVERY" (equity only)

    Returns
    -------
    dict with keys: brokerage, stt, exc_charge, sebi, gst, stamp, total
    """
    itype = instrument_type.lower()
    is_delivery = product_type.upper() == "DELIVERY"

    buy_val  = abs(entry_price * quantity)
    sell_val = abs(exit_price  * quantity)
    total_turnover = buy_val + sell_val

    # ── 1. Brokerage ──────────────────────────────────────────────────────
    # Angel One (per executed ORDER, NOT per lot):
    #   Equity Delivery : min(₹20, 0.1% of order value) — each leg separately
    #   Equity Intraday : min(₹20, 0.1% of order value) — each leg separately
    #   Futures/Options : flat ₹20 per executed order
    #   Currency/Commodity: flat ₹20 per executed order
    if itype == "equity":
        brokerage = min(20.0, buy_val * 0.001) + min(20.0, sell_val * 0.001)
    else:
        brokerage = 20.0 + 20.0  # ₹20 entry order + ₹20 exit order = ₹40

    # ── 2. STT (Securities Transaction Tax) ──────────────────────────────
    if itype == "equity":
        if is_delivery:
            stt = total_turnover * 0.001        # 0.1% both sides (buy & sell)
        else:
            stt = sell_val * 0.00025            # 0.025% sell side only
    elif itype == "futures":
        stt = sell_val * 0.0002                 # 0.02% sell side (contract value)
    elif itype == "options":
        stt = sell_val * 0.001                  # 0.1% on sell premium
    else:
        stt = sell_val * 0.00025

    # ── 3. Exchange Transaction Charges ──────────────────────────────────
    if itype == "equity":
        exc_charge = total_turnover * 0.0000297  # NSE 0.00297% (delivery & intraday)
    elif itype == "futures":
        exc_charge = total_turnover * 0.0000173  # NSE 0.00173%
    elif itype == "options":
        exc_charge = total_turnover * 0.0003503  # NSE 0.03503% on premium
    else:
        exc_charge = total_turnover * 0.0000297

    # ── 4. SEBI Charges (₹10 per crore = 0.000001 of turnover) ───────────
    sebi = total_turnover * 0.000001

    # ── 5. GST 18% on (brokerage + exchange charges + SEBI charges) ──────
    gst = (brokerage + exc_charge + sebi) * 0.18

    # ── 6. Stamp Duty (buy side only) ────────────────────────────────────
    if itype == "equity":
        stamp = buy_val * (0.00015 if is_delivery else 0.00003)
    elif itype == "futures":
        stamp = buy_val * 0.00002
    elif itype == "options":
        stamp = buy_val * 0.00003
    else:
        stamp = buy_val * 0.00003

    total = brokerage + stt + exc_charge + sebi + gst + stamp

    return {
        "brokerage":  round(brokerage, 4),
        "stt":        round(stt, 4),
        "exc_charge": round(exc_charge, 4),
        "sebi":       round(sebi, 4),
        "gst":        round(gst, 4),
        "stamp":      round(stamp, 4),
        "total":      round(total, 4),
    }
