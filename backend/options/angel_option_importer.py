"""
Angel One Historical Option OHLC Importer
==========================================
Fetches 5-min candlestick data for ATM ± range of option contracts using
the Angel One SmartAPI historical endpoint and stores into snapshot_db.

Angel One limits:
  • 5-min data  → max 100 calendar days back
  • 3 req/sec   → we wait 0.4s between calls

How it works:
  1. Load Angel One instrument master (all active + recently expired contracts)
  2. For each symbol/expiry, find strikes in ATM ± range (covers all likely ATM
     values over the 100-day window)
  3. Fetch 5-min OHLC for each contract via SmartAPI getCandleData
  4. Store into snapshot_db so the 4-tier option pricer uses real data
     automatically — no backtest code changes needed

Limitations:
  • Angel One ScripMaster only contains contracts active at download time
    plus recently expired contracts (~30-60 days post expiry).
    Contracts expired >60 days ago are NOT fetchable.
  • For daily option backtest accuracy: NSE bhavcopy (already built) is better.
  • For intraday (5-min) backtest accuracy this is the best free source.

Run via:
    from options.angel_option_importer import run_import
    result = run_import()
Or via the API endpoint POST /option-chain/import-angel-ohlc
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

# Symbols to import (index options only — OPTIDX)
DEFAULT_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

# How many strikes either side of the centre strike to import
# NIFTY step=50 → 10 strikes = ±500 points (covers ~3 months of ATM range)
# BANKNIFTY step=100 → 10 strikes = ±1000 points
ATM_RADIUS = 10    # strikes either side of the midpoint of available strikes

INTERVAL = "FIVE_MINUTE"

# Angel One 5-min data limit: 100 calendar days per request
MAX_DAYS_BACK = 100

# Rate-limit: Angel One allows ~3 req/sec; 0.4s gap is safe
_SLEEP = 0.4

# ── Import state (used by status endpoint) ────────────────────────────────────
_import_running: bool = False
_import_progress: dict = {}


# ── Main entry point ──────────────────────────────────────────────────────────

def run_import(
    symbols: list[str] | None = None,
    days_back: int = MAX_DAYS_BACK,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """
    Bulk-import 5-min option OHLC from Angel One into snapshot_db.

    Parameters
    ----------
    symbols     : list of underlying names, default DEFAULT_SYMBOLS
    days_back   : how many calendar days back to fetch (max 100 for 5-min)
    progress_cb : optional callable(sym, expiry, strike, ot, n_bars)

    Returns
    -------
    dict: {total_rows, total_contracts, skipped, errors}
    """
    global _import_running, _import_progress

    if _import_running:
        return {"error": "Import already running"}

    _import_running = True
    _import_progress = {"status": "running", "total_rows": 0, "total_contracts": 0, "current": ""}

    try:
        return _do_import(symbols or DEFAULT_SYMBOLS, days_back, progress_cb)
    finally:
        _import_running = False
        _import_progress["status"] = "idle"


def get_status() -> dict:
    return {"running": _import_running, **_import_progress}


# ── Core logic ────────────────────────────────────────────────────────────────

def _do_import(symbols: list[str], days_back: int, progress_cb) -> dict:
    from angel.symbols import ensure_loaded, get_option_chain
    from data.engine import fetch_historical
    from options.snapshot_db import insert_snapshots, init_db

    init_db()
    ensure_loaded()

    to_dt   = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0)
    from_dt = to_dt - timedelta(days=days_back)
    from_str = from_dt.strftime("%Y-%m-%d 09:15")
    to_str   = to_dt.strftime("%Y-%m-%d 15:30")

    total_rows      = 0
    total_contracts = 0
    skipped         = 0
    errors          = 0

    for sym in symbols:
        logger.info("Importing option OHLC for %s …", sym)
        _import_progress["current"] = sym

        contracts = get_option_chain(sym)
        if not contracts:
            logger.warning("%s: no option contracts found in instrument master", sym)
            skipped += 1
            continue

        # Group by expiry
        expiry_map: dict[str, list] = {}
        for c in contracts:
            exp = c.get("expiry", "").upper()
            if exp:
                expiry_map.setdefault(exp, []).append(c)

        logger.info("%s: %d expiries in instrument master", sym, len(expiry_map))

        for expiry_str, exp_contracts in sorted(expiry_map.items()):
            # Parse expiry date — Angel One format: "26APR2026"
            try:
                exp_date = datetime.strptime(expiry_str, "%d%b%Y").date()
            except ValueError:
                logger.debug("Cannot parse expiry '%s', skipping", expiry_str)
                continue

            # Skip contracts that expired before our fetch window starts
            if exp_date < from_dt.date():
                logger.debug("Expiry %s is before fetch window, skipping", expiry_str)
                continue

            # Build strike list
            strikes_in_master = sorted(
                set(float(c.get("strike", 0)) for c in exp_contracts if c.get("strike"))
            )
            if not strikes_in_master:
                continue

            # Pick ATM ± radius strikes around the midpoint
            mid = len(strikes_in_master) // 2
            lo  = max(0, mid - ATM_RADIUS)
            hi  = min(len(strikes_in_master), mid + ATM_RADIUS + 1)
            target_strikes = strikes_in_master[lo:hi]

            logger.info(
                "%s %s: importing %d strikes × CE/PE (%s … %s)",
                sym, expiry_str, len(target_strikes),
                target_strikes[0] if target_strikes else "-",
                target_strikes[-1] if target_strikes else "-",
            )

            # Build a fast token lookup: (strike, ot) → token
            token_map: dict[tuple, str] = {}
            for c in exp_contracts:
                try:
                    k  = float(c.get("strike", 0))
                    ot = (c.get("optiontype") or c.get("option_type") or "").upper()
                    tk = c.get("token", "")
                    if k and ot and tk:
                        token_map[(k, ot)] = tk
                except Exception:
                    continue

            for strike in target_strikes:
                for ot in ("CE", "PE"):
                    token = token_map.get((strike, ot))
                    if not token:
                        skipped += 1
                        continue

                    try:
                        raw = fetch_historical(
                            symboltoken=token,
                            exchange="NFO",
                            interval=INTERVAL,
                            from_date=from_str,
                            to_date=to_str,
                        )
                    except Exception as e:
                        logger.warning(
                            "%s %s %.0f %s: fetch error — %s", sym, expiry_str, strike, ot, e
                        )
                        errors += 1
                        time.sleep(_SLEEP)
                        continue

                    if not raw:
                        skipped += 1
                        time.sleep(_SLEEP)
                        continue

                    # raw candle: [timestamp, open, high, low, close, volume]
                    rows = []
                    for candle in raw:
                        try:
                            ts  = str(candle[0])
                            ltp = float(candle[4])   # close price as LTP proxy
                            vol = int(candle[5] or 0)
                            rows.append((ts, sym.upper(), expiry_str.upper(),
                                         strike, ot, ltp, vol, None))
                        except (IndexError, ValueError, TypeError):
                            continue

                    if rows:
                        insert_snapshots(rows)
                        total_rows      += len(rows)
                        total_contracts += 1
                        _import_progress["total_rows"]      = total_rows
                        _import_progress["total_contracts"] = total_contracts
                        logger.info(
                            "  ✓ %s %s %.0f %s: %d bars stored",
                            sym, expiry_str, strike, ot, len(rows)
                        )
                        if progress_cb:
                            progress_cb(sym, expiry_str, strike, ot, len(rows))

                    time.sleep(_SLEEP)   # rate limit

    result = {
        "total_rows":      total_rows,
        "total_contracts": total_contracts,
        "skipped":         skipped,
        "errors":          errors,
    }
    _import_progress.update(result)
    logger.info("Angel option import complete: %s", result)
    return result
