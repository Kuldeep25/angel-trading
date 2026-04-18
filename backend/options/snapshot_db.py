"""
SQLite store for option chain price snapshots.

Schema
──────
Table: option_snapshots
  id           INTEGER PRIMARY KEY
  ts           TEXT     NOT NULL   -- ISO datetime (IST, minute-level precision)
  underlying   TEXT     NOT NULL   -- e.g. NIFTY, BANKNIFTY
  expiry       TEXT     NOT NULL   -- e.g. 24APR2025
  strike       REAL     NOT NULL
  option_type  TEXT     NOT NULL   -- CE | PE
  ltp          REAL     NOT NULL   -- last traded price (₹)
  oi           INTEGER             -- open interest
  iv           REAL                -- implied volatility (if available from feed)

Index on (underlying, expiry, strike, option_type, ts) for fast backtest lookup.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "option_snapshots.db")


def get_db_path() -> str:
    return _DB_PATH


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    con = sqlite3.connect(_DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    """Create tables + indexes if they don't exist."""
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS option_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    NOT NULL,
                underlying  TEXT    NOT NULL,
                expiry      TEXT    NOT NULL,
                strike      REAL    NOT NULL,
                option_type TEXT    NOT NULL,
                ltp         REAL    NOT NULL,
                oi          INTEGER,
                iv          REAL
            );

            CREATE INDEX IF NOT EXISTS idx_lookup
                ON option_snapshots (underlying, expiry, strike, option_type, ts);

            CREATE INDEX IF NOT EXISTS idx_ts
                ON option_snapshots (ts);
        """)
    logger.info("option_snapshots DB initialised at %s", _DB_PATH)


def insert_snapshots(rows: List[Tuple]) -> None:
    """
    Bulk insert snapshot rows.

    Each row: (ts, underlying, expiry, strike, option_type, ltp, oi, iv)
    """
    if not rows:
        return
    with _conn() as con:
        con.executemany(
            """INSERT OR IGNORE INTO option_snapshots
               (ts, underlying, expiry, strike, option_type, ltp, oi, iv)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def get_nearest_premium(
    underlying: str,
    strike: float,
    option_type: str,
    target_ts: str,          # ISO datetime string
    expiry: str = "",        # blank = nearest available expiry
    tolerance_minutes: int = 15,
) -> Optional[float]:
    """
    Return the stored LTP closest in time to target_ts for the given contract.
    Returns None if no data within tolerance_minutes window exists.
    """
    from datetime import timedelta

    try:
        dt = datetime.fromisoformat(target_ts)
    except ValueError:
        return None

    lo = (dt - timedelta(minutes=tolerance_minutes)).isoformat(timespec="seconds")
    hi = (dt + timedelta(minutes=tolerance_minutes)).isoformat(timespec="seconds")

    with _conn() as con:
        if expiry:
            row = con.execute(
                """SELECT ltp FROM option_snapshots
                   WHERE underlying=? AND expiry=? AND strike=? AND option_type=?
                   AND ts BETWEEN ? AND ?
                   ORDER BY ABS(strftime('%s', ts) - strftime('%s', ?))
                   LIMIT 1""",
                (underlying.upper(), expiry.upper(), strike,
                 option_type.upper(), lo, hi, target_ts),
            ).fetchone()
        else:
            row = con.execute(
                """SELECT ltp FROM option_snapshots
                   WHERE underlying=? AND strike=? AND option_type=?
                   AND ts BETWEEN ? AND ?
                   ORDER BY ABS(strftime('%s', ts) - strftime('%s', ?))
                   LIMIT 1""",
                (underlying.upper(), strike, option_type.upper(),
                 lo, hi, target_ts),
            ).fetchone()

    return float(row["ltp"]) if row else None


def get_straddle_premium(
    underlying: str,
    strike: float,
    target_ts: str,
    expiry: str = "",
    tolerance_minutes: int = 15,
) -> Optional[float]:
    """Return CE + PE combined (straddle) premium from stored snapshots."""
    ce = get_nearest_premium(underlying, strike, "CE", target_ts, expiry, tolerance_minutes)
    pe = get_nearest_premium(underlying, strike, "PE", target_ts, expiry, tolerance_minutes)
    if ce is None or pe is None:
        return None
    return round(ce + pe, 2)


def coverage_summary() -> dict:
    """Return a summary dict of what data is stored — used by the status endpoint."""
    with _conn() as con:
        underlyings = [r[0] for r in con.execute(
            "SELECT DISTINCT underlying FROM option_snapshots ORDER BY underlying"
        ).fetchall()]
        total = con.execute("SELECT COUNT(*) FROM option_snapshots").fetchone()[0]
        if total:
            date_range = con.execute(
                "SELECT MIN(ts), MAX(ts) FROM option_snapshots"
            ).fetchone()
            return {
                "total_rows": total,
                "underlyings": underlyings,
                "from_ts": date_range[0],
                "to_ts":   date_range[1],
            }
    return {"total_rows": 0, "underlyings": [], "from_ts": None, "to_ts": None}
