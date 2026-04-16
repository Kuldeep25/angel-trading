"""
Instrument master cache from Angel One OpenAPI CDN.

Downloads the JSON instrument list once per day, caches it locally,
and provides fast lookups for symbol tokens, option chains, lot sizes.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, date
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_INSTRUMENTS_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)
_CACHE_FILE = os.path.join(os.path.dirname(__file__), "instruments_cache.json")
_META_FILE = os.path.join(os.path.dirname(__file__), "instruments_meta.json")

# In-memory index after load
_by_token: Dict[str, Dict] = {}
_by_symbol_exchange: Dict[str, Dict] = {}
_loaded = False


# ─────────────────────────────────────────────────────────────────────────────
#  Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def ensure_loaded(force: bool = False) -> None:
    """Load (or reload) the instrument master, downloading if stale."""
    global _loaded
    if _loaded and not force:
        return
    _load_or_download()
    _loaded = True


def get_token(symbol: str, exchange: str) -> Optional[str]:
    """Return the symbol token for a given trading symbol + exchange."""
    ensure_loaded()
    exchange = exchange.upper()
    sym = symbol.upper()
    key = f"{exchange}:{sym}"
    rec = _by_symbol_exchange.get(key)
    if rec:
        return rec["token"]
    # NSE equities are stored with a '-EQ' suffix (e.g. 'RELIANCE-EQ')
    if exchange == "NSE" and not sym.endswith("-EQ"):
        rec = _by_symbol_exchange.get(f"{exchange}:{sym}-EQ")
        if rec:
            return rec["token"]
    # BSE equities use just the numeric code; also try without suffix
    if exchange == "BSE" and not sym.endswith("-EQ") and not sym.endswith("-BE"):
        for suffix in ("-EQ", "-BE", "-A", ""):
            r = _by_symbol_exchange.get(f"{exchange}:{sym}{suffix}")
            if r:
                return r["token"]
    return None


def get_nearest_futures_token(underlying: str) -> Optional[tuple]:
    """
    Return (token, symbol, exchange) for the nearest non-expired futures contract
    for the given underlying (e.g. 'NIFTY', 'BANKNIFTY', 'RELIANCE').
    Prefers FUTIDX for indices, FUTSTK for stocks.
    Returns None if no active contract found.
    """
    ensure_loaded()
    from datetime import date as _date
    today = _date.today()
    underlying_upper = underlying.upper()

    best = None
    best_expiry = None
    for rec in _by_symbol_exchange.values():
        if rec.get("exch_seg", "").upper() != "NFO":
            continue
        itype = rec.get("instrumenttype", "").upper()
        if itype not in ("FUTIDX", "FUTSTK"):
            continue
        if rec.get("name", "").upper() != underlying_upper:
            continue
        expiry_str = rec.get("expiry", "")
        try:
            exp = datetime.strptime(expiry_str, "%d%b%Y").date()
        except ValueError:
            continue
        if exp < today:
            continue
        if best_expiry is None or exp < best_expiry:
            best_expiry = exp
            best = rec

    if best:
        return best["token"], best["symbol"], "NFO"
    return None


def get_all_futures_tokens(underlying: str) -> list:
    """
    Return ALL futures contracts for the underlying sorted by expiry (ascending).
    Each entry is (token, symbol, exchange, expiry_date).
    Includes both expired and active contracts.
    """
    ensure_loaded()
    underlying_upper = underlying.upper()
    contracts = []
    for rec in _by_symbol_exchange.values():
        if rec.get("exch_seg", "").upper() != "NFO":
            continue
        itype = rec.get("instrumenttype", "").upper()
        if itype not in ("FUTIDX", "FUTSTK"):
            continue
        if rec.get("name", "").upper() != underlying_upper:
            continue
        expiry_str = rec.get("expiry", "")
        try:
            exp = datetime.strptime(expiry_str, "%d%b%Y").date()
        except ValueError:
            continue
        contracts.append((rec["token"], rec["symbol"], "NFO", exp))
    contracts.sort(key=lambda x: x[3])
    return contracts


def get_instrument(symbol: str, exchange: str) -> Optional[Dict]:
    """Return full instrument record for a given symbol + exchange."""
    ensure_loaded()
    exchange = exchange.upper()
    sym = symbol.upper()
    rec = _by_symbol_exchange.get(f"{exchange}:{sym}")
    if rec:
        return rec
    if exchange == "NSE" and not sym.endswith("-EQ"):
        rec = _by_symbol_exchange.get(f"{exchange}:{sym}-EQ")
        if rec:
            return rec
    return None


def get_instrument_by_token(token: str, exchange: str) -> Optional[Dict]:
    """Return full instrument record by token + exchange."""
    ensure_loaded()
    key = f"{exchange.upper()}:{token}"
    return _by_token.get(key)


def get_lot_size(symbol: str, exchange: str = "NFO") -> int:
    """Return lot size for futures/options contracts (default 1 for equity).
    
    If the direct lookup returns 1 (not found or equity), automatically searches
    NFO futures contracts for the underlying to get the correct lot size.
    """
    ensure_loaded()
    key = f"{exchange.upper()}:{symbol.upper()}"
    rec = _by_symbol_exchange.get(key)
    if rec:
        try:
            ls = int(rec.get("lotsize", 1))
            if ls > 1:
                return ls
        except (TypeError, ValueError):
            pass

    # Fallback: search all NFO futures for this underlying to get the lot size
    # (handles cases like get_lot_size('NIFTY', 'NSE') or get_lot_size('NIFTY', 'NFO'))
    sym_upper = symbol.upper()
    # Strip common futures suffixes to get the underlying name
    underlying = sym_upper
    for suffix in ("FUT", "CE", "PE"):
        if underlying.endswith(suffix):
            # e.g. NIFTY28APR26FUT -> try NIFTY
            import re
            m = re.match(r'^([A-Z]+)', underlying)
            if m:
                underlying = m.group(1)
            break

    best_lot = 1
    for rec2 in _by_symbol_exchange.values():
        if rec2.get("exch_seg", "").upper() != "NFO":
            continue
        if rec2.get("name", "").upper() != underlying:
            continue
        itype = rec2.get("instrumenttype", "").upper()
        if itype not in ("FUTIDX", "FUTSTK", "OPTIDX", "OPTSTK"):
            continue
        try:
            ls = int(rec2.get("lotsize", 1))
            if ls > best_lot:
                best_lot = ls
        except (TypeError, ValueError):
            continue
    return best_lot


def get_option_chain(underlying: str, expiry: Optional[str] = None) -> List[Dict]:
    """
    Return all option contracts (CE + PE) for an underlying.

    Parameters
    ----------
    underlying : str
        e.g. "NIFTY", "BANKNIFTY", "RELIANCE"
    expiry : str, optional
        e.g. "26APR2024" – if None, returns all expiries

    Returns
    -------
    List of instrument records with keys:
        token, symbol, name, expiry, strike, optiontype, exchange, lotsize
    """
    ensure_loaded()
    results: List[Dict] = []
    prefix = underlying.upper()
    for rec in _by_symbol_exchange.values():
        if rec.get("exch_seg", "").upper() != "NFO":
            continue
        sym: str = rec.get("symbol", "")
        # NFO option symbols start with underlying name
        if not sym.upper().startswith(prefix):
            continue
        instrument_type: str = rec.get("instrumenttype", "").upper()
        if instrument_type not in ("OPTIDX", "OPTSTK"):
            continue
        if expiry and rec.get("expiry", "").upper() != expiry.upper():
            continue
        results.append(_normalize_instrument(rec))
    return results


def get_expiries(underlying: str) -> List[str]:
    """Return sorted list of unique expiry dates for an underlying."""
    ensure_loaded()
    expiries: set[str] = set()
    prefix = underlying.upper()
    for rec in _by_symbol_exchange.values():
        if rec.get("exch_seg", "").upper() != "NFO":
            continue
        sym: str = rec.get("symbol", "")
        if not sym.upper().startswith(prefix):
            continue
        instrument_type: str = rec.get("instrumenttype", "").upper()
        if instrument_type not in ("OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"):
            continue
        exp = rec.get("expiry", "")
        if exp:
            expiries.add(exp.upper())
    return sorted(expiries)


def search_equity(query: str, exchange: str = "NSE") -> List[Dict]:
    """Simple fuzzy search by name/symbol for equity."""
    ensure_loaded()
    query_upper = query.upper()
    results = []
    for rec in _by_symbol_exchange.values():
        if rec.get("exch_seg", "").upper() != exchange.upper():
            continue
        if query_upper in rec.get("symbol", "").upper() or query_upper in rec.get("name", "").upper():
            results.append(_normalize_instrument(rec))
            if len(results) >= 20:
                break
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_instrument(rec: Dict) -> Dict:
    return {
        "token": rec.get("token", ""),
        "symbol": rec.get("symbol", ""),
        "name": rec.get("name", ""),
        "expiry": rec.get("expiry", ""),
        "strike": rec.get("strike", ""),
        "optiontype": rec.get("optiontype", ""),
        "exchange": rec.get("exch_seg", ""),
        "lotsize": rec.get("lotsize", "1"),
        "tick_size": rec.get("tick_size", ""),
        "instrumenttype": rec.get("instrumenttype", ""),
    }


def _load_or_download() -> None:
    global _by_token, _by_symbol_exchange

    # Check if cache is fresh (downloaded today)
    if _is_cache_fresh():
        logger.info("Loading instruments from local cache.")
        records = _load_cache()
    else:
        logger.info("Downloading fresh instrument master from Angel One CDN…")
        records = _download()
        if records:
            _save_cache(records)
        else:
            # Fall back to stale cache if download fails
            logger.warning("Download failed — using stale cache.")
            records = _load_cache()

    if not records:
        logger.error("No instrument data available.")
        return

    _by_token = {}
    _by_symbol_exchange = {}
    for rec in records:
        exchange = rec.get("exch_seg", "").upper()
        token = str(rec.get("token", ""))
        symbol = str(rec.get("symbol", "")).upper()
        if token:
            _by_token[f"{exchange}:{token}"] = rec
        if symbol:
            _by_symbol_exchange[f"{exchange}:{symbol}"] = rec

    logger.info("Instrument master loaded: %d records.", len(records))


def _is_cache_fresh() -> bool:
    if not os.path.exists(_META_FILE) or not os.path.exists(_CACHE_FILE):
        return False
    try:
        with open(_META_FILE) as f:
            meta = json.load(f)
        cached_date = meta.get("date", "")
        return cached_date == date.today().isoformat()
    except Exception:
        return False


def _load_cache() -> List[Dict]:
    if not os.path.exists(_CACHE_FILE):
        return []
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Cache load error: %s", exc)
        return []


def _save_cache(records: List[Dict]) -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f)
        with open(_META_FILE, "w") as f:
            json.dump({"date": date.today().isoformat(), "count": len(records)}, f)
        logger.info("Instrument cache saved (%d records).", len(records))
    except Exception as exc:
        logger.error("Cache save error: %s", exc)


def _download() -> List[Dict]:
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.get(_INSTRUMENTS_URL)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("Instrument master download failed: %s", exc)
        return []
