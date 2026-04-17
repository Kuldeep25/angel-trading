"""
Account info routes — funds, margins, and profile.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from angel.client import angel_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/account/funds")
def get_funds():
    """
    Return available cash, used margin, net balance from Angel One RMS.
    Returns zeros when not connected.
    """
    if not angel_client.is_connected:
        return {"connected": False, "net": 0, "available_cash": 0,
                "used_margin": 0, "collateral": 0, "realised_mtom": 0}
    try:
        resp = angel_client.smart_api.rmsLimit()
        if not resp or not resp.get("status"):
            raise HTTPException(status_code=502, detail="RMS API returned error.")
        data = resp.get("data", {})
        return {
            "connected":       True,
            "net":             _f(data.get("net", 0)),
            "available_cash":  _f(data.get("availablecash", 0)),
            "used_margin":     _f(data.get("utiliseddebits", 0)),
            "collateral":      _f(data.get("collateral", 0)),
            "realised_mtom":   _f(data.get("m2mRealisedMtom", 0)),
            "unrealised_mtom": _f(data.get("m2mUnrealisedMtom", 0)),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("rmsLimit error: %s", exc)
        return {"connected": True, "net": 0, "available_cash": 0,
                "used_margin": 0, "collateral": 0,
                "realised_mtom": 0, "unrealised_mtom": 0,
                "error": str(exc)}


def _f(val) -> float:
    """Safe float conversion from string or number."""
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return 0.0
