"""
NSE F&O Bhavcopy Downloader
============================
Downloads official NSE end-of-day F&O bhavcopy CSV files.

Each file contains daily OHLC for every option and futures contract:
    INSTRUMENT, SYMBOL, EXPIRY_DT, STRIKE_PR, OPTION_TYP,
    OPEN, HIGH, LOW, CLOSE, SETTLE_PR, CONTRACTS, VAL_INLAKH,
    OPEN_INT, CHG_IN_OI, TIMESTAMP

URL pattern (NSE Archives):
    https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{YYYY}/{MON}/fo{DD}{MON}{YYYY}bhav.csv.zip

NSE blocks requests without proper browser headers — this module handles that.
"""
from __future__ import annotations

import csv
import io
import logging
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Generator, Iterator, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

# NSE requires a Referer and User-Agent or it returns 403
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Delay between consecutive downloads to be respectful to NSE servers
_INTER_REQUEST_DELAY = 1.5   # seconds


def bhavcopy_url(d: date) -> str:
    """Return the NSE archive URL for a given date."""
    dd  = f"{d.day:02d}"
    mon = _MONTH_ABBR[d.month]
    yyyy = d.year
    return (
        f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES"
        f"/{yyyy}/{mon}/fo{dd}{mon}{yyyy}bhav.csv.zip"
    )


def _fetch_zip(url: str, retries: int = 3) -> Optional[bytes]:
    """Download a ZIP file from NSE, returning raw bytes or None on failure."""
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=_HEADERS)
            with urlopen(req, timeout=30) as resp:
                return resp.read()
        except HTTPError as e:
            if e.code == 404:
                return None   # market holiday or weekend — no file
            logger.warning("HTTP %s fetching %s (attempt %d/%d)", e.code, url, attempt, retries)
        except Exception as e:
            logger.warning("Error fetching %s (attempt %d/%d): %s", url, attempt, retries, e)
        if attempt < retries:
            time.sleep(2 ** attempt)
    return None


def _parse_bhavcopy_csv(raw_csv: bytes) -> List[dict]:
    """
    Parse a raw bhavcopy CSV (already decompressed) into a list of row dicts.

    Only returns rows for OPTIDX and OPTSTK (options on indices and stocks).
    Futures (FUTIDX, FUTSTK) are excluded — we have those from Angel One.
    """
    rows = []
    reader = csv.DictReader(io.StringIO(raw_csv.decode("utf-8", errors="replace")))
    for row in reader:
        # Strip whitespace from all keys/values
        row = {k.strip(): v.strip() for k, v in row.items()}
        instrument = row.get("INSTRUMENT", "")
        if instrument not in ("OPTIDX", "OPTSTK"):
            continue
        try:
            rows.append({
                "instrument": instrument,
                "symbol":      row["SYMBOL"],
                "expiry":      row["EXPIRY_DT"],   # e.g. "25-APR-2024"
                "strike":      float(row["STRIKE_PR"]),
                "option_type": row["OPTION_TYP"],  # CE or PE
                "open":        float(row["OPEN"])  if row.get("OPEN")  else None,
                "high":        float(row["HIGH"])  if row.get("HIGH")  else None,
                "low":         float(row["LOW"])   if row.get("LOW")   else None,
                "close":       float(row["CLOSE"]) if row.get("CLOSE") else None,
                "settle":      float(row["SETTLE_PR"]) if row.get("SETTLE_PR") else None,
                "oi":          int(float(row["OPEN_INT"])) if row.get("OPEN_INT") else 0,
                "volume":      int(float(row["CONTRACTS"])) if row.get("CONTRACTS") else 0,
                "date":        row["TIMESTAMP"],  # e.g. "25-APR-2024"
            })
        except (KeyError, ValueError) as e:
            logger.debug("Skipping malformed row: %s — %s", row, e)
    return rows


def fetch_bhavcopy(d: date) -> Optional[List[dict]]:
    """
    Download and parse the NSE F&O bhavcopy for a given date.

    Returns list of option row dicts, or None if the date has no data
    (weekend, market holiday, or network error).
    """
    url  = bhavcopy_url(d)
    data = _fetch_zip(url)
    if data is None:
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # The ZIP always contains one CSV named like fo01JAN2024bhav.csv
            csv_name = next(
                (n for n in zf.namelist() if n.lower().endswith(".csv")), None
            )
            if csv_name is None:
                logger.warning("No CSV found in ZIP for %s", d)
                return None
            raw_csv = zf.read(csv_name)
    except zipfile.BadZipFile:
        logger.warning("Bad ZIP from NSE for %s", d)
        return None

    rows = _parse_bhavcopy_csv(raw_csv)
    logger.info("Bhavcopy %s: %d option rows", d, len(rows))
    return rows


def date_range(from_date: date, to_date: date) -> Iterator[date]:
    """Yield each calendar date from from_date to to_date inclusive."""
    current = from_date
    while current <= to_date:
        yield current
        current += timedelta(days=1)


def download_range(
    from_date: date,
    to_date: date,
    delay: float = _INTER_REQUEST_DELAY,
    skip_weekends: bool = True,
) -> Generator[tuple[date, List[dict]], None, None]:
    """
    Yield (date, rows) for each trading day in [from_date, to_date].

    Skips weekends and dates that return no data (market holidays).
    Sleeps `delay` seconds between requests.
    """
    for d in date_range(from_date, to_date):
        if skip_weekends and d.weekday() >= 5:   # 5=Sat, 6=Sun
            continue
        rows = fetch_bhavcopy(d)
        if rows is not None:
            yield d, rows
        time.sleep(delay)
