"""Donchian breakout in the direction of the higher-timeframe trend.

Why this shape:

* The entry is a *state* (a new N-bar extreme) rather than a pattern opinion,
  so it is testable and has no discretionary parameters beyond N.
* The stop is volatility-scaled and placed before entry, so risk is defined
  at signal time and sizing can use it.
* The take-profit ladder is expressed in R multiples, which is what makes
  expectancy auditable.  Exits decide profitability far more than the entry
  filter does.
* The regime filter (EMA fast/slow plus a long EMA) and the volatility band
  remove the worst breakout regime: low-volatility chop and blow-off tops.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np

from oq.data.features import atr, ema, rolling_max_prev, rolling_min_prev
from oq.domain.intent import Intent
from oq.strategy.base import StrategyContext, SymbolView, num
from oq.types import Book, D


class TrendDonchian:
    id = "trend_donchian"
    book = Book.DIRECTIONAL

    def __init__(self, params: dict, timeframe: str = "15m") -> None:
        self.params = params
        self.timeframe = timeframe
        self.entry_lookback = int(num(params, "entry_lookback", 48))
        self.atr_period = int(num(params, "atr_period", 14))
        self.ema_fast_n = int(num(params, "ema_fast", 21))
        self.ema_slow_n = int(num(params, "ema_slow", 55))
        self.ema_trend_n = int(num(params, "ema_trend", 200))
        self.atr_stop_mult = D(num(params, "atr_stop_mult", 2.0))
        self.min_atr_pct = D(num(params, "min_atr_pct", 0.004))
        self.max_atr_pct = D(num(params, "max_atr_pct", 0.05))
        self.tp1_r = D(num(params, "tp1_r", 1.5))
        self.tp2_r = D(num(params, "tp2_r", 3.0))
        self.time_stop_bars = int(num(params, "time_stop_bars", 32))
        self.max_signals = int(num(params, "max_signals_per_cycle", 2))
        self.max_spread_bps = D(num(params, "max_spread_bps", 25))
        self.warmup = max(
            self.entry_lookback + 1,
            self.ema_trend_n + 1,
            self.atr_period + 1,
            self.ema_slow_n + 1,
        )

    def _tp_ladder(self) -> tuple[tuple[Decimal, Decimal], ...]:
        return ((self.tp1_r, D("0.33")), (self.tp2_r, D("0.33")))

    def _candidate(self, view: SymbolView) -> tuple[str, Decimal, Decimal] | None:
        """Return ``(side, stop_price, breakout_score)`` or None."""
        if view.close.size < self.warmup:
            return None
        i = view.i
        close = view.close

        atr_series = view.feature(
            f"atr:{self.atr_period}",
            lambda: atr(view.high, view.low, view.close, self.atr_period),
        )
        atr_value = float(atr_series[i])
        if not np.isfinite(atr_value) or atr_value <= 0:
            return None

        price = float(close[i])
        atr_pct = atr_value / price
        if atr_pct < float(self.min_atr_pct) or atr_pct > float(self.max_atr_pct):
            return None

        fast = float(view.feature(f"ema:{self.ema_fast_n}", lambda: ema(close, self.ema_fast_n))[i])
        slow = float(view.feature(f"ema:{self.ema_slow_n}", lambda: ema(close, self.ema_slow_n))[i])
        trend = float(
            view.feature(f"ema:{self.ema_trend_n}", lambda: ema(close, self.ema_trend_n))[i]
        )
        if not (np.isfinite(fast) and np.isfinite(slow) and np.isfinite(trend)):
            return None

        upper = float(
            view.feature(
                f"dmax:{self.entry_lookback}",
                lambda: rolling_max_prev(view.high, self.entry_lookback),
            )[i]
        )
        lower = float(
            view.feature(
                f"dmin:{self.entry_lookback}",
                lambda: rolling_min_prev(view.low, self.entry_lookback),
            )[i]
        )
        if not (np.isfinite(upper) and np.isfinite(lower)):
            return None

        stop_distance = self.atr_stop_mult * D(atr_value)
        if price > upper and fast > slow and price > trend:
            return ("long", D(price) - stop_distance, D((price - upper) / atr_value))
        if price < lower and fast < slow and price < trend:
            return ("short", D(price) + stop_distance, D((lower - price) / atr_value))
        return None

    def generate(self, ctx: StrategyContext) -> list[Intent]:
        candidates = []
        for symbol, view in ctx.views.items():
            if view.close.size < self.warmup:
                continue
            if ctx.position(symbol, self.book) is not None:
                continue
            if view.quote is not None and view.quote.spread_bps > self.max_spread_bps:
                continue
            found = self._candidate(view)
            if found is None:
                continue
            side, stop, score = found
            candidates.append((score, symbol, view, side, stop))

        candidates.sort(key=lambda item: item[0], reverse=True)
        intents: list[Intent] = []
        for _score, symbol, view, side, stop in candidates[: self.max_signals]:
            price = view.price
            if side == "long" and stop >= price:
                continue
            if side == "short" and stop <= price:
                continue
            tags = {"score": str(_score), "tf": self.timeframe}
            factory = Intent.open_long if side == "long" else Intent.open_short
            intents.append(
                factory(
                    strategy_id=self.id,
                    symbol=symbol,
                    ts_ms=ctx.now_ms,
                    stop_px=stop,
                    ref_px=price,
                    book=self.book,
                    tp_ladder=self._tp_ladder(),
                    time_stop_bars=self.time_stop_bars,
                    tags=tags,
                )
            )
        return intents


__all__ = ["TrendDonchian"]
