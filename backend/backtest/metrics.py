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
    instrument_type: str = "equity",
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
    net_pnls: List[float] = []
    total_charges_sum = 0.0

    from backtest.charges import compute_charges

    for trade in trades:
        # Compute charges if not already set on the trade object
        if trade.charges == 0.0 and trade.entry_price > 0 and trade.exit_price > 0:
            c = compute_charges(
                instrument_type=instrument_type,
                quantity=trade.quantity,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
            )
            trade.charges = c["total"]
            trade.net_pnl = round(trade.pnl - trade.charges, 4)

        equity += trade.net_pnl if trade.net_pnl != 0.0 else trade.pnl
        pnls.append(trade.pnl)
        net_pnls.append(trade.net_pnl if trade.net_pnl != 0.0 else trade.pnl)
        total_charges_sum += trade.charges
        if (trade.net_pnl if trade.net_pnl != 0.0 else trade.pnl) > 0:
            wins += 1
        equity_curve.append([trade.exit_time, round(equity, 2)])
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

    total_trades  = len(trades)
    gross_pnl     = sum(pnls)
    net_pnl_total = sum(net_pnls)
    total_pnl     = net_pnl_total
    total_return  = total_pnl / initial_capital * 100 if initial_capital > 0 else 0.0
    win_rate      = wins / total_trades * 100 if total_trades > 0 else 0.0
    avg_pnl       = net_pnl_total / total_trades if total_trades > 0 else 0.0

    # Sharpe ratio (annualised, assuming 252 trading days)
    if total_trades > 1:
        mean   = avg_pnl
        variance = sum((p - mean) ** 2 for p in net_pnls) / (total_trades - 1)
        std_dev  = math.sqrt(variance) if variance > 0 else 0.0
        sharpe   = (mean / std_dev) * math.sqrt(252) if std_dev > 0 else 0.0
    else:
        sharpe = 0.0

    # Per-charge-type aggregation across all trades
    charge_breakdown: Dict[str, float] = {
        "brokerage": 0.0, "stt": 0.0, "exc_charge": 0.0,
        "sebi": 0.0, "gst": 0.0, "stamp": 0.0,
    }
    for trade in trades:
        if trade.entry_price > 0 and trade.exit_price > 0:
            c = compute_charges(instrument_type, trade.quantity, trade.entry_price, trade.exit_price)
            for k in charge_breakdown:
                charge_breakdown[k] = round(charge_breakdown[k] + c[k], 2)

    summary: Dict[str, Any] = {
        "total_trades":    total_trades,
        "wins":            wins,
        "losses":          total_trades - wins,
        "win_rate":        round(win_rate, 2),
        "gross_pnl":       round(gross_pnl, 2),
        "total_charges":   round(total_charges_sum, 2),
        "total_pnl":       round(net_pnl_total, 2),
        "total_return":    round(total_return, 2),
        "max_drawdown":    round(max_drawdown, 2),
        "sharpe_ratio":    round(sharpe, 4),
        "avg_pnl":         round(avg_pnl, 2),
        "final_equity":    round(equity, 2),
        "initial_capital": round(initial_capital, 2),
        "charge_breakdown": {k: round(v, 2) for k, v in charge_breakdown.items()},
    }

    return summary, equity_curve


def _empty_summary(capital: float) -> Dict[str, Any]:
    return {
        "total_trades":    0,
        "wins":            0,
        "losses":          0,
        "win_rate":        0.0,
        "gross_pnl":       0.0,
        "total_charges":   0.0,
        "total_pnl":       0.0,
        "total_return":    0.0,
        "max_drawdown":    0.0,
        "sharpe_ratio":    0.0,
        "avg_pnl":         0.0,
        "final_equity":    round(capital, 2),
        "initial_capital": round(capital, 2),
        "charge_breakdown": {"brokerage": 0.0, "stt": 0.0, "exc_charge": 0.0, "sebi": 0.0, "gst": 0.0, "stamp": 0.0},
    }
