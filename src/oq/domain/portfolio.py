"""Portfolio accounting.

One instance per account.  Books (carry / directional) share cash and equity
but are reported and risk-limited separately.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from oq.domain.position import Position
from oq.types import ZERO, Book, D, InstType, Side, safe_div


def _day_key(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.UTC).strftime("%Y-%m-%d")


def _week_key(ts_ms: int) -> str:
    iso = dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.UTC).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@dataclass(slots=True)
class Portfolio:
    cash: Decimal
    positions: dict[tuple[str, Book], Position] = field(default_factory=dict)
    realized_pnl: Decimal = ZERO
    fees_paid: Decimal = ZERO
    funding_net: Decimal = ZERO
    start_equity: Decimal = ZERO
    peak_equity: Decimal = ZERO
    day_key: str = ""
    day_start_equity: Decimal = ZERO
    week_key: str = ""
    week_start_equity: Decimal = ZERO
    initial_equity: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.start_equity == ZERO:
            self.start_equity = self.cash
        if self.initial_equity == ZERO:
            self.initial_equity = self.cash
        if self.peak_equity == ZERO:
            self.peak_equity = self.start_equity
        if self.day_start_equity == ZERO:
            self.day_start_equity = self.start_equity
        if self.week_start_equity == ZERO:
            self.week_start_equity = self.start_equity

    # -- queries -------------------------------------------------------
    @staticmethod
    def key(symbol: str, book: Book) -> tuple[str, Book]:
        return (symbol, book)

    def get(self, symbol: str, book: Book) -> Position | None:
        return self.positions.get(self.key(symbol, book))

    def positions_of(self, book: Book | None = None) -> list[Position]:
        items = list(self.positions.values())
        if book is not None:
            items = [p for p in items if p.book is book]
        return items

    def equity(self, marks: Mapping[str, Decimal]) -> Decimal:
        total = self.cash
        for position in self.positions.values():
            mark = marks.get(position.symbol)
            if mark is None or mark <= ZERO:
                mark = position.avg_px
            if position.inst_type is InstType.SPOT:
                total += position.qty * mark
            else:
                total += position.unrealized(mark)
        return total

    def gross_notional(self, marks: Mapping[str, Decimal]) -> Decimal:
        return sum((p.notional(self._mark(p, marks)) for p in self.positions.values()), ZERO)

    def net_notional(self, marks: Mapping[str, Decimal]) -> Decimal:
        total = ZERO
        for position in self.positions.values():
            total += position.qty * self._mark(position, marks)
        return total

    def margin_used(self, marks: Mapping[str, Decimal]) -> Decimal:
        return sum((p.margin_used(self._mark(p, marks)) for p in self.positions.values()), ZERO)

    def open_risk(self, book: Book | None = None) -> Decimal:
        return sum((p.open_risk() for p in self.positions_of(book)), ZERO)

    def exposure_by_symbol(self, marks: Mapping[str, Decimal]) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for position in self.positions.values():
            out[position.symbol] = out.get(position.symbol, ZERO) + position.notional(
                self._mark(position, marks)
            )
        return out

    def drawdown(self, marks: Mapping[str, Decimal]) -> Decimal:
        equity = self.equity(marks)
        peak = max(self.peak_equity, equity)
        return ZERO if peak <= ZERO else max(ZERO, (peak - equity) / peak)

    def day_pnl(self, marks: Mapping[str, Decimal]) -> Decimal:
        base = self.day_start_equity
        return ZERO if base <= ZERO else (self.equity(marks) - base) / base

    def week_pnl(self, marks: Mapping[str, Decimal]) -> Decimal:
        base = self.week_start_equity
        return ZERO if base <= ZERO else (self.equity(marks) - base) / base

    def heat(self, marks: Mapping[str, Decimal]) -> Decimal:
        return safe_div(self.open_risk(), self.equity(marks))

    # -- mutations -----------------------------------------------------
    def apply_fill(
        self,
        *,
        symbol: str,
        book: Book,
        inst_type: InstType,
        side: Side,
        qty: Decimal,
        px: Decimal,
        fee: Decimal,
        ts_ms: int,
        strategy_id: str = "",
        stop_px: Decimal | None = None,
        leverage: Decimal = D(3),
        tp1_r: Decimal = D("1.5"),
        tp2_r: Decimal = D("3.0"),
        time_stop_bars: int | None = None,
        tags: Mapping[str, str] | None = None,
    ) -> tuple[Position, Decimal]:
        key = self.key(symbol, book)
        signed = qty if side is Side.BUY else -qty
        position = self.positions.get(key)

        if position is None:
            position = Position(
                symbol=symbol,
                book=book,
                inst_type=inst_type,
                strategy_id=strategy_id,
                qty=signed,
                avg_px=px,
                opened_ms=ts_ms,
                updated_ms=ts_ms,
                stop_px=stop_px,
                leverage=leverage,
                tp1_r=tp1_r,
                tp2_r=tp2_r,
                time_stop_bars=time_stop_bars,
                tags=dict(tags or {}),
            )
            self.positions[key] = position
            realized = ZERO
        else:
            realized = position.add(signed, px)
            position.updated_ms = ts_ms
            if stop_px is not None:
                position.stop_px = stop_px
            if strategy_id:
                position.strategy_id = strategy_id
            if position.qty == ZERO:
                del self.positions[key]

        if inst_type is InstType.SPOT:
            self.cash -= signed * px
        else:
            self.cash += realized
        self.cash -= fee
        self.realized_pnl += realized
        self.fees_paid += fee
        position.fees_paid += fee
        return position, realized

    def apply_funding(self, symbol: str, book: Book, amount: Decimal, ts_ms: int) -> None:
        """``amount`` is signed: positive means the account receives funding."""
        self.cash += amount
        self.funding_net += amount
        position = self.get(symbol, book)
        if position is not None:
            position.funding_net += amount
            position.updated_ms = ts_ms

    def mark(self, marks: Mapping[str, Decimal], ts_ms: int) -> Decimal:
        equity = self.equity(marks)
        self.peak_equity = max(self.peak_equity, equity)
        self.roll_periods(ts_ms, marks, equity)
        return equity

    def roll_periods(
        self, ts_ms: int, marks: Mapping[str, Decimal], equity: Decimal | None = None
    ) -> None:
        day = _day_key(ts_ms)
        week = _week_key(ts_ms)
        if not self.day_key:
            self.day_key = day
        elif day != self.day_key:
            self.day_key = day
            self.day_start_equity = equity if equity is not None else self.equity(marks)
        if not self.week_key:
            self.week_key = week
        elif week != self.week_key:
            self.week_key = week
            self.week_start_equity = equity if equity is not None else self.equity(marks)

    def close_all(self, marks: Mapping[str, Decimal]) -> list[tuple[Position, Decimal]]:
        """Used by the kill switch and rebalancing; returns closed positions."""
        closed: list[tuple[Position, Decimal]] = []
        for position in list(self.positions.values()):
            mark = self._mark(position, marks)
            realized = position.unrealized(mark)
            self.cash += realized
            self.realized_pnl += realized
            closed.append((position, realized))
            self.positions.pop(self.key(position.symbol, position.book), None)
        return closed

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _mark(position: Position, marks: Mapping[str, Decimal]) -> Decimal:
        mark = marks.get(position.symbol)
        return mark if mark is not None and mark > ZERO else position.avg_px

    def snapshot(self, marks: Mapping[str, Decimal]) -> dict[str, object]:
        equity = self.equity(marks)
        return {
            "cash": str(self.cash),
            "equity": str(equity),
            "gross_notional": str(self.gross_notional(marks)),
            "net_notional": str(self.net_notional(marks)),
            "margin_used": str(self.margin_used(marks)),
            "open_risk": str(self.open_risk()),
            "heat": str(self.heat(marks)),
            "realized_pnl": str(self.realized_pnl),
            "fees_paid": str(self.fees_paid),
            "funding_net": str(self.funding_net),
            "drawdown": str(self.drawdown(marks)),
            "day_pnl": str(self.day_pnl(marks)),
            "week_pnl": str(self.week_pnl(marks)),
            "positions": [p.as_dict(self._mark(p, marks)) for p in self.positions_of()],
        }

    def total_exposure(self, marks: Mapping[str, Decimal]) -> Decimal:
        return self.gross_notional(marks)

    def symbols(self) -> Iterable[str]:
        return {p.symbol for p in self.positions.values()}
