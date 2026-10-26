"""Order entity and its state machine.

The state machine is deliberately explicit and *closed*: an unexpected
transition raises instead of being silently ignored.  Half of the incident
class in the reference projects came from writes that "succeeded" into a
wrong state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from oq.types import (
    ZERO,
    Book,
    InstType,
    Liquidity,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
    check_transition,
)


def make_client_order_id(intent_id: str, leg_index: int, attempt: int = 0) -> str:
    """Deterministic id, so a retry after a timeout is deduplicated by the venue.

    OKX accepts up to 32 alphanumeric characters.
    """
    raw = f"{intent_id}|{leg_index}|{attempt}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
    return f"oq{digest}"


@dataclass(slots=True)
class Order:
    client_id: str
    symbol: str
    inst_type: InstType
    side: Side
    order_type: OrderType
    qty: Decimal
    px: Decimal | None = None
    tif: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    status: OrderStatus = OrderStatus.INTENT
    filled_qty: Decimal = ZERO
    avg_px: Decimal | None = None
    fee: Decimal = ZERO
    venue_order_id: str | None = None
    intent_id: str | None = None
    cycle_id: str | None = None
    strategy_id: str | None = None
    book: Book = Book.DIRECTIONAL
    created_ms: int = 0
    updated_ms: int = 0
    error: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.qty <= ZERO:
            raise ValueError("order quantity must be positive")
        if self.order_type is OrderType.LIMIT and (self.px is None or self.px <= ZERO):
            raise ValueError("limit orders need a positive price")

    @property
    def remaining(self) -> Decimal:
        return max(ZERO, self.qty - self.filled_qty)

    def transition(self, new: OrderStatus, *, ts_ms: int, error: str | None = None) -> None:
        check_transition(self.status, new)
        self.status = new
        self.updated_ms = ts_ms
        if error:
            self.error = error

    def apply_fill(self, qty: Decimal, px: Decimal, fee: Decimal, *, ts_ms: int) -> Decimal:
        """Apply a fill, return the newly-realised signed delta for the position."""
        if qty <= ZERO:
            raise ValueError("fill quantity must be positive")
        if qty > self.remaining:
            raise ValueError(f"overfill on {self.client_id}: {qty} > {self.remaining}")
        prior = self.filled_qty
        self.filled_qty += qty
        if self.avg_px is None:
            self.avg_px = px
        else:
            self.avg_px = (self.avg_px * prior + px * qty) / (prior + qty)
        self.fee += fee
        self.updated_ms = ts_ms
        if self.filled_qty >= self.qty:
            self.transition(OrderStatus.FILLED, ts_ms=ts_ms)
        elif self.status in {OrderStatus.ACKED, OrderStatus.PARTIAL}:
            self.transition(OrderStatus.PARTIAL, ts_ms=ts_ms)
        return qty if self.side is Side.BUY else -qty

    def as_dict(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "venue_order_id": self.venue_order_id,
            "symbol": self.symbol,
            "inst_type": self.inst_type.value,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "qty": str(self.qty),
            "px": None if self.px is None else str(self.px),
            "filled_qty": str(self.filled_qty),
            "avg_px": None if self.avg_px is None else str(self.avg_px),
            "status": self.status.value,
            "reduce_only": self.reduce_only,
            "fee": str(self.fee),
            "intent_id": self.intent_id,
            "strategy_id": self.strategy_id,
            "book": self.book.value,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class Fill:
    order_client_id: str
    symbol: str
    side: Side
    qty: Decimal
    px: Decimal
    fee: Decimal
    ts_ms: int
    liquidity: Liquidity = Liquidity.TAKER
    venue_fill_id: str | None = None
    fee_ccy: str = "USDT"

    @property
    def signed_qty(self) -> Decimal:
        return self.qty if self.side is Side.BUY else -self.qty

    @property
    def notional(self) -> Decimal:
        return self.qty * self.px

    def as_dict(self) -> dict[str, object]:
        return {
            "order_client_id": self.order_client_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "qty": str(self.qty),
            "px": str(self.px),
            "fee": str(self.fee),
            "ts_ms": self.ts_ms,
            "liquidity": self.liquidity.value,
            "venue_fill_id": self.venue_fill_id,
        }
