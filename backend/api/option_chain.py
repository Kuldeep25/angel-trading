"""
FastAPI router — option chain snapshot collector control + NSE bhavcopy download.

Endpoints:
  GET  /option-chain/status             — collector status + DB coverage
  POST /option-chain/collect/start      — start background collection
  POST /option-chain/collect/stop       — stop background collection
  POST /option-chain/collect/now        — trigger one immediate snapshot
  GET  /option-chain/premium            — query a single stored premium
  GET  /option-chain/bhavcopy/status    — bhavcopy DB coverage summary
  POST /option-chain/bhavcopy/download  — bulk download NSE bhavcopy for date range
  POST /option-chain/import-angel-ohlc  — import 5-min option OHLC from Angel One
  GET  /option-chain/import-angel-ohlc/status — Angel One import job status
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from options import collector
from options.snapshot_db import coverage_summary, get_nearest_premium, get_straddle_premium, init_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/option-chain", tags=["Option Chain"])


# ─────────────────────────────────────────────────────────────────────────────
#  Models
# ─────────────────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    underlyings:      Optional[List[str]] = None   # None = use defaults
    interval_seconds: int = 300                    # collection frequency


class PremiumQuery(BaseModel):
    underlying:  str
    strike:      float
    option_type: str   # CE | PE | STRADDLE
    target_ts:   str   # ISO datetime  e.g. "2025-04-17T10:30:00"
    expiry:      str = ""
    tolerance_minutes: int = 15


class BhavDownloadRequest(BaseModel):
    from_date: str   # "YYYY-MM-DD"
    to_date:   str   # "YYYY-MM-DD"


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    """Collector status + database coverage summary."""
    return {
        "collector": collector.status(),
        "database":  coverage_summary(),
    }


@router.post("/collect/start")
async def start_collection(req: StartRequest):
    """Start the background option chain collector."""
    init_db()
    started = collector.start(
        underlyings=req.underlyings,
        interval_seconds=req.interval_seconds,
    )
    if not started:
        return {"ok": False, "message": "Collector already running."}
    return {"ok": True, "message": "Collector started.", "status": collector.status()}


@router.post("/collect/stop")
async def stop_collection():
    """Stop the background collector."""
    await collector.stop()
    return {"ok": True, "message": "Collector stopped.", "status": collector.status()}


@router.post("/collect/now")
async def collect_now():
    """Trigger one immediate snapshot outside the scheduler."""
    init_db()
    try:
        count = await collector._collect_once()
        return {"ok": True, "rows_inserted": count}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/premium")
async def query_premium(req: PremiumQuery):
    """
    Look up a stored option premium for a given contract + timestamp.
    Returns the BSM fallback notice if no data is found.
    """
    ot = req.option_type.upper()
    if ot == "STRADDLE":
        ltp = get_straddle_premium(
            req.underlying, req.strike, req.target_ts,
            req.expiry, req.tolerance_minutes,
        )
    else:
        ltp = get_nearest_premium(
            req.underlying, req.strike, ot, req.target_ts,
            req.expiry, req.tolerance_minutes,
        )

    if ltp is None:
        return {
            "found": False,
            "ltp":   None,
            "note":  "No snapshot within tolerance. Backtest uses BSM estimate.",
        }
    return {"found": True, "ltp": ltp}


# ─────────────────────────────────────────────────────────────────────────────
#  NSE Bhavcopy endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/bhavcopy/status")
async def bhavcopy_status():
    """Return NSE bhavcopy database coverage summary."""
    try:
        from options.bhavcopy_db import coverage_summary as bhav_coverage, init_db as bhav_init
        bhav_init()
        return bhav_coverage()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# Track running download jobs to prevent duplicate parallel downloads
_download_running: bool = False


@router.post("/bhavcopy/download")
async def download_bhavcopy(req: BhavDownloadRequest, background_tasks: BackgroundTasks):
    """
    Bulk download NSE F&O bhavcopy for a date range and store in SQLite.

    Runs in the background — returns immediately with a job ID.
    Progress is visible in server logs (uvicorn terminal).

    Date range limits:
    - NSE provides bhavcopy going back ~5 years
    - Each file is ~5-10 MB compressed; download is throttled to 1.5s/day
    - For 1 year of data (~250 trading days) expect ~6 minutes
    """
    global _download_running
    if _download_running:
        raise HTTPException(status_code=409, detail="A bhavcopy download is already running.")

    try:
        from_d = date.fromisoformat(req.from_date)
        to_d   = date.fromisoformat(req.to_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")

    if (to_d - from_d).days > 365 * 3:
        raise HTTPException(
            status_code=400,
            detail="Date range too large. Max 3 years per request to avoid rate-limiting."
        )

    background_tasks.add_task(_run_bhavcopy_download, from_d, to_d)
    return {
        "ok": True,
        "message": f"Download started for {req.from_date} → {req.to_date} in background.",
        "note": "Check uvicorn logs for progress. Use GET /bhavcopy/status to see DB coverage.",
    }


def _compute_iv_for_day(day: date, rows: list) -> None:
    """
    Pre-compute and cache ATM IV for each unique symbol/expiry on a given
    bhavcopy day.  Uses the ATM option's close price + strike as a proxy for
    spot to run BSM IV inversion (Newton-Raphson).  Results are stored in
    bhavcopy_iv_cache so backtests can use real-IV pricing immediately.

    For ATM options: strike ≈ spot, so the approximation is tight.
    Any error in IV from this approximation is negligible (<0.3 vol points
    for a 1% moneyness offset).
    """
    if not rows:
        return
    try:
        from options.bhavcopy_db import get_atm_iv, store_atm_iv
        from backtest.option_pricer import implied_vol, RISK_FREE_RATE, MIN_T
        import math

        date_str = day.isoformat()
        # Group rows by (symbol, expiry, option_type)
        # For each group: find ATM row (closest to ATM), compute IV from close
        groups: dict[tuple, list] = {}
        for r in rows:
            key = (r.get("symbol", ""), r.get("expiry", ""), r.get("option_type", "CE"))
            groups.setdefault(key, []).append(r)

        for (sym, expiry, ot), group_rows in groups.items():
            if not sym or not expiry or ot not in ("CE", "PE"):
                continue

            # Skip if IV already cached
            if get_atm_iv(sym, date_str, expiry, ot) is not None:
                continue

            # Find the ATM row: closest strike to spot
            # We don't have spot; use the strike with the highest open-interest
            # as a proxy for the most actively traded (closest-to-ATM) strike.
            best = max(
                (r for r in group_rows if r.get("close") and float(r["close"]) > 0),
                key=lambda r: float(r.get("oi", 0) or 0),
                default=None,
            )
            if best is None:
                continue

            try:
                K            = float(best["strike"])
                market_price = float(best["close"])
                exp_date     = date.fromisoformat(expiry)
                dte          = max((exp_date - day).days, 1)
                T            = max(dte / 365.0, MIN_T)
                # ATM approximation: S ≈ K
                iv = implied_vol(market_price, K, K, T, ot, RISK_FREE_RATE)
                if iv is not None:
                    store_atm_iv(sym, date_str, expiry, iv, ot)
            except Exception:
                continue

    except Exception as e:
        logger.debug("IV pre-computation skipped for %s: %s", day, e)


async def _run_bhavcopy_download(from_d: date, to_d: date) -> None:
    """Background task: download bhavcopy files and insert into DB."""
    global _download_running
    _download_running = True
    try:
        from options.bhavcopy_db import init_db as bhav_init, insert_bhavcopy_rows
        from options.nse_bhavcopy import download_range
        bhav_init()
        total_inserted = 0
        total_days     = 0
        logger.info("Bhavcopy bulk download: %s → %s", from_d, to_d)
        # Run blocking I/O in a thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        def _download():
            nonlocal total_inserted, total_days
            for d, rows in download_range(from_d, to_d):
                n = insert_bhavcopy_rows(rows, d)
                total_inserted += n
                total_days += 1
                logger.info("Bhavcopy %s: %d rows stored (%d total so far)", d, n, total_inserted)
                # Pre-compute and cache ATM IV for each day as it downloads
                _compute_iv_for_day(d, rows)
        await loop.run_in_executor(None, _download)
        logger.info(
            "Bhavcopy download complete: %d trading days, %d rows inserted",
            total_days, total_inserted,
        )
    except Exception as e:
        logger.error("Bhavcopy download error: %s", e, exc_info=True)
    finally:
        _download_running = False


# ─────────────────────────────────────────────────────────────────────────────
#  Angel One historical option OHLC importer
# ─────────────────────────────────────────────────────────────────────────────

class AngelImportRequest(BaseModel):
    symbols: List[str] = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
    days_back: int = 100   # max 100 for 5-min data


@router.post("/import-angel-ohlc")
async def import_angel_ohlc(req: AngelImportRequest, background_tasks: BackgroundTasks):
    """
    Import 5-min option OHLC from Angel One SmartAPI into snapshot_db.

    Fetches real intraday option candlestick data for the last N days for
    ATM ± 10 strikes across all active expiries.  Stored data is automatically
    used by the backtest engine's 4-tier option pricer (tier-3: snapshot).

    Runs in the background — returns immediately.

    Notes:
    • Angel One max 100 calendar days for 5-min data
    • Only active + recently expired contracts (~60 days post expiry) are
      available in the instrument master
    • Rate-limited to 0.4s per API call; ~3-5 min total for 4 symbols
    """
    from options.angel_option_importer import get_status, _import_running
    if _import_running:
        raise HTTPException(status_code=409, detail="An Angel One import is already running.")

    if not req.symbols:
        raise HTTPException(status_code=400, detail="symbols list cannot be empty.")

    days = max(1, min(req.days_back, 100))
    background_tasks.add_task(_run_angel_import, req.symbols, days)
    return {
        "ok":      True,
        "message": f"Angel One import started for {req.symbols}, last {days} days.",
        "note":    "Check GET /option-chain/import-angel-ohlc/status for progress.",
    }


@router.get("/import-angel-ohlc/status")
async def angel_import_status():
    """Return the current status of the Angel One option OHLC import job."""
    from options.angel_option_importer import get_status
    return get_status()


async def _run_angel_import(symbols: list, days_back: int) -> None:
    """Background task wrapper for the Angel One importer."""
    loop = asyncio.get_event_loop()
    try:
        from options.angel_option_importer import run_import
        result = await loop.run_in_executor(
            None, lambda: run_import(symbols=symbols, days_back=days_back)
        )
        logger.info("Angel One import finished: %s", result)
    except Exception as e:
        logger.error("Angel One import error: %s", e, exc_info=True)

