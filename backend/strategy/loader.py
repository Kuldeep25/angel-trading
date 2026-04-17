"""
Dynamic strategy loader — imports a Strategy class from a .py file at runtime.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class StrategyProtocol(Protocol):
    """Expected interface every strategy must satisfy."""

    def generate(self, df: Any) -> Any:
        ...


def load_strategy(file_path: str) -> StrategyProtocol:
    """
    Dynamically load a strategy class from a Python file.

    The file must define a class named ``Strategy`` with a
    ``generate(self, df)`` method that accepts a pandas DataFrame
    and returns a DataFrame/Series with at least a ``signal`` column.

    Raises
    ------
    FileNotFoundError  if the file does not exist.
    AttributeError     if ``Strategy`` class is missing.
    TypeError          if ``generate`` method is missing.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Strategy file not found: {path}")

    module_name = path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[arg-type]
    except Exception as exc:
        raise ImportError(f"Error executing strategy file {path}: {exc}") from exc

    if not hasattr(module, "Strategy"):
        raise AttributeError(
            f"Strategy file '{path}' must define a class named 'Strategy'."
        )

    cls = module.Strategy
    if not callable(getattr(cls, "generate", None)):
        raise TypeError(
            f"Strategy class in '{path}' must have a callable 'generate' method."
        )

    logger.info("Strategy loaded from %s", path)
    return cls()


def get_strategy_defaults(file_path: str) -> dict:
    """
    Inspect a strategy file and return any sl_pct / tsl_pct declared as class
    attributes on the Strategy class.  Returns an empty dict for attributes not
    present.
    """
    try:
        path = Path(file_path).resolve()
        if not path.exists():
            return {}

        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            return {}

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[arg-type]

        cls = getattr(module, "Strategy", None)
        if cls is None:
            return {}

        defaults: dict = {}
        for attr in ("sl_pct", "tsl_pct", "target_pct"):
            val = getattr(cls, attr, None)
            if val is not None and isinstance(val, (int, float)):
                defaults[attr] = float(val)
        return defaults
    except Exception as exc:
        logger.warning("Could not read strategy defaults from %s: %s", file_path, exc)
        return {}
