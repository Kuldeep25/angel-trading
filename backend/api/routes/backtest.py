from fastapi import APIRouter, HTTPException
from api.models.request_models import BacktestRequest
from backtest.engine import run_backtest, BacktestConfig
from angel.client import angel_client

router = APIRouter()


@router.post("/backtest")
def backtest(req: BacktestRequest):
    # Give a clear error immediately if not connected — saves 30s of retries
    if not angel_client.is_connected:
        raise HTTPException(
            status_code=503,
            detail=(
                "Angel One is not connected. "
                "Click the red server badge and use POST /reconnect, "
                "or restart uvicorn to trigger a fresh login."
            ),
        )
    cfg = BacktestConfig(
        strategy_name     = req.strategy_name,
        symbol            = req.symbol,
        exchange          = req.exchange,
        instrument_type   = req.instrument_type,
        interval          = req.interval,
        from_date         = req.from_date,
        to_date           = req.to_date,
        capital           = req.capital,
        sl_pct            = req.sl_pct,
        tsl_pct           = req.tsl_pct,
        target_pct        = req.target_pct,
        position_size_pct  = req.position_size_pct,
        slippage_pct       = req.slippage_pct,
        position_sizing    = req.position_sizing,
        max_trades_per_day = req.max_trades_per_day,
        intraday_squareoff = req.intraday_squareoff,
        allow_reentry      = req.allow_reentry,
    )
    try:
        result = run_backtest(cfg)
        return result
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest error: {exc}")
