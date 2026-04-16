"""
Strategy metadata manager — CRUD for strategy files + metadata JSON.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()
_STRATEGIES_DIR = Path(_settings.strategies_dir)
_META_FILE = Path(_settings.strategies_meta_file)


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def list_strategies() -> List[Dict[str, Any]]:
    """Return all strategy metadata records."""
    meta = _load_meta()
    return list(meta.values())


def get_strategy(name: str) -> Optional[Dict[str, Any]]:
    """Return metadata for a single strategy by name."""
    return _load_meta().get(name)


def add_strategy(
    name: str,
    code: str,
    category: str = "equity",
    description: str = "",
) -> Dict[str, Any]:
    """
    Save a new strategy Python file and register it in metadata.

    Raises
    ------
    ValueError if a strategy with the same name already exists.
    """
    meta = _load_meta()
    if name in meta:
        raise ValueError(f"Strategy '{name}' already exists. Use edit instead.")

    file_path = _strategy_path(name)
    file_path.write_text(code, encoding="utf-8")

    record = _make_record(name, str(file_path), category, description)
    meta[name] = record
    _save_meta(meta)
    logger.info("Strategy added: %s", name)
    return record


def edit_strategy(
    name: str,
    code: Optional[str] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    enabled: Optional[bool] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Update code and/or metadata for an existing strategy."""
    meta = _load_meta()
    if name not in meta:
        raise KeyError(f"Strategy '{name}' not found.")

    record = meta[name]

    if code is not None:
        file_path = Path(record["file_path"])
        file_path.write_text(code, encoding="utf-8")
        record["updated_at"] = _now()

    if category is not None:
        record["category"] = category
    if description is not None:
        record["description"] = description
    if enabled is not None:
        record["enabled"] = enabled
    if mode is not None:
        if mode not in ("live", "paper"):
            raise ValueError("mode must be 'live' or 'paper'.")
        record["mode"] = mode

    record["updated_at"] = _now()
    meta[name] = record
    _save_meta(meta)
    logger.info("Strategy updated: %s", name)
    return record


def delete_strategy(name: str) -> None:
    """Remove strategy file and metadata."""
    meta = _load_meta()
    if name not in meta:
        raise KeyError(f"Strategy '{name}' not found.")

    file_path = Path(meta[name]["file_path"])
    if file_path.exists():
        file_path.unlink()

    del meta[name]
    _save_meta(meta)
    logger.info("Strategy deleted: %s", name)


def copy_strategy(source_name: str, new_name: str) -> Dict[str, Any]:
    """Duplicate an existing strategy under a new name."""
    meta = _load_meta()
    if source_name not in meta:
        raise KeyError(f"Source strategy '{source_name}' not found.")
    if new_name in meta:
        raise ValueError(f"Strategy '{new_name}' already exists.")

    source_record = meta[source_name]
    source_file = Path(source_record["file_path"])
    new_file = _strategy_path(new_name)

    shutil.copy2(source_file, new_file)

    new_record = _make_record(
        new_name,
        str(new_file),
        source_record.get("category", "equity"),
        f"Copy of {source_name}",
    )
    meta[new_name] = new_record
    _save_meta(meta)
    logger.info("Strategy copied: %s → %s", source_name, new_name)
    return new_record


def get_strategy_code(name: str) -> str:
    """Read and return the raw Python code of a strategy."""
    meta = _load_meta()
    if name not in meta:
        raise KeyError(f"Strategy '{name}' not found.")
    return Path(meta[name]["file_path"]).read_text(encoding="utf-8")


def toggle_strategy(name: str, enabled: bool) -> Dict[str, Any]:
    """Enable or disable a strategy without changing its code."""
    return edit_strategy(name, enabled=enabled)


def set_mode(name: str, mode: str) -> Dict[str, Any]:
    """Switch strategy between 'live' and 'paper' mode."""
    return edit_strategy(name, mode=mode)


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_meta() -> Dict[str, Any]:
    if not _META_FILE.exists():
        return {}
    try:
        return json.loads(_META_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load strategies meta: %s", exc)
        return {}


def _save_meta(meta: Dict[str, Any]) -> None:
    _META_FILE.parent.mkdir(parents=True, exist_ok=True)
    _META_FILE.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _strategy_path(name: str) -> Path:
    _STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
    return _STRATEGIES_DIR / f"{safe_name}.py"


def _make_record(
    name: str, file_path: str, category: str, description: str
) -> Dict[str, Any]:
    return {
        "name": name,
        "file_path": file_path,
        "category": category,
        "description": description,
        "enabled": False,
        "mode": "paper",
        "created_at": _now(),
        "updated_at": _now(),
    }


def _now() -> str:
    return datetime.utcnow().isoformat()
