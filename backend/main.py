"""
FastAPI application entry point.

Startup sequence:
  1. Load Angel One instrument master (downloads if stale)
  2. Connect to Angel One SmartAPI
  3. Initialise Telegram + WhatsApp notifiers
  4. Register sample strategies (first run only)

Run with:
    cd backend
    uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure backend/ is on the Python path when run from repo root
sys.path.insert(0, os.path.dirname(__file__))

from config.settings import get_settings
from angel.client import angel_client
from angel import symbols as instrument_master
from notifications.telegram_bot import init_telegram
from notifications.whatsapp import init_whatsapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

settings = get_settings()


# ─────────────────────────────────────────────────────────────────────────────
#  Lifespan (startup / shutdown)
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info("=== AI Trading Agent starting up ===")

    # 1. Load instrument master
    try:
        logger.info("Loading Angel One instrument master…")
        instrument_master.ensure_loaded()
        logger.info("Instrument master ready.")
    except Exception as exc:
        logger.error("Instrument master load error: %s", exc)

    # 2. Connect to Angel One
    if settings.angel_api_key and settings.angel_client_code:
        try:
            angel_client.connect(
                api_key     = settings.angel_api_key,
                client_code = settings.angel_client_code,
                password    = settings.angel_password,
                totp_secret = settings.angel_totp_secret,
            )
        except Exception as exc:
            logger.error("Angel One connection failed: %s — running without live data.", exc)
    else:
        logger.warning(
            "Angel One credentials not set. "
            "Copy .env.example to .env and fill in your credentials."
        )

    # 3. Notifications
    if settings.telegram_bot_token and settings.telegram_chat_id:
        init_telegram(settings.telegram_bot_token, settings.telegram_chat_id)
    if settings.twilio_account_sid and settings.twilio_auth_token:
        init_whatsapp(
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_whatsapp_from,
            settings.twilio_whatsapp_to,
        )

    # 4. Register built-in sample strategies on first run
    _register_sample_strategies()

    # 5. Start position guard monitor (SL / Target / TSL)
    from execution.paper_trading import guard_engine as _guard_engine
    _guard_engine.start()

    # 6. Initialise option chain snapshot DB and auto-start collector
    from options.snapshot_db import init_db as _init_snapshot_db
    from options.bhavcopy_db import init_db as _init_bhavcopy_db
    from options import collector as _oc_collector
    _init_snapshot_db()
    _init_bhavcopy_db()
    if angel_client.is_connected:
        _oc_collector.start()   # collects every 5 min during market hours
        logger.info("Option chain collector auto-started.")
    else:
        logger.info("Option chain collector NOT started — Angel One not connected.")

    logger.info("=== Startup complete. API ready. ===")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────
    logger.info("Shutting down…")
    from level_strategy.engine import stop_monitor as _ls_stop_monitor
    _ls_stop_monitor()
    await _oc_collector.stop()
    _guard_engine.stop()
    angel_client.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Trading Agent",
    description="Full-stack AI trading system powered by Angel One SmartAPI.",
    version="1.0.0",
    lifespan=lifespan,
)

_base_origins = ["http://localhost:4200", "http://127.0.0.1:4200"]
_extra = [o.strip() for o in settings.extra_cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_base_origins + _extra,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
from api.routes import ping, backtest, strategies, live, positions, voice, symbols, account  # noqa: E402
from api.option_chain import router as _option_chain_router  # noqa: E402
from api.routes import level_strategy as _level_strategy_router  # noqa: E402

app.include_router(ping.router,                   tags=["Health"])
app.include_router(backtest.router,               tags=["Backtest"])
app.include_router(strategies.router,             tags=["Strategies"])
app.include_router(live.router,                   tags=["Live Trading"])
app.include_router(positions.router,              tags=["Positions"])
app.include_router(account.router,                tags=["Account"])
app.include_router(voice.router,                  tags=["Voice"])
app.include_router(symbols.router,                tags=["Symbols"])
app.include_router(_option_chain_router,          tags=["Option Chain"])
app.include_router(_level_strategy_router.router, tags=["Level Strategy"])


@app.post("/reconnect", tags=["Health"])
async def reconnect():
    """Re-attempt Angel One login without restarting uvicorn."""
    if not (settings.angel_api_key and settings.angel_client_code):
        return {"status": "error", "detail": "Angel One credentials not configured in .env"}
    try:
        angel_client.connect(
            api_key     = settings.angel_api_key,
            client_code = settings.angel_client_code,
            password    = settings.angel_password,
            totp_secret = settings.angel_totp_secret,
        )
        return {"status": "ok", "detail": "Angel One reconnected successfully"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@app.post("/disconnect", tags=["Health"])
async def disconnect():
    """Disconnect Angel One session (close trading for the day)."""
    if not angel_client.is_connected:
        return {"status": "ok", "detail": "Already disconnected"}
    try:
        angel_client.disconnect()
        return {"status": "ok", "detail": "Angel One session closed"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
#  First-run strategy registration
# ─────────────────────────────────────────────────────────────────────────────

def _register_sample_strategies() -> None:
    import strategy.manager as mgr
    from pathlib import Path

    samples = [
        {
            "name": "equity_ema_crossover",
            "file": "strategy/strategies/equity_ema_crossover.py",
            "category": "equity",
            "description": "EMA 9/21 crossover — works on all timeframes.",
        },
        {
            "name": "futures_breakout",
            "file": "strategy/strategies/futures_breakout.py",
            "category": "futures",
            "description": "Opening Range Breakout for intraday futures.",
        },
        {
            "name": "options_straddle",
            "file": "strategy/strategies/options_straddle.py",
            "category": "options",
            "description": "ATM Straddle entry at session open.",
        },
    ]

    for s in samples:
        if mgr.get_strategy(s["name"]) is None:
            fp = Path(s["file"])
            if fp.exists():
                code = fp.read_text(encoding="utf-8")
                try:
                    mgr.add_strategy(s["name"], code, s["category"], s["description"])
                    logger.info("Sample strategy registered: %s", s["name"])
                except Exception as exc:
                    logger.warning("Could not register %s: %s", s["name"], exc)
