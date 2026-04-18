"""
NSE F&O Bhavcopy SQLite Store
==============================
Stores real daily OHLC data for option contracts downloaded from NSE bhavcopy.

Schema:
    bhavcopy_options (
        id           INTEGER PRIMARY KEY,
        date         TEXT,        -- "YYYY-MM-DD"
        symbol       TEXT,        -- e.g. "NIFTY", "BANKNIFTY", "RELIANCE"
        expiry       TEXT,        -- "YYYY-MM-DD"
        strike       REAL,
        option_type  TEXT,        -- "CE" or "PE"
        open         REAL,
        high         REAL,
        low          REAL,
        close        REAL,
        settle       REAL,
        oi           INTEGER,
        volume       INTEGER,
        UNIQUE(date, symbol, expiry, strike, option_type)
    )
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent / "bhavcopy.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bhavcopy_options (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    expiry      TEXT    NOT NULL,
    strike      REAL    NOT NULL,
    option_type TEXT    NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    settle      REAL,
    oi          INTEGER DEFAULT 0,
    volume      INTEGER DEFAULT 0,
    iv          REAL,    -- back-calculated implied volatility (annualised)
    UNIQUE(date, symbol, expiry, strike, option_type)
);
CREATE INDEX IF NOT EXISTS idx_bhavcopy_lookup
    ON bhavcopy_options(symbol, date, expiry, strike, option_type);
-- ATM IV cache: one IV per (symbol, date, expiry) — avoids recomputing per backtest
CREATE TABLE IF NOT EXISTS bhavcopy_iv_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    expiry      TEXT NOT NULL,
    atm_iv      REAL NOT NULL,
    option_type TEXT NOT NULL DEFAULT 'CE',
    UNIQUE(symbol, date, expiry, option_type)
);
CREATE INDEX IF NOT EXISTS idx_iv_cache ON bhavcopy_iv_cache(symbol, date, expiry);
CREATE TABLE IF NOT EXISTS bhavcopy_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_MIGRATE_IV_COLUMN = """
ALTER TABLE bhavcopy_options ADD COLUMN iv REAL;
"""

# NSE bhavcopy uses dates like "25-APR-2024" — convert to ISO
_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def _nse_date_to_iso(nse_date: str) -> str:
    """Convert NSE date string (25-APR-2024) to ISO (2024-04-25)."""
    try:
        parts = nse_date.strip().split("-")
        if len(parts) == 3:
            dd, mon, yyyy = parts
            mm = _MONTH_MAP.get(mon.upper(), mon)
            return f"{yyyy}-{mm}-{dd.zfill(2)}"
    except Exception:
        pass
    return nse_date


@contextmanager
def _connect():
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables, indexes and migrate existing DBs if needed."""
    with _connect() as conn:
        conn.executescript(_CREATE_TABLE)
        # Migrate: add iv column if it doesn't exist yet (for existing DBs)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bhavcopy_options)").fetchall()}
        if "iv" not in cols:
            try:
                conn.execute(_MIGRATE_IV_COLUMN)
                logger.info("Migrated bhavcopy_options: added iv column")
            except Exception:
                pass
    logger.info("Bhavcopy DB initialised at %s", _DB_PATH)


def insert_bhavcopy_rows(rows: List[dict], trading_date: date) -> int:
    """
    Insert bhavcopy rows for a given trading date.
    Skips duplicates (UNIQUE constraint).

    Returns number of rows actually inserted.
    """
    iso_date = trading_date.isoformat()
    inserted = 0
    with _connect() as conn:
        for row in rows:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO bhavcopy_options
                        (date, symbol, expiry, strike, option_type,
                         open, high, low, close, settle, oi, volume)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        iso_date,
                        row["symbol"].upper(),
                        _nse_date_to_iso(row["expiry"]),
                        row["strike"],
                        row["option_type"].upper(),
                        row.get("open"),
                        row.get("high"),
                        row.get("low"),
                        row.get("close"),
                        row.get("settle"),
                        row.get("oi", 0),
                        row.get("volume", 0),
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    inserted += 1
            except Exception as e:
                logger.debug("Row insert error: %s — %s", row, e)
        # Track last downloaded date
        conn.execute(
            "INSERT OR REPLACE INTO bhavcopy_meta(key, value) VALUES ('last_date', ?)",
            (iso_date,)
        )
    return inserted


