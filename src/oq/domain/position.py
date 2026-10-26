"""Position with signed quantity and exact Decimal accounting.

Sign convention: ``qty > 0`` is long, ``qty < 0`` is short, in **base units**
(e.g. 0.35 BTC, not 35 contracts).  Conversion to venue contracts happens
only in the adapter.

Cash convention:

* spot  — buying moves cash out, selling moves cash in;
* swap  — opening moves no cash, closing realises PnL into cash;
* both  — fees always reduce cash, funding is applied separately.

Getting this wrong double-counts PnL.  ``tests/test_portfolio.py`` pins both
invariants: cash is conserved and equity is path-independent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from oq.types import ZERO, Book, D, InstType, PosSide


@dataclass(slots=True)
class Position:
    symbol: str
    book: Book
    inst_type: InstType
    strategy_id: str
    qty: Decimal
    avg_px: Decimal
    opened_ms: int
    updated_ms: int
    stop_px: Decimal | None = None
    tp_px: Decimal | None = None
    leverage: Decimal = D(3)
    realized_pnl: Decimal = ZERO
    fees_paid: Decimal = ZERO
    funding_net: Decimal = ZERO
    initial_qty: Decimal = ZERO
    initial_stop_px: Decimal | None = None
    tp1_done: bool = False
    tp2_done: bool = False
    tp1_r: Decimal = D("1.5")
    tp2_r: Decimal = D("3.0")
    time_stop_bars: int | None = None
    opened_bar: int | None = None
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.qty == ZERO:
            raise ValueError("a position with zero quantity must not be stored")
        if self.avg_px <= ZERO:
            raise ValueError("average price must be positive")
        if self.initial_qty == ZERO:
            self.initial_qty = abs(self.qty)
        if self.initial_stop_px is None:
            self.initial_stop_px = self.stop_px
        if self.leverage <= ZERO:
            raise ValueError("leverage must be positive")

    @property
    def pos_side(self) -> PosSide:
        return PosSide.LONG if self.qty > ZERO else PosSide.SHORT

    @property
    def is_long(self) -> bool:
        return self.qty > ZERO

    @property
    def abs_qty(self) -> Decimal:
        return abs(self.qty)

    def notional(self, mark: Decimal) -> Decimal:
        return abs(self.qty) * mark

    def unrealized(self, mark: Decimal) -> Decimal:
        return self.qty * (mark - self.avg_px)

    def open_risk(self) -> Decimal:
        if self.stop_px is None:
            return ZERO
        return abs(self.qty) * abs(self.avg_px - self.stop_px)

    def margin_used(self, mark: Decimal) -> Decimal:
        if self.inst_type is InstType.SPOT:
            return self.notional(mark)
        return self.notional(mark) / self.leverage

    def add(self, signed_qty: Decimal, px: Decimal) -> Decimal:
        """Apply a signed fill, returning realised PnL for the closed portion.

        Positive return means profit.  Works for long and short because the
        closing slice carries the sign of the *existing* position.
        """
        if signed_qty == ZERO:
            return ZERO
        current = self.qty
        if current == ZERO:
            self.qty = signed_qty
            self.avg_px = px
            return ZERO

        same_direction = (current > ZERO) == (signed_qty > ZERO)
        if same_direction:
            total = abs(current) + abs(signed_qty)
            self.avg_px = (abs(current) * self.avg_px + abs(signed_qty) * px) / total
            self.qty = current + signed_qty
            return ZERO

        closing = min(abs(current), abs(signed_qty)) * (1 if current > ZERO else -1)
        realized = (px - self.avg_px) * closing
        new_qty = current + signed_qty
        if new_qty == ZERO:
            self.avg_px = ZERO
        elif (new_qty > ZERO) != (current > ZERO):
            self.avg_px = px
        self.qty = new_qty
        self.realized_pnl += realized
        return realized

    def move_stop(self, new_stop: Decimal) -> bool:
        """Only ever tighten.  Returns True when the stop actually moved."""
        if self.stop_px is None:
            self.stop_px = new_stop
            return True
        if self.is_long and new_stop <= self.stop_px:
            return False
        if not self.is_long and new_stop >= self.stop_px:
            return False
        self.stop_px = new_stop
        return True

    def as_dict(self, mark: Decimal) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "book": self.book.value,
            "inst_type": self.inst_type.value,
            "strategy_id": self.strategy_id,
            "pos_side": self.pos_side.value,
            "qty": str(self.qty),
            "avg_px": str(self.avg_px),
            "mark_px": str(mark),
            "notional": str(self.notional(mark)),
            "unrealized": str(self.unrealized(mark)),
            "realized_pnl": str(self.realized_pnl),
            "stop_px": None if self.stop_px is None else str(self.stop_px),
            "tp_px": None if self.tp_px is None else str(self.tp_px),
            "leverage": str(self.leverage),
            "margin_used": str(self.margin_used(mark)),
            "open_risk": str(self.open_risk()),
            "tp1_done": self.tp1_done,
            "tp2_done": self.tp2_done,
            "tp1_r": str(self.tp1_r),
            "tp2_r": str(self.tp2_r),
            "opened_ms": self.opened_ms,
            "updated_ms": self.updated_ms,
            "tags": dict(self.tags),
        }
