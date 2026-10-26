"""Reconciliation: local book versus venue truth.

The local ledger can be wrong after a crash, a partial fill, or a manual
intervention.  Reconciliation never guesses: it reports each discrepancy with
the action a human should take.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from oq.domain.portfolio import Portfolio
from oq.types import ZERO, D


@dataclass(slots=True)
class Discrepancy:
    kind: str
    symbol: str
    local: str
    venue: str
    action: str


@dataclass(slots=True)
class ReconcileReport:
    checked: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)
    venue_equity: Decimal = ZERO

    @property
    def ok(self) -> bool:
        return not self.discrepancies

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "venue_equity": str(self.venue_equity),
            "discrepancies": [
                {
                    "kind": d.kind,
                    "symbol": d.symbol,
                    "local": d.local,
                    "venue": d.venue,
                    "action": d.action,
                }
                for d in self.discrepancies
            ],
        }


def reconcile(portfolio: Portfolio, adapter, tolerance: Decimal = D("0.0001")) -> ReconcileReport:
    report = ReconcileReport()
    try:
        report.venue_equity = adapter.balance_usdt()
        venue_positions = adapter.venue_positions()
    except Exception as exc:
        report.discrepancies.append(
            Discrepancy(
                kind="venue_unreachable",
                symbol="-",
                local="n/a",
                venue=str(exc),
                action=(
                    "do not trade until the venue is readable; investigate credentials or outage"
                ),
            )
        )
        return report

    venue_map: dict[str, Decimal] = {}
    for row in venue_positions:
        symbol = str(row.get("instId", ""))
        size = D(row.get("pos") or 0)
        if symbol:
            venue_map[symbol] = venue_map.get(symbol, ZERO) + size

    checked = set()
    for position in portfolio.positions_of():
        checked.add(position.symbol)
        venue_qty = venue_map.get(position.symbol)
        report.checked += 1
        if venue_qty is None:
            report.discrepancies.append(
                Discrepancy(
                    kind="missing_at_venue",
                    symbol=position.symbol,
                    local=str(position.qty),
                    venue="0",
                    action=(
                        "close locally or investigate a rejected order; "
                        "never assume the position exists"
                    ),
                )
            )
            continue
        if abs(venue_qty - position.qty) > tolerance:
            report.discrepancies.append(
                Discrepancy(
                    kind="quantity_mismatch",
                    symbol=position.symbol,
                    local=str(position.qty),
                    venue=str(venue_qty),
                    action="adopt the venue quantity and log an adjustment; do not average",
                )
            )

    for symbol, qty in venue_map.items():
        if symbol in checked or qty == ZERO:
            continue
        report.discrepancies.append(
            Discrepancy(
                kind="unknown_at_venue",
                symbol=symbol,
                local="0",
                venue=str(qty),
                action="adopt the venue position or close it manually; it is unhedged",
            )
        )
    return report


__all__ = ["Discrepancy", "ReconcileReport", "reconcile"]
