"""Market data value objects.

Indicators run on float64 arrays for speed; money that reaches an order goes
through :class:`~decimal.Decimal`.  The boundary is explicit: analytics may
lose the 17th digit, accounting may not.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from oq.types import ZERO, D


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    timeframe: str
    ts_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    confirmed: bool = True

    @property
    def typical(self) -> Decimal:
        return (self.high + self.low + self.close) / D(3)

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "ts_ms": self.ts_ms,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "confirmed": self.confirmed,
        }


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    ts_ms: int
    bid: Decimal
    ask: Decimal
    last: Decimal
    bid_size: Decimal = ZERO
    ask_size: Decimal = ZERO

    @property
    def mid(self) -> Decimal:
        if self.bid > ZERO and self.ask > ZERO:
            return (self.bid + self.ask) / D(2)
        return self.last

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid
        if mid <= ZERO or self.ask <= ZERO or self.bid <= ZERO:
            return ZERO
        return (self.ask - self.bid) / mid * D(10_000)

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "ts_ms": self.ts_ms,
            "bid": str(self.bid),
            "ask": str(self.ask),
            "last": str(self.last),
            "spread_bps": str(self.spread_bps),
        }


@dataclass(frozen=True, slots=True)
class FundingPoint:
    symbol: str
    ts_ms: int
    rate: Decimal

    def as_dict(self) -> dict[str, object]:
        return {"symbol": self.symbol, "ts_ms": self.ts_ms, "rate": str(self.rate)}
