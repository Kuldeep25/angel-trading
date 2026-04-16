from fastapi import APIRouter, HTTPException
from execution.position_tracker import get_all_positions, get_live_positions, get_paper_positions
from execution.engine import cancel_order
from execution.paper_trading import paper_engine

router = APIRouter()


@router.get("/positions")
def positions():
    return get_all_positions()


@router.post("/positions/exit/{symbol}")
def exit_position(symbol: str, paper: bool = True):
    if paper:
        from options.engine import get_spot_price
        ltp = get_spot_price(symbol.replace("-EQ", ""), "NSE") or 0.0
        order = paper_engine.exit_position(symbol, ltp)
        if order is None:
            raise HTTPException(status_code=404, detail=f"No paper position for '{symbol}'.")
        return order
    raise HTTPException(status_code=400, detail="Live exit not implemented via REST — use cancel_order or place a counter order.")


@router.post("/positions/exit-all")
def exit_all_positions(paper: bool = True):
    if paper:
        from options.engine import get_spot_price
        positions = paper_engine.get_positions()
        ltp_map = {}
        for pos in positions:
            sym = pos["symbol"]
            ltp = get_spot_price(sym.replace("-EQ", ""), "NSE") or 0.0
            ltp_map[sym] = ltp
        orders = paper_engine.exit_all_positions(ltp_map)
        return {"status": "ok", "orders": orders}
    raise HTTPException(status_code=400, detail="Live exit-all not implemented via REST.")
