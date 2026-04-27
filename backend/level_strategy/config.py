"""
Configuration loader for Level-Based Options Trading Strategy.
Reads/writes backend/config/level_strategy_config.json.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

_CONFIG_FILE = os.path.join(
    os.path.dirname(__file__), "..", "config", "level_strategy_config.json"
)
_CONFIG_FILE = os.path.normpath(_CONFIG_FILE)

DEFAULT_CONFIG: Dict[str, Any] = {
    "use_trend_filter": True,

    "entry_mode": "ATM",       # ATM | ITM1 | OTM1
    "strike_offset": 0,        # signed int: 0=ATM, 1=one step OTM, -1=one step ITM

    "confirmation_tf": "FIVE_MINUTE",
    "entry_tf": "ONE_MINUTE",

    "target_mode": "next_level",   # next_level | risk_reward
    "risk_reward": 2,

    "sl_mode": "level_break",      # level_break | percent
    "sl_percent": 20,

    "use_tsl": False,
    "tsl_mode": "percent",         # percent | points
    "tsl_percent": 20,
    "tsl_points": 10,

    "exit_on_opposite_signal": True,
    "trade_time_limit": "15:15",

    "quantity_mode": "auto",       # auto | fixed
    "fixed_lots": 1,
    "capital": 100000,

    "max_trades_per_day": 3,
}


def load_config() -> Dict[str, Any]:
    """Load config from JSON file, filling missing keys with defaults."""
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception:
            pass
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    """Persist config dict to JSON file."""
    merged = DEFAULT_CONFIG.copy()
    merged.update(cfg)
    os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
