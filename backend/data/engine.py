"""
Historical data engine — fetches candles from Angel One SmartAPI
with automatic date-range chunking to stay within per-interval limits.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import List, Tuple

from angel.client import angel_client

logger = logging.getLogger(__name__)

# Maximum days per request per interval (Angel One documented limits)
_INTERVAL_MAX_DAYS: dict[str, int] = {
    "ONE_MINUTE":     30,
    "THREE_MINUTE":   60,
    "FIVE_MINUTE":    100,
    "TEN_MINUTE":     100,
    "FIFTEEN_MINUTE": 200,
    "THIRTY_MINUTE":  200,
    "ONE_HOUR":       400,
    "ONE_DAY":        2000,
}

# Seconds to wait between API calls (3 req/sec limit → ~0.35 s gap)
_RATE_LIMIT_SLEEP = 0.4
_MAX_RETRIES = 3


def fetch_historical(
    symboltoken: str,
    exchange: str,
    interval: str,
    from_date: str,
    to_date: str,
) -> List[List]:
    """
    Fetch OHLCV candles from Angel One, automatically splitting the date
    range into chunks that comply with the API's per-interval limits.

    Parameters
    ----------
    symboltoken : str   Angel One instrument token
    exchange    : str   e.g. "NSE", "NFO", "BSE", "MCX"
    interval    : str   ONE_MINUTE | THREE_MINUTE | FIVE_MINUTE | TEN_MINUTE |
                        FIFTEEN_MINUTE | THIRTY_MINUTE | ONE_HOUR | ONE_DAY
    from_date   : str   "YYYY-MM-DD HH:MM"
    to_date     : str   "YYYY-MM-DD HH:MM"

    Returns
    -------
    List of raw candles: [timestamp, open, high, low, close, volume]
    """
    interval = interval.upper()
    if interval not in _INTERVAL_MAX_DAYS:
        raise ValueError(
            f"Invalid interval '{interval}'. "
            f"Allowed: {list(_INTERVAL_MAX_DAYS.keys())}"
        )

    max_days = _INTERVAL_MAX_DAYS[interval]
    fmt = "%Y-%m-%d %H:%M"

    def _parse_date(s: str) -> datetime:
        for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, f)
            except ValueError:
                pass
        raise ValueError(f"Cannot parse date '{s}'. Expected YYYY-MM-DD or YYYY-MM-DD HH:MM")

    dt_from = _parse_date(from_date)
    dt_to = _parse_date(to_date)

    chunks = _build_chunks(dt_from, dt_to, max_days)
    logger.info(
        "Fetching %s %s %s: %d chunk(s) for range %s → %s",
        exchange, symboltoken, interval, len(chunks), from_date, to_date,
    )

    all_candles: List[List] = []
    for chunk_from, chunk_to in chunks:
        candles = _fetch_chunk(
            symboltoken, exchange, interval,
            chunk_from.strftime(fmt), chunk_to.strftime(fmt),
        )
        all_candles.extend(candles)
        time.sleep(_RATE_LIMIT_SLEEP)

    # Deduplicate by timestamp (first occurrence wins)
    seen: set[str] = set()
    unique: List[List] = []
    for c in all_candles:
        ts = c[0]
        if ts not in seen:
            seen.add(ts)
            unique.append(c)

    unique.sort(key=lambda c: c[0])
    logger.info("Total candles fetched: %d", len(unique))
    return unique


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_chunks(
    dt_from: datetime, dt_to: datetime, max_days: int
) -> List[Tuple[datetime, datetime]]:
    """Split [dt_from, dt_to] into at-most max_days-wide sub-ranges."""
    chunks: List[Tuple[datetime, datetime]] = []
    current = dt_from
    delta = timedelta(days=max_days)
    while current < dt_to:
        end = min(current + delta, dt_to)
        chunks.append((current, end))
        current = end + timedelta(minutes=1)
    return chunks


def _fetch_chunk(
    symboltoken: str,
    exchange: str,
    interval: str,
    from_date: str,
    to_date: str,
) -> List[List]:
    params = {
        "exchange": exchange.upper(),
        "symboltoken": symboltoken,
        "interval": interval,
        "fromdate": from_date,
        "todate": to_date,
    }
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = angel_client.smart_api.getCandleData(params)
            logger.debug("getCandleData raw response: %s", resp)
            if resp and resp.get("status"):
                data = resp.get("data", []) or []
                if not data:
                    msg = resp.get("message", "")
                    logger.warning(
                        "getCandleData returned empty data (attempt %d). "
                        "message=%r params=%s", attempt, msg, params
                    )
                return data
            logger.warning(
                "getCandleData returned non-OK (attempt %d): %s", attempt, resp
            )
        except Exception as exc:
            logger.error(
                "getCandleData error (attempt %d/%d): %s",
                attempt, _MAX_RETRIES, exc,
            )
        # Exponential back-off: 1s, 2s, 4s
        if attempt < _MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))

    logger.error(
        "Failed to fetch chunk %s – %s after %d attempts.",
        from_date, to_date, _MAX_RETRIES,
    )
    return []
