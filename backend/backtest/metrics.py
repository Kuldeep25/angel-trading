"""
Backtest metrics — compute summary statistics from a list of Trade objects.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from backtest.models import Trade


def compute_metrics(
    trades: List["Trade"],
    initial_capital: float,
) -> Tuple[Dict[str, Any], List[List]]:
    """
    Compute standard trading performance metrics.

    Returns
    -------
    (summary_dict, equity_curve)

    equity_curve: [[timestamp_str, equity_value], ...]
    """
    if not trades:
        return _empty_summary(initial_capital), [[]]

    # Build equity curve
    equity = initial_capital
    equity_curve: List[List] = [["start", round(equity, 2)]]
    peak_equity  = equity
    max_drawdown = 0.0
    peak_ts: str = ""

    wins  = 0
    pnls: List[float] = []

    for trade in trades:
        equity += trade.pnl
        pnls.append(trade.pnl)
        if trade.pnl > 0:
            wins += 1
        equity_curve.append([trade.exit_time, round(equity, 2)])
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

    total_trades  = len(trades)
    total_pnl     = equity - initial_capital
    total_return  = total_pnl / initial_capital * 100 if initial_capital > 0 else 0.0
    win_rate      = wins / total_trades * 100 if total_trades > 0 else 0.0
    avg_pnl       = sum(pnls) / total_trades if total_trades > 0 else 0.0

    # Sharpe ratio (annualised, assuming 252 trading days)
    if total_trades > 1:
        mean   = avg_pnl
        variance = sum((p - mean) ** 2 for p in pnls) / (total_trades - 1)
        std_dev  = math.sqrt(variance) if variance > 0 else 0.0
        sharpe   = (mean / std_dev) * math.sqrt(252) if std_dev > 0 else 0.0
    else:
        sharpe = 0.0

    summary: Dict[str, Any] = {
        "total_trades":   total_trades,
        "wins":           wins,
        "losses":         total_trades - wins,
        "win_rate":       round(win_rate, 2),
        "total_pnl":      round(total_pnl, 2),
        "total_return":   round(total_return, 2),
        "max_drawdown":   round(max_drawdown, 2),
        "sharpe_ratio":   round(sharpe, 4),
        "avg_pnl":        round(avg_pnl, 2),
        "final_equity":   round(equity, 2),
        "initial_capital": round(initial_capital, 2),
    }

    return summary, equity_curve


def _empty_summary(capital: float) -> Dict[str, Any]:
    return {
        "total_trades":    0,
        "wins":            0,
        "losses":          0,
        "win_rate":        0.0,
        "total_pnl":       0.0,
        "total_return":    0.0,
        "max_drawdown":    0.0,
        "sharpe_ratio":    0.0,
        "avg_pnl":         0.0,
        "final_equity":    round(capital, 2),
        "initial_capital": round(capital, 2),
    }
