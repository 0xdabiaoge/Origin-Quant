"""Position lifecycle as intent generation.

Stops, targets and time exits are *rules*, not discretionary decisions, so
they live in one place and produce the same intents in backtest, paper and
live.  The R-multiple ladder is the core of the expectancy:

    1R stop, 1.5R take on a third, 3R take on a third, trail the rest.

With that structure a 42% win rate breaks even; the reference projects ran a
0.5R average win and needed 67%, which is why their equity curves looked the
way they did.
"""

from __future__ import annotations

from decimal import Decimal

from oq.domain.intent import Intent
from oq.domain.position import Position
from oq.strategy.base import StrategyContext, SymbolView
from oq.types import ZERO, Book, D, Side


class PositionManager:
    """Emits exits and stop moves for open positions."""

    id = "position_manager"
    book = Book.DIRECTIONAL
    timeframe = "15m"

    def __init__(
        self,
        *,
        breakeven_r: Decimal = D("1.0"),
        trail_r: Decimal = D("2.0"),
        trail_atr_mult: Decimal = D("2.0"),
    ) -> None:
        self.breakeven_r = breakeven_r
        self.trail_r = trail_r
        self.trail_atr_mult = trail_atr_mult

    def generate(self, ctx: StrategyContext) -> list[Intent]:
        intents: list[Intent] = []
        for position in ctx.portfolio.positions_of():
            if position.book is Book.CARRY:
                continue  # carry exits are decided by the carry strategy
            view = ctx.view(position.symbol)
            if view is None or view.close.size == 0:
                continue
            intents.extend(self._evaluate(position, view, ctx))
        return intents

    def _evaluate(self, position: Position, view: SymbolView, ctx: StrategyContext) -> list[Intent]:
        close = view.price
        high = view.last_high
        low = view.last_low
        entry = position.avg_px
        if position.stop_px is None:
            return [self._close(position, ctx, "missing stop")]
        r = abs(entry - position.stop_px)
        if r <= ZERO:
            return [self._close(position, ctx, "zero risk distance")]

        bars_held = self._bars_held(position, ctx)

        # 1. Hard stop against the bar range, not just the close.
        if position.is_long and low <= position.stop_px:
            return [self._close(position, ctx, f"stop {position.stop_px}")]
        if not position.is_long and high >= position.stop_px:
            return [self._close(position, ctx, f"stop {position.stop_px}")]

        # 2. Take-profit ladder as fractions of the initial position.
        favourable = high if position.is_long else low
        profit_r = (favourable - entry) / r if position.is_long else (entry - favourable) / r

        if not position.tp1_done and profit_r >= position.tp1_r:
            return [self._reduce(position, ctx, D("0.33"), "tp1")]
        if position.tp1_done and not position.tp2_done and profit_r >= position.tp2_r:
            return [self._reduce(position, ctx, D("0.33"), "tp2")]
        if bars_held >= (position.time_stop_bars or 32):
            return [self._close(position, ctx, "time stop")]

        # 3. Stop management: breakeven, then ATR trail.  Only ever tighter.
        new_stop = self._next_stop(position, view, close, entry, r, profit_r)
        if new_stop is not None:
            return [
                Intent.move_stop(
                    strategy_id=self.id,
                    symbol=position.symbol,
                    ts_ms=ctx.now_ms,
                    book=position.book,
                    new_stop=new_stop,
                    reason="breakeven" if profit_r < self.trail_r else "trail",
                )
            ]
        return []

    def _next_stop(
        self,
        position: Position,
        view: SymbolView,
        close: Decimal,
        entry: Decimal,
        r: Decimal,
        profit_r: Decimal,
    ) -> Decimal | None:
        if profit_r < self.breakeven_r:
            return None
        buffer = max(r * D("0.05"), close * D("0.001"))
        if profit_r >= self.trail_r:
            atr_value = self._atr(view)
            if atr_value <= ZERO:
                return None
            candidate = (
                close - self.trail_atr_mult * atr_value
                if position.is_long
                else close + self.trail_atr_mult * atr_value
            )
        else:
            candidate = entry + buffer if position.is_long else entry - buffer

        if position.is_long:
            if candidate >= close - buffer:
                return None
            if position.stop_px is not None and candidate <= position.stop_px:
                return None
        else:
            if candidate <= close + buffer:
                return None
            if position.stop_px is not None and candidate >= position.stop_px:
                return None
        return candidate

    @staticmethod
    def _atr(view: SymbolView) -> Decimal:
        from oq.data.features import atr

        series = view.feature("atr:14", lambda: atr(view.high, view.low, view.close, 14))
        value = float(series[view.i])
        if value != value:  # NaN
            return ZERO
        return D(value)

    @staticmethod
    def _bars_held(position: Position, ctx: StrategyContext) -> int:
        if position.opened_bar is not None and ctx.bar_index is not None:
            return max(0, ctx.bar_index - position.opened_bar)
        return max(0, int((ctx.now_ms - position.opened_ms) / 900_000))

    def _close(self, position: Position, ctx: StrategyContext, reason: str) -> Intent:
        return Intent.close_position(
            strategy_id=self.id,
            symbol=position.symbol,
            ts_ms=ctx.now_ms,
            book=position.book,
            side=self._closing_side(position),
            reason=reason,
        )

    def _reduce(
        self, position: Position, ctx: StrategyContext, fraction: Decimal, tag: str
    ) -> Intent:
        return Intent.close_position(
            strategy_id=self.id,
            symbol=position.symbol,
            ts_ms=ctx.now_ms,
            book=position.book,
            side=self._closing_side(position),
            fraction=fraction,
            reason=tag,
        )

    @staticmethod
    def _closing_side(position: Position) -> Side:
        return Side.SELL if position.is_long else Side.BUY


__all__ = ["PositionManager"]
