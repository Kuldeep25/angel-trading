from fastapi import APIRouter, HTTPException
from api.models.request_models import BacktestRequest
from backtest.engine import run_backtest, BacktestConfig

router = APIRouter()


@router.post("/backtest")
def backtest(req: BacktestRequest):
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
        position_size_pct = req.position_size_pct,
    )
    try:
        result = run_backtest(cfg)
        return result
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest error: {exc}")
