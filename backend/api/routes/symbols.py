from __future__ import annotations

from fastapi import APIRouter, Query
from angel.symbols import search_instruments

router = APIRouter(prefix="/symbols")


@router.get("")
def search_symbols(
    q: str = Query("", description="Search query — partial symbol or company name"),
    instrument_type: str = Query("equity", description="equity | futures | options"),
    limit: int = Query(50, le=200),
):
    """
    Return a list of symbols matching the query, filtered by instrument type.

    Response item:
      { symbol, raw_symbol, name, exchange, lot_size, expiry?, option_type? }
    """
    return search_instruments(query=q, instrument_type=instrument_type, limit=limit)
