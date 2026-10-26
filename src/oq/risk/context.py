"""Immutable risk context.

Everything a gate may look at is captured here.  No gate touches the
network, the database, the clock or the filesystem, which is why the whole
risk layer is testable with plain data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from oq.data.models import Quote
from oq.domain.portfolio import Portfolio
from oq.types import ZERO, Book


@dataclass(frozen=True, slots=True)
class RiskContext:
    now_ms: int
    portfolio: Portfolio
    marks: Mapping[str, Decimal]
    quotes: Mapping[str, Quote] = field(default_factory=dict)
    data_ok: Mapping[str, bool] = field(default_factory=dict)
    atr: Mapping[str, Decimal] = field(default_factory=dict)
    realized_vol: Mapping[str, float] = field(default_factory=dict)
    clusters: Mapping[str, str] = field(default_factory=dict)
    funding: Mapping[str, Decimal] = field(default_factory=dict)
    cooldowns: Mapping[tuple[str, Book], int] = field(default_factory=dict)
    trading_halted: bool = False
    halt_reason: str = ""
    kill_switch: bool = False
    config_hash: str = ""

    @property
    def equity(self) -> Decimal:
        return self.portfolio.equity(self.marks)

    def price(self, symbol: str) -> Decimal:
        return self.marks.get(symbol, ZERO)

    def is_fresh(self, symbol: str) -> bool:
        return bool(self.data_ok.get(symbol, True))

    def atr_of(self, symbol: str) -> Decimal:
        return self.atr.get(symbol, ZERO)

    def in_cooldown(self, symbol: str, book: Book) -> bool:
        until = self.cooldowns.get((symbol, book), 0)
        return until > self.now_ms


__all__ = ["RiskContext"]
