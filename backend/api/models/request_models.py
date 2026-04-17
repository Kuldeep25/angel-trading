from pydantic import BaseModel, Field
from typing import Optional


class BacktestRequest(BaseModel):
    strategy_name: str
    symbol: str
    exchange: str = "NSE"
    instrument_type: str = "equity"   # equity | futures | options
    interval: str = "ONE_DAY"
    from_date: str = Field(..., description="YYYY-MM-DD HH:MM")
    to_date: str   = Field(..., description="YYYY-MM-DD HH:MM")
    capital: float = 100_000.0
    sl_pct: float     = 2.0
    tsl_pct: float    = 0.0
    target_pct: float = 0.0
    position_size_pct: float = 95.0


class StrategyAddRequest(BaseModel):
    name: str
    code: str
    category: str = "equity"
    description: str = ""


class StrategyEditRequest(BaseModel):
    code: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    mode: Optional[str] = None


class LiveStartRequest(BaseModel):
    strategy_name: str
    symbol: str
    exchange: str = "NSE"
    interval: str = "ONE_MINUTE"
    paper: bool = True
    capital: float = 100_000.0
    sl_pct: float = 2.0
    tsl_pct: float = 0.0


class VoiceExecuteRequest(BaseModel):
    text: str
