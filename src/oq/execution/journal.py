"""Execution journal: the seam between trading logic and storage.

The backtest uses a null journal so the same OMS code runs without a
database; the live path uses the store, writing each projection and its event
inside one transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol

from oq.domain.events import EventType
from oq.domain.order import Fill, Order
from oq.domain.portfolio import Portfolio
from oq.storage import Database, Store, TradeRecord
from oq.types import Book, OrderStatus


class Journal(Protocol):
    def record_order(self, order: Order) -> None: ...

    def record_fill(self, fill: Fill) -> None: ...

    def record_trade(self, trade: TradeRecord) -> None: ...

    def sync_positions(self, portfolio: Portfolio) -> None: ...

    def record_event(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        ts_ms: int,
        symbol: str | None = None,
        cycle_id: str | None = None,
    ) -> None: ...

    def record_equity(self, ts_ms: int, snapshot: Mapping[str, Any]) -> None: ...

    def record_funding(
        self, ts_ms: int, symbol: str, book: Book, amount: Decimal, rate: Decimal
    ) -> None: ...

    def add_cooldown(self, symbol: str, book: Book, until_ms: int) -> None: ...

    def cooldowns(self) -> dict[tuple[str, Book], int]: ...


class NullJournal:
    """No-op journal used by the backtester."""

    def __init__(self) -> None:
        self.orders: list[Order] = []
        self.fills: list[Fill] = []
        self.trades: list[TradeRecord] = []
        self.events: list[tuple[EventType, Mapping[str, Any], int]] = []
        self.cooldown_map: dict[tuple[str, Book], int] = {}

    def record_order(self, order: Order) -> None:
        self.orders.append(order)

    def record_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def record_trade(self, trade: TradeRecord) -> None:
        self.trades.append(trade)

    def sync_positions(self, portfolio: Portfolio) -> None:
        return None

    def record_event(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        ts_ms: int,
        symbol: str | None = None,
        cycle_id: str | None = None,
    ) -> None:
        self.events.append((event_type, payload, ts_ms))

    def record_equity(self, ts_ms: int, snapshot: Mapping[str, Any]) -> None:
        return None

    def record_funding(
        self, ts_ms: int, symbol: str, book: Book, amount: Decimal, rate: Decimal
    ) -> None:
        return None

    def add_cooldown(self, symbol: str, book: Book, until_ms: int) -> None:
        self.cooldown_map[(symbol, book)] = until_ms

    def cooldowns(self) -> dict[tuple[str, Book], int]:
        return dict(self.cooldown_map)


class StoreJournal:
    def __init__(self, db: Database, store: Store) -> None:
        self.db = db
        self.store = store

    def record_order(self, order: Order) -> None:
        with self.db.tx() as conn:
            self.store.save_order(conn, order)
            event_type = _ORDER_EVENTS.get(order.status, EventType.ORDER_SUBMITTED)
            self.store.emit(
                conn,
                event_type,
                order.as_dict(),
                ts_ms=order.updated_ms or order.created_ms,
                cycle_id=order.cycle_id,
                symbol=order.symbol,
            )

    def record_fill(self, fill: Fill) -> None:
        with self.db.tx() as conn:
            self.store.save_fill(conn, fill)
            self.store.emit(
                conn,
                EventType.FILL,
                fill.as_dict(),
                ts_ms=fill.ts_ms,
                symbol=fill.symbol,
            )

    def record_trade(self, trade: TradeRecord) -> None:
        with self.db.tx() as conn:
            self.store.save_trade(conn, trade)
            self.store.emit(
                conn,
                EventType.POSITION_CLOSED,
                {
                    "symbol": trade.symbol,
                    "book": trade.book.value,
                    "pnl": str(trade.pnl),
                    "r_multiple": None if trade.r_multiple is None else str(trade.r_multiple),
                    "reason": trade.reason,
                },
                ts_ms=trade.exit_ms,
                symbol=trade.symbol,
            )

    def sync_positions(self, portfolio: Portfolio) -> None:
        with self.db.tx() as conn:
            self.store.sync_positions(conn, portfolio)
            self.store.save_portfolio_state(conn, portfolio)

    def record_event(
        self,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        ts_ms: int,
        symbol: str | None = None,
        cycle_id: str | None = None,
    ) -> None:
        with self.db.tx() as conn:
            self.store.emit(
                conn, event_type, payload, ts_ms=ts_ms, symbol=symbol, cycle_id=cycle_id
            )

    def record_equity(self, ts_ms: int, snapshot: Mapping[str, Any]) -> None:
        with self.db.tx() as conn:
            self.store.save_equity(conn, ts_ms, snapshot)

    def record_funding(
        self, ts_ms: int, symbol: str, book: Book, amount: Decimal, rate: Decimal
    ) -> None:
        with self.db.tx() as conn:
            self.store.save_funding(
                conn, ts_ms=ts_ms, symbol=symbol, book=book, amount=amount, rate=rate
            )
            self.store.emit(
                conn,
                EventType.FUNDING,
                {"symbol": symbol, "amount": str(amount), "rate": str(rate)},
                ts_ms=ts_ms,
                symbol=symbol,
            )

    def add_cooldown(self, symbol: str, book: Book, until_ms: int) -> None:
        current = self.cooldowns()
        current[(symbol, book)] = until_ms
        payload = {f"{sym}|{bk.value}": ts for (sym, bk), ts in current.items()}
        with self.db.tx() as conn:
            self.store.set_kv(conn, "cooldowns", payload)

    def cooldowns(self) -> dict[tuple[str, Book], int]:
        raw = self.store.get_kv("cooldowns", {}) or {}
        out: dict[tuple[str, Book], int] = {}
        for key, value in raw.items():
            symbol, _, book = key.partition("|")
            try:
                out[(symbol, Book(book))] = int(value)
            except (ValueError, TypeError):
                continue
        return out


_ORDER_EVENTS = {
    OrderStatus.SUBMITTING: EventType.ORDER_SUBMITTED,
    OrderStatus.ACKED: EventType.ORDER_ACK,
    OrderStatus.PARTIAL: EventType.ORDER_ACK,
    OrderStatus.FILLED: EventType.ORDER_ACK,
    OrderStatus.REJECTED: EventType.ORDER_REJECT,
    OrderStatus.CANCELED: EventType.ORDER_CANCEL,
    OrderStatus.UNKNOWN: EventType.ORDER_UNKNOWN,
}


__all__ = ["Journal", "NullJournal", "StoreJournal"]
