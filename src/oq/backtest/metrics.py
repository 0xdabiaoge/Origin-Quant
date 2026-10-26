"""Performance metrics.

Every number here is computed from the realised equity curve and the trade
list; none of them is estimated from a model.  R-multiple statistics are
reported alongside the usual ratios because they are the only figures that
tell you whether the *exit geometry* works, independent of position size.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from oq.storage.storage import TradeRecord
from oq.types import ZERO


@dataclass(slots=True)
class EquityPoint:
    ts_ms: int
    equity: Decimal


def compute_metrics(
    curve: Sequence[EquityPoint],
    trades: Sequence[TradeRecord],
    *,
    initial_cash: Decimal,
    periods_per_year: float,
) -> dict[str, float]:
    if not curve:
        return {}
    start = initial_cash if initial_cash > ZERO else curve[0].equity
    end = curve[-1].equity
    total_return = float((end - start) / start) if start > ZERO else 0.0

    # Daily resampling for Sharpe/Sortino: annualising 15-minute returns
    # without accounting for autocorrelation overstates the ratio.
    daily = _daily_equity(curve)
    daily_returns = [
        float((daily[i] - daily[i - 1]) / daily[i - 1])
        for i in range(1, len(daily))
        if daily[i - 1] > ZERO
    ]
    sharpe = _sharpe(daily_returns, risk_free=0.0, periods=365.0)
    sortino = _sortino(daily_returns, periods=365.0)

    peak = start
    max_dd = 0.0
    for point in curve:
        peak = max(peak, point.equity)
        if peak > ZERO:
            max_dd = max(max_dd, float((peak - point.equity) / peak))

    days = max(1.0, (curve[-1].ts_ms - curve[0].ts_ms) / 86_400_000)
    cagr = (
        float((float(end) / float(start)) ** (365.0 / days) - 1.0)
        if start > ZERO and end > ZERO
        else 0.0
    )

    wins = [t for t in trades if t.pnl > ZERO]
    losses = [t for t in trades if t.pnl < ZERO]
    gross_win = sum((t.pnl for t in wins), ZERO)
    gross_loss = abs(sum((t.pnl for t in losses), ZERO))
    with_r = [t for t in trades if t.r_multiple is not None]
    r_values = [float(t.r_multiple) for t in with_r if t.r_multiple is not None]
    win_r = [float(t.r_multiple) for t in with_r if t.r_multiple is not None and t.pnl > ZERO]
    loss_r = [float(t.r_multiple) for t in with_r if t.r_multiple is not None and t.pnl < ZERO]

    fees = sum((t.fees for t in trades), ZERO)
    funding = sum((t.funding for t in trades), ZERO)
    return {
        "start_equity": float(start),
        "end_equity": float(end),
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": (cagr / max_dd) if max_dd > 0 else 0.0,
        "trades": float(len(trades)),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "profit_factor": float(gross_win / gross_loss)
        if gross_loss > ZERO
        else float("inf")
        if gross_win > ZERO
        else 0.0,
        "avg_win": float(gross_win / len(wins)) if wins else 0.0,
        "avg_loss": float(gross_loss / len(losses)) if losses else 0.0,
        "payoff_ratio": (
            float((gross_win / len(wins)) / (gross_loss / len(losses))) if wins and losses else 0.0
        ),
        "avg_win_r": (sum(win_r) / len(win_r)) if win_r else 0.0,
        "avg_loss_r": (sum(loss_r) / len(loss_r)) if loss_r else 0.0,
        "expectancy_r": (sum(r_values) / len(r_values)) if r_values else 0.0,
        "fees": float(fees),
        "funding": float(funding),
        "net_pnl": float(end - start),
        "days": days,
        "periods_per_year": periods_per_year,
    }


def _daily_equity(curve: Sequence[EquityPoint]) -> list[Decimal]:
    out: list[Decimal] = []
    current_day = None
    last = curve[0].equity
    for point in curve:
        day = point.ts_ms // 86_400_000
        if current_day is None:
            current_day = day
            last = point.equity
            continue
        if day != current_day:
            out.append(last)
            current_day = day
        last = point.equity
    out.append(last)
    return out


def _sharpe(returns: Sequence[float], *, risk_free: float, periods: float) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (mean - risk_free / periods) / std * math.sqrt(periods)


def _sortino(returns: Sequence[float], *, periods: float) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    downside = [min(0.0, r) for r in returns]
    dd = math.sqrt(sum(r * r for r in downside) / len(downside))
    if dd == 0:
        return 0.0
    return mean / dd * math.sqrt(periods)


__all__ = ["EquityPoint", "compute_metrics"]
