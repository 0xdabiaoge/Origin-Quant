"""Strategy protocol, market view and shared indicator cache.

Indicators are causal: ``feature[i]`` depends only on bars ``0..i``.  The
backtester therefore computes them once per symbol and lets strategies index
by ``view.index``; the live loop computes them once per cycle.  Nothing here
can see the next bar, which is what makes the same code valid in both.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

import numpy as np

from oq.data.models import Quote
from oq.domain.intent import Intent
from oq.domain.portfolio import Portfolio
from oq.domain.position import Position
from oq.types import ZERO, Book, D


@dataclass(frozen=True, slots=True)
class SymbolView:
    symbol: str
    timeframe: str
    ts_ms: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    index: int = -1
    funding_rates: np.ndarray | None = None
    funding_ts: np.ndarray | None = None
    quote: Quote | None = None
    cache: dict[str, np.ndarray] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        size = self.close.size
        if self.index < 0:
            object.__setattr__(self, "index", max(0, size - 1))
        if self.index >= size:
            object.__setattr__(self, "index", size - 1)

    @property
    def i(self) -> int:
        return self.index

    @property
    def price(self) -> Decimal:
        return D(float(self.close[self.index])) if self.close.size else ZERO

    @property
    def last_open(self) -> Decimal:
        return D(float(self.open[self.index]))

    @property
    def last_high(self) -> Decimal:
        return D(float(self.high[self.index]))

    @property
    def last_low(self) -> Decimal:
        return D(float(self.low[self.index]))

    @property
    def last_volume(self) -> Decimal:
        return D(float(self.volume[self.index]))

    @property
    def last_funding(self) -> Decimal:
        if self.funding_rates is None or self.funding_rates.size == 0:
            return ZERO
        if self.funding_ts is None:
            return D(float(self.funding_rates[-1]))
        mask = self.funding_ts <= self.ts_ms
        if not mask.any():
            return ZERO
        return D(float(self.funding_rates[mask][-1]))

    def feature(self, key: str, compute: Callable[[], np.ndarray]) -> np.ndarray:
        """Memoised causal indicator.  ``key`` must encode all parameters."""
        cached = self.cache.get(key)
        if cached is None:
            cached = compute()
            self.cache[key] = cached
        return cached

    def at(self, index: int) -> SymbolView:
        return SymbolView(
            symbol=self.symbol,
            timeframe=self.timeframe,
            ts_ms=int(self.ts_ms),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            index=index,
            funding_rates=self.funding_rates,
            funding_ts=self.funding_ts,
            quote=self.quote,
            cache=self.cache,
        )


@dataclass(frozen=True, slots=True)
class StrategyContext:
    now_ms: int
    equity: Decimal
    portfolio: Portfolio
    views: Mapping[str, SymbolView]
    params: Mapping[str, Any]
    config_hash: str
    bar_index: int | None = None

    def view(self, symbol: str) -> SymbolView | None:
        return self.views.get(symbol)

    def position(self, symbol: str, book: Book) -> Position | None:
        return self.portfolio.get(symbol, book)

    def mark(self, symbol: str) -> Decimal:
        view = self.views.get(symbol)
        return view.price if view is not None else ZERO


@runtime_checkable
class Strategy(Protocol):
    id: str
    book: Book
    timeframe: str

    def generate(self, ctx: StrategyContext) -> Sequence[Intent]: ...


def num(params: Mapping[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def dec(params: Mapping[str, Any], key: str, default: Decimal) -> Decimal:
    return D(params.get(key, default))


__all__ = ["Strategy", "StrategyContext", "SymbolView", "dec", "np", "num"]
