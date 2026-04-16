"""
Options engine — strike selection, expiry handling, lot sizes, and
live option contract lookup using the Angel One instrument master.
"""
from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

from angel.symbols import (
    get_instrument,
    get_lot_size,
    get_option_chain,
    get_expiries,
    get_token,
)
from angel.client import angel_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Strike helpers
# ─────────────────────────────────────────────────────────────────────────────

_STRIKE_STEPS: Dict[str, int] = {
    "NIFTY":     50,
    "BANKNIFTY": 100,
    "FINNIFTY":  50,
    "MIDCPNIFTY": 25,
}
_DEFAULT_STRIKE_STEP = 50


def get_strike_step(underlying: str) -> int:
    """Return the standard strike interval for a given underlying."""
    return _STRIKE_STEPS.get(underlying.upper(), _DEFAULT_STRIKE_STEP)


def get_atm_strike(spot_price: float, underlying: str = "") -> int:
    """Round spot price to the nearest valid strike."""
    step = get_strike_step(underlying)
    return int(round(spot_price / step) * step)


def get_itm_strike(
    spot_price: float, underlying: str, option_type: str, depth: int = 1
) -> int:
    """
    Return an ITM strike n levels away from ATM.

    depth=1 means 1 step ITM, depth=2 means 2 steps ITM, etc.
    """
    step = get_strike_step(underlying)
    atm  = get_atm_strike(spot_price, underlying)
    if option_type.upper() == "CE":   # Call ITM → strike < spot
        return atm - step * depth
    else:                              # Put ITM → strike > spot
        return atm + step * depth


def get_otm_strike(
    spot_price: float, underlying: str, option_type: str, depth: int = 1
) -> int:
    """Return an OTM strike n levels away from ATM."""
    step = get_strike_step(underlying)
    atm  = get_atm_strike(spot_price, underlying)
    if option_type.upper() == "CE":   # Call OTM → strike > spot
        return atm + step * depth
    else:                              # Put OTM → strike < spot
        return atm - step * depth


# ─────────────────────────────────────────────────────────────────────────────
#  Expiry helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_nearest_expiry(
    underlying: str, monthly: bool = False
) -> Optional[str]:
    """
    Return the nearest weekly expiry for index options, or
    monthly expiry if monthly=True.

    Returns date string as stored in instrument master (e.g. "25APR2024").
    """
    expiries = get_expiries(underlying)
    if not expiries:
        logger.warning("No expiries found for %s", underlying)
        return None

    today = date.today()
    parsed: List[Tuple[date, str]] = []
    for exp_str in expiries:
        try:
            exp_date = datetime.strptime(exp_str, "%d%b%Y").date()
            parsed.append((exp_date, exp_str))
        except ValueError:
            continue

    future = [(d, s) for d, s in parsed if d >= today]
    if not future:
        return None

    future.sort(key=lambda x: x[0])

    if monthly:
        # Monthly expiry = last Thursday of the month (approximate: take expiry
        # whose month differs from the nearest weekly)
        nearest_date = future[0][0]
        monthly_expiries = [
            (d, s) for d, s in future if d.month != nearest_date.month
            or (d.month == nearest_date.month and len([
                (dd, ss) for dd, ss in future if dd.month == d.month
            ]) == 1)
        ]
        if monthly_expiries:
            return monthly_expiries[0][1]

    return future[0][1]


# ─────────────────────────────────────────────────────────────────────────────
#  Contract lookup
# ─────────────────────────────────────────────────────────────────────────────

def get_option_contract(
    underlying: str,
    strike: int,
    option_type: str,
    expiry: str,
    exchange: str = "NFO",
) -> Optional[Dict[str, Any]]:
    """
    Find an option contract in the instrument master.

    Parameters
    ----------
    underlying  : str  e.g. "NIFTY", "BANKNIFTY"
    strike      : int  e.g. 22000
    option_type : str  "CE" or "PE"
    expiry      : str  e.g. "25APR2024"
    exchange    : str  "NFO" (default)

    Returns
    -------
    Instrument record dict or None if not found.
    """
    chain = get_option_chain(underlying, expiry)
    opt_type_upper = option_type.upper()
    for contract in chain:
        try:
            contract_strike = int(float(contract.get("strike", 0)))
        except (TypeError, ValueError):
            continue
        if (
            contract_strike == strike
            and contract.get("optiontype", "").upper() == opt_type_upper
        ):
            return contract
    return None


def get_straddle_contracts(
    underlying: str,
    spot_price: float,
    expiry: Optional[str] = None,
    exchange: str = "NFO",
) -> Tuple[Optional[Dict], Optional[Dict]]:
    """
    Return (call_contract, put_contract) for an ATM straddle.
    If expiry is None, uses the nearest weekly expiry.
    """
    if expiry is None:
        expiry = get_nearest_expiry(underlying)
    if expiry is None:
        return None, None

    strike = get_atm_strike(spot_price, underlying)
    call = get_option_contract(underlying, strike, "CE", expiry, exchange)
    put  = get_option_contract(underlying, strike, "PE", expiry, exchange)
    return call, put


# ─────────────────────────────────────────────────────────────────────────────
#  Spot price (live)
# ─────────────────────────────────────────────────────────────────────────────

def get_spot_price(underlying: str, exchange: str = "NSE") -> Optional[float]:
    """
    Fetch the current LTP (spot price) from Angel One for the given underlying.
    """
    token = get_token(underlying, exchange)
    if token is None:
        logger.warning("Token not found for %s:%s", exchange, underlying)
        return None
    try:
        params = {
            "exchange": exchange.upper(),
            "tradingsymbol": underlying,
            "symboltoken": token,
        }
        resp = angel_client.smart_api.getLtpData(params)
        if resp and resp.get("status"):
            ltp = resp["data"].get("ltp", 0)
            return float(ltp) / 100.0  # paise → rupees
    except Exception as exc:
        logger.error("getLtpData error for %s: %s", underlying, exc)
    return None