def get_option_ohlc(
    symbol: str,
    trade_date: str,    # "YYYY-MM-DD"
    expiry: str,        # "YYYY-MM-DD"
    strike: float,
    option_type: str,   # "CE" or "PE"
) -> Optional[dict]:
    """
    Fetch real OHLC for a specific option contract on a specific date.
    Returns dict with open/high/low/close/settle/oi/volume, or None.
    """
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT open, high, low, close, settle, oi, volume
            FROM bhavcopy_options
            WHERE symbol=? AND date=? AND expiry=? AND strike=? AND option_type=?
            LIMIT 1
            """,
            (symbol.upper(), trade_date, expiry, float(strike), option_type.upper()),
        ).fetchone()
        if row:
            return dict(row)
    return None


def get_atm_ohlc(
    symbol: str,
    trade_date: str,
    expiry: str,
    spot: float,
    option_type: str = "CE",
) -> Optional[dict]:
    """
    Find the ATM (nearest strike to spot) option OHLC for a date/expiry.
    option_type: "CE", "PE", or "STRADDLE" (returns combined CE+PE close).
    """
    ot = option_type.upper()

    with _connect() as conn:
        if ot == "STRADDLE":
            # Get both CE and PE at ATM
            ce = conn.execute(
                """
                SELECT strike, open, high, low, close, settle
                FROM bhavcopy_options
                WHERE symbol=? AND date=? AND expiry=? AND option_type='CE'
                ORDER BY ABS(strike - ?) LIMIT 1
                """,
                (symbol.upper(), trade_date, expiry, spot),
            ).fetchone()
            pe = conn.execute(
                """
                SELECT strike, open, high, low, close, settle
                FROM bhavcopy_options
                WHERE symbol=? AND date=? AND expiry=? AND option_type='PE'
                ORDER BY ABS(strike - ?) LIMIT 1
                """,
                (symbol.upper(), trade_date, expiry, spot),
            ).fetchone()
            if ce and pe:
                return {
                    "strike":  ce["strike"],
                    "open":   (ce["open"]   or 0) + (pe["open"]   or 0),
                    "high":   (ce["high"]   or 0) + (pe["high"]   or 0),
                    "low":    (ce["low"]    or 0) + (pe["low"]    or 0),
                    "close":  (ce["close"]  or 0) + (pe["close"]  or 0),
                    "settle": (ce["settle"] or 0) + (pe["settle"] or 0),
                    "source": "bhavcopy",
                }
            return None
        else:
            row = conn.execute(
                """
                SELECT strike, open, high, low, close, settle
                FROM bhavcopy_options
                WHERE symbol=? AND date=? AND expiry=? AND option_type=?
                ORDER BY ABS(strike - ?) LIMIT 1
                """,
                (symbol.upper(), trade_date, expiry, ot, spot),
            ).fetchone()
            if row:
                return dict(row) | {"source": "bhavcopy"}
    return None


def get_nearest_expiry(
    symbol: str,
    trade_date: str,   # "YYYY-MM-DD"
) -> Optional[str]:
    """Return the nearest expiry date (ISO) for which we have bhavcopy data."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT expiry FROM bhavcopy_options
            WHERE symbol=? AND date=? AND expiry >= ?
            ORDER BY expiry ASC LIMIT 1
            """,
            (symbol.upper(), trade_date, trade_date),
        ).fetchone()
        return row["expiry"] if row else None


def store_atm_iv(
    symbol: str,
    trade_date: str,
    expiry: str,
    iv: float,
    option_type: str = "CE",
) -> None:
    """Persist a computed ATM implied volatility to the IV cache."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO bhavcopy_iv_cache
                (symbol, date, expiry, atm_iv, option_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (symbol.upper(), trade_date, expiry, round(iv, 6), option_type.upper()),
        )


def get_atm_iv(
    symbol: str,
    trade_date: str,
    expiry: str,
    option_type: str = "CE",
) -> Optional[float]:
    """Retrieve a cached ATM implied volatility. Returns None if not computed yet."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT atm_iv FROM bhavcopy_iv_cache
            WHERE symbol=? AND date=? AND expiry=? AND option_type=?
            LIMIT 1
            """,
            (symbol.upper(), trade_date, expiry, option_type.upper()),
        ).fetchone()
        return float(row["atm_iv"]) if row else None


def coverage_summary() -> dict:
    """Return a summary of what's in the DB."""
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM bhavcopy_options").fetchone()[0]
        dates = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM bhavcopy_options"
        ).fetchone()
        symbols = conn.execute(
            "SELECT symbol, COUNT(*) as cnt FROM bhavcopy_options GROUP BY symbol ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
        last = conn.execute(
            "SELECT value FROM bhavcopy_meta WHERE key='last_date'"
        ).fetchone()
    return {
        "total_rows":   total,
        "date_from":    dates[0],
        "date_to":      dates[1],
        "trading_days": dates[2],
        "last_download": last["value"] if last else None,
        "top_symbols":  [{"symbol": r["symbol"], "rows": r["cnt"]} for r in symbols],
    }
