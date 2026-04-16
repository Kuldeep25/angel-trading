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
