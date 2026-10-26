"""Broker protocol shared by the paper simulator and the live adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from oq.data.models import Quote
from oq.domain.order import Fill, Order
from oq.types import Instrument


@dataclass(frozen=True, slots=True)
class BrokerContext:
    """Everything a broker may use to decide a fill price.

    In the backtest the engine fills the *next* bar, so ``high``/``low`` refer
    to that bar.  In paper and live only ``price`` (the latest tradable quote)
    is meaningful.
    """

    now_ms: int
    price: Decimal
    high: Decimal | None = None
    low: Decimal | None = None
    next_open: Decimal | None = None
    quote: Quote | None = None
    allow_partial: bool = False
    prices: Mapping[str, Decimal] = field(default_factory=dict)
    tags: Mapping[str, str] = field(default_factory=dict)

    def price_for(self, symbol: str) -> Decimal:
        return self.prices.get(symbol, self.price)


@dataclass(slots=True)
class BrokerResult:
    fills: list[Fill] = field(default_factory=list)
    rejected: str | None = None
    venue_order_id: str | None = None


@runtime_checkable
class Broker(Protocol):
    def place(self, order: Order, instrument: Instrument, ctx: BrokerContext) -> BrokerResult: ...

    def cancel(self, order: Order, instrument: Instrument) -> bool: ...


def slippage_price(price: Decimal, side_is_buy: bool, bps: int) -> Decimal:
    factor = Decimal(bps) / Decimal(10_000)
    return price * (Decimal(1) + factor) if side_is_buy else price * (Decimal(1) - factor)


__all__ = ["Broker", "BrokerContext", "BrokerResult", "slippage_price"]
