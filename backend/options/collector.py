"""
Option chain snapshot collector.

Runs as a background asyncio task during market hours (09:14–15:31 IST).
Every `interval_seconds` (default 300 = 5 min) it:
  1. Fetches all NFO option contracts for each configured underlying
  2. Calls Angel One getLTP (batched in groups of 50)
  3. Stores (ts, underlying, expiry, strike, option_type, ltp, oi, iv) to SQLite

Start / stop via the FastAPI lifespan or the /option-chain/collect endpoints.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional

from angel.client import angel_client
from angel import symbols as instrument_master
from options.snapshot_db import init_db, insert_snapshots

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Underlyings to track — add/remove as needed
DEFAULT_UNDERLYINGS = [
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "SENSEX",
]

MARKET_OPEN  = dtime(9, 14)    # start slightly before 9:15 to capture opening
MARKET_CLOSE = dtime(15, 31)   # run one last snapshot after 15:30

BATCH_SIZE = 50   # Angel One getLTP accepts max 50 tokens per call


# ─────────────────────────────────────────────────────────────────────────────
#  Singleton state
# ─────────────────────────────────────────────────────────────────────────────

class _CollectorState:
    def __init__(self) -> None:
        self.running:    bool = False
        self.task:       Optional[asyncio.Task] = None
        self.last_run:   Optional[str] = None
        self.last_count: int = 0
        self.errors:     int = 0
        self.underlyings: List[str] = list(DEFAULT_UNDERLYINGS)
        self.interval:   int = 300   # seconds

_state = _CollectorState()


# ─────────────────────────────────────────────────────────────────────────────
#  Core collection logic
# ─────────────────────────────────────────────────────────────────────────────

def _is_market_hours() -> bool:
    now = datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def _get_option_contracts(underlying: str) -> List[Dict[str, Any]]:
    """Return all NFO option contracts for a given underlying."""
    try:
        return instrument_master.get_option_chain(underlying)
    except Exception as exc:
        logger.warning("get_option_chain(%s) error: %s", underlying, exc)
        return []


def _batch_ltp(tokens_exchange: List[tuple]) -> Dict[str, float]:
    """
    Fetch LTP for a list of (token, exchange) tuples.
    Returns {token: ltp} dict.
    """
    result: Dict[str, float] = {}
    if not angel_client.is_connected:
        return result

    # Group into batches of BATCH_SIZE
    for i in range(0, len(tokens_exchange), BATCH_SIZE):
        batch = tokens_exchange[i : i + BATCH_SIZE]
        payload = [
            {"exchange": exc, "tradingsymbol": sym, "symboltoken": tok}
            for tok, sym, exc in batch
        ]
        try:
            resp = angel_client.smart_api.getQuote(
                mode="LTP",
                exchangeTokens={
                    exc: [tok for tok, sym, exc2 in batch if exc2 == exc]
                    for exc in {exc for _, _, exc in batch}
                },
            )
            if resp and resp.get("status") and resp.get("data"):
                for exc_data in resp["data"].values():
                    if not isinstance(exc_data, list):
                        continue
                    for item in exc_data:
                        tok = str(item.get("symboltoken", ""))
                        ltp = item.get("ltp")
                        oi  = item.get("opnInterest")
                        if tok and ltp is not None:
                            result[tok] = {"ltp": float(ltp), "oi": oi}
        except Exception as exc_err:
            logger.warning("getLTP batch error: %s", exc_err)

    return result


async def _collect_once() -> int:
    """
    Run one full snapshot collection pass.
    Returns the number of rows inserted.
    """
    ts = datetime.now().replace(second=0, microsecond=0).isoformat(timespec="seconds")
    rows: list = []

    for underlying in _state.underlyings:
        contracts = _get_option_contracts(underlying)
        if not contracts:
            logger.debug("No contracts found for %s", underlying)
            continue

        # Build (token, symbol, exchange) list for batch LTP fetch
        token_list = [
            (str(c["token"]), c["symbol"], c.get("exchange", "NFO"))
            for c in contracts
            if c.get("token")
        ]

        # Run in thread executor to avoid blocking event loop
        loop = asyncio.get_event_loop()
        ltp_map: Dict[str, Any] = await loop.run_in_executor(
            None, _batch_ltp, token_list
        )

        for contract in contracts:
            tok  = str(contract.get("token", ""))
            data = ltp_map.get(tok)
            if data is None:
                continue
            ltp = data.get("ltp", 0.0)
            if ltp <= 0:
                continue

            rows.append((
                ts,
                underlying.upper(),
                contract.get("expiry", "").upper(),
                float(contract.get("strike", 0.0)),
                contract.get("optiontype", "CE").upper(),
                ltp,
                data.get("oi"),
                None,   # IV — not available from LTP endpoint; computed post-hoc if needed
            ))

    await asyncio.get_event_loop().run_in_executor(None, insert_snapshots, rows)
    logger.info("Option snapshot: %d rows saved at %s", len(rows), ts)
    return len(rows)


async def _collector_loop() -> None:
    """Main background loop."""
    init_db()
    logger.info(
        "Option chain collector started — underlyings: %s, interval: %ds",
        _state.underlyings, _state.interval,
    )
    while _state.running:
        try:
            if _is_market_hours():
                count = await _collect_once()
                _state.last_run   = datetime.now().isoformat(timespec="seconds")
                _state.last_count = count
            else:
                logger.debug("Outside market hours — skipping collection.")
        except Exception as exc:
            _state.errors += 1
            logger.error("Collector error: %s", exc, exc_info=True)

        # Sleep in small chunks so stop() is responsive
        for _ in range(_state.interval):
            if not _state.running:
                break
            await asyncio.sleep(1)

    logger.info("Option chain collector stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def start(
    underlyings: Optional[List[str]] = None,
    interval_seconds: int = 300,
) -> bool:
    """Start the background collector. Returns False if already running."""
    if _state.running:
        return False
    if underlyings:
        _state.underlyings = [u.upper() for u in underlyings]
    _state.interval = interval_seconds
    _state.running  = True
    _state.errors   = 0
    _state.task = asyncio.create_task(_collector_loop())
    return True


async def stop() -> None:
    """Stop the background collector gracefully."""
    _state.running = False
    if _state.task and not _state.task.done():
        try:
            await asyncio.wait_for(_state.task, timeout=5.0)
        except asyncio.TimeoutError:
            _state.task.cancel()


def status() -> Dict[str, Any]:
    return {
        "running":    _state.running,
        "last_run":   _state.last_run,
        "last_count": _state.last_count,
        "errors":     _state.errors,
        "underlyings": _state.underlyings,
        "interval_seconds": _state.interval,
    }
