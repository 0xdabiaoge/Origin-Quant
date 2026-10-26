"""Backtest data adapters: parquet or deterministic synthetic bars."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

from oq.config import Config
from oq.data.models import Bar, FundingPoint
from oq.data.store import ParquetStore
from oq.data.synthetic import SyntheticMarket, generate
from oq.strategy.base import SymbolView


@dataclass(slots=True)
class SymbolSeries:
    symbol: str
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    funding_ts: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    funding_rates: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    cache: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return int(self.close.size)

    def view(self, index: int, quote=None) -> SymbolView:
        return SymbolView(
            symbol=self.symbol,
            timeframe="",
            ts_ms=int(self.ts[index]),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            index=index,
            funding_rates=self.funding_rates if self.funding_rates.size else None,
            funding_ts=self.funding_ts if self.funding_ts.size else None,
            quote=quote,
            cache=self.cache,
        )


def from_bars(
    symbol: str, bars: Iterable[Bar], funding: Iterable[FundingPoint] | None = None
) -> SymbolSeries:
    ordered = sorted(bars, key=lambda b: b.ts_ms)
    ts = np.array([b.ts_ms for b in ordered], dtype=np.int64)
    return SymbolSeries(
        symbol=symbol,
        ts=ts,
        open=np.array([float(b.open) for b in ordered], dtype=np.float64),
        high=np.array([float(b.high) for b in ordered], dtype=np.float64),
        low=np.array([float(b.low) for b in ordered], dtype=np.float64),
        close=np.array([float(b.close) for b in ordered], dtype=np.float64),
        volume=np.array([float(b.volume) for b in ordered], dtype=np.float64),
        funding_ts=np.array([p.ts_ms for p in (funding or [])], dtype=np.int64),
        funding_rates=np.array([float(p.rate) for p in (funding or [])], dtype=np.float64),
    )


def load_synthetic(
    config: Config,
    *,
    symbols: Iterable[str],
    timeframe: str,
    days: int,
    seed: int = 7,
    start_ms: int | None = None,
) -> dict[str, SymbolSeries]:
    import time

    if start_ms is None:
        now = int(time.time() * 1000)
        start_ms = now - days * 86_400_000
    market: SyntheticMarket = generate(symbols, timeframe, start_ms, days, seed=seed)
    wanted = set(symbols)
    return {
        symbol: from_bars(symbol, bars, market.funding.get(symbol, []))
        for symbol, bars in market.bars.items()
        if symbol in wanted
    }


def load_parquet(
    config: Config, *, symbols: Iterable[str], timeframe: str, store: ParquetStore | None = None
) -> dict[str, SymbolSeries]:
    store = store or ParquetStore(config.data.parquet_dir)
    out: dict[str, SymbolSeries] = {}
    for symbol in symbols:
        try:
            frame = store.read_bars(symbol, timeframe)
            funding = store.read_funding(symbol)
        except FileNotFoundError:
            continue
        if frame is None or frame.empty:
            continue
        ts = frame["ts_ms"].to_numpy(dtype=np.int64)
        out[symbol] = SymbolSeries(
            symbol=symbol,
            ts=ts,
            open=frame["open"].to_numpy(dtype=np.float64),
            high=frame["high"].to_numpy(dtype=np.float64),
            low=frame["low"].to_numpy(dtype=np.float64),
            close=frame["close"].to_numpy(dtype=np.float64),
            volume=frame["volume"].to_numpy(dtype=np.float64),
            funding_ts=(
                funding["ts_ms"].to_numpy(dtype=np.int64)
                if "ts_ms" in funding
                else np.empty(0, dtype=np.int64)
            ),
            funding_rates=(
                funding["rate"].to_numpy(dtype=np.float64)
                if "rate" in funding
                else np.empty(0, dtype=np.float64)
            ),
        )
    return out


def trading_symbols(config: Config) -> list[str]:
    strategies = {s.id for s in config.strategies if s.enabled}
    symbols: list[str] = []
    for item in config.universe:
        if not item.enabled:
            continue
        if (
            item.book.value == "carry"
            and "carry_basis" in strategies
            or item.book.value == "directional"
            and "trend_donchian" in strategies
        ):
            symbols.append(item.symbol)
    return symbols


__all__ = ["SymbolSeries", "from_bars", "load_parquet", "load_synthetic", "trading_symbols"]
