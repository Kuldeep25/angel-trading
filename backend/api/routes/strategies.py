from fastapi import APIRouter, HTTPException
from typing import Optional
from api.models.request_models import StrategyAddRequest, StrategyEditRequest
import strategy.manager as mgr
from strategy.loader import get_strategy_defaults

router = APIRouter(prefix="/strategies")


@router.get("/list")
def list_strategies():
    return mgr.list_strategies()


@router.get("/{name}")
def get_strategy(name: str):
    rec = mgr.get_strategy(name)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found.")
    rec = dict(rec)
    rec["code"] = mgr.get_strategy_code(name)
    # Inject class-level sl_pct / tsl_pct defaults if the strategy defines them
    defaults = get_strategy_defaults(rec.get("file_path", ""))
    rec["default_sl_pct"]     = defaults.get("sl_pct")
    rec["default_tsl_pct"]    = defaults.get("tsl_pct")
    rec["default_target_pct"] = defaults.get("target_pct")
    return rec


@router.post("/add")
def add_strategy(req: StrategyAddRequest):
    try:
        return mgr.add_strategy(req.name, req.code, req.category, req.description)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.put("/edit/{name}")
def edit_strategy(name: str, req: StrategyEditRequest):
    try:
        return mgr.edit_strategy(
            name,
            code        = req.code,
            category    = req.category,
            description = req.description,
            enabled     = req.enabled,
            mode        = req.mode,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/delete/{name}")
def delete_strategy(name: str):
    try:
        mgr.delete_strategy(name)
        return {"status": "deleted", "name": name}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/copy/{source_name}/{new_name}")
def copy_strategy(source_name: str, new_name: str):
    try:
        return mgr.copy_strategy(source_name, new_name)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/toggle/{name}")
def toggle_strategy(name: str, enabled: bool):
    try:
        return mgr.toggle_strategy(name, enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/mode/{name}")
def set_mode(name: str, mode: str):
    try:
        return mgr.set_mode(name, mode)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
