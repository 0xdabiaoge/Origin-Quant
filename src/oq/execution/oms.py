"""Order management: intent -> orders -> fills -> portfolio -> journal.

This is the only place that turns a risk-approved plan into an order.  It is
shared verbatim by backtest, paper and live; the broker is the only thing
that changes.

Failure handling that the reference projects lacked:

* multi-leg entries unwind the filled leg when a sibling leg fails, so the
  book is never left with half a hedge;
* a venue that returns no acknowledgement leaves the order in ``UNKNOWN``
  instead of guessing;
* every exit is ``reduce_only`` so a stale intent can never open a position.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from oq.config import Config
from oq.domain.events import EventType, new_event_id
from oq.domain.intent import Intent, LegPlan, RiskDecision
from oq.domain.order import Fill, Order, make_client_order_id
from oq.domain.portfolio import Portfolio
from oq.domain.position import Position
from oq.execution.broker import Broker, BrokerContext
from oq.execution.journal import Journal
from oq.storage.storage import TradeRecord
from oq.types import ZERO, Action, D, Instrument, OrderStatus, OrderType, Side


@dataclass(slots=True)
class ExecutionResult:
    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class _Snap:
    position: Position
    entry_px: Decimal
    risk_per_unit: Decimal
    is_long: bool


class OrderManager:
    def __init__(
        self,
        config: Config,
        instruments: dict[str, Instrument],
        portfolio: Portfolio,
        journal: Journal,
    ) -> None:
        self.config = config
        self.instruments = instruments
        self.portfolio = portfolio
        self.journal = journal

    def execute(
        self,
        decision: RiskDecision,
        intent: Intent,
        broker: Broker,
        ctx: BrokerContext,
    ) -> ExecutionResult:
        result = ExecutionResult()
        if not decision.approved:
            return result
        if intent.action is Action.MOVE_STOP:
            self._apply_stop_move(intent, ctx, result)
            return result
        if not decision.legs:
            return result

        snapshots = self._snapshots(decision, intent)
        filled: list[tuple[Order, Instrument]] = []

        for index, plan in enumerate(decision.legs):
            instrument = self.instruments.get(plan.symbol)
            if instrument is None:
                result.errors.append(f"unknown instrument {plan.symbol}")
                continue
            order = self._build_order(index, plan, intent, decision, ctx)
            self.journal.record_order(order)
            result.orders.append(order)

            broker_result = broker.place(order, instrument, ctx)
            if broker_result.rejected:
                order.transition(
                    OrderStatus.REJECTED, ts_ms=ctx.now_ms, error=broker_result.rejected
                )
                self.journal.record_order(order)
                result.errors.append(f"{plan.symbol}: {broker_result.rejected}")
                continue

            if broker_result.venue_order_id:
                order.venue_order_id = broker_result.venue_order_id
            if not broker_result.fills:
                order.transition(OrderStatus.UNKNOWN, ts_ms=ctx.now_ms, error="no acknowledgement")
                self.journal.record_order(order)
                result.errors.append(f"{plan.symbol}: no acknowledgement")
                continue

            order.transition(OrderStatus.ACKED, ts_ms=ctx.now_ms)
            for fill in broker_result.fills:
                order.apply_fill(fill.qty, fill.px, fill.fee, ts_ms=fill.ts_ms)
                result.fills.append(fill)
                self.journal.record_fill(fill)
                self._apply_fill(fill, plan, intent, ctx)
            self.journal.record_order(order)
            filled.append((order, instrument))

        # A partially executed hedge is worse than no hedge.
        if result.errors and len(intent.legs) > 1 and intent.action is Action.OPEN and filled:
            for order, instrument in filled:
                self._unwind(order, instrument, broker, ctx, result)

        for trade in self._trades(decision, intent, snapshots, result):
            result.trades.append(trade)
            self.journal.record_trade(trade)

        self.journal.sync_positions(self.portfolio)
        return result

    # -- stop maintenance ----------------------------------------------
    def _apply_stop_move(self, intent: Intent, ctx: BrokerContext, result: ExecutionResult) -> None:
        symbol = intent.legs[0].symbol
        position = self.portfolio.get(symbol, intent.book)
        if position is None or intent.stop_px is None:
            result.errors.append(f"stop move without position: {symbol}")
            return
        if not position.move_stop(intent.stop_px):
            return
        position.updated_ms = ctx.now_ms
        self.journal.sync_positions(self.portfolio)
        self.journal.record_event(
            EventType.STOP_MOVED,
            {
                "symbol": symbol,
                "book": intent.book.value,
                "stop_px": str(intent.stop_px),
                "reason": intent.tags.get("reason", ""),
            },
            ts_ms=ctx.now_ms,
            symbol=symbol,
        )

    # -- construction ---------------------------------------------------
    def _build_order(
        self,
        index: int,
        plan: LegPlan,
        intent: Intent,
        decision: RiskDecision,
        ctx: BrokerContext,
    ) -> Order:
        instrument = self.instruments[plan.symbol]
        is_exit = intent.action in {Action.CLOSE, Action.REDUCE}
        px = None
        order_type = OrderType.MARKET
        return Order(
            client_id=make_client_order_id(intent.id, index),
            symbol=plan.symbol,
            inst_type=instrument.inst_type,
            side=plan.side,
            order_type=order_type,
            qty=plan.qty,
            px=px,
            reduce_only=is_exit and self.config.risk.reduce_only_on_exit,
            status=OrderStatus.SUBMITTING,
            intent_id=intent.id,
            cycle_id=intent.tags.get("cycle_id"),
            strategy_id=intent.strategy_id,
            book=intent.book,
            created_ms=ctx.now_ms,
            updated_ms=ctx.now_ms,
        )

    # -- application ----------------------------------------------------
    def _apply_fill(self, fill: Fill, plan: LegPlan, intent: Intent, ctx: BrokerContext) -> None:
        instrument = self.instruments[plan.symbol]
        time_stop = intent.time_stop_bars
        tp1_r = intent.tp_ladder[0][0] if intent.tp_ladder else D("1.5")
        tp2_r = intent.tp_ladder[1][0] if len(intent.tp_ladder) > 1 else D("3.0")
        position, _ = self.portfolio.apply_fill(
            symbol=fill.symbol,
            book=intent.book,
            inst_type=instrument.inst_type,
            side=fill.side,
            qty=fill.qty,
            px=fill.px,
            fee=fill.fee,
            ts_ms=fill.ts_ms,
            strategy_id=intent.strategy_id,
            stop_px=plan.stop_px if intent.action is Action.OPEN else None,
            tp1_r=tp1_r,
            tp2_r=tp2_r,
            time_stop_bars=time_stop,
        )
        bar_index = ctx.tags.get("bar_index")
        if bar_index is not None and position.opened_bar is None:
            try:
                position.opened_bar = int(bar_index)
            except (TypeError, ValueError):
                pass

        if intent.action is Action.REDUCE:
            reason = intent.tags.get("reason", "")
            if reason == "tp1":
                position.tp1_done = True
            elif reason == "tp2":
                position.tp2_done = True

    def _snapshots(self, decision: RiskDecision, intent: Intent) -> list[_Snap]:
        if intent.action not in {Action.CLOSE, Action.REDUCE}:
            return []
        out: list[_Snap] = []
        for plan in decision.legs:
            position = self.portfolio.get(plan.symbol, intent.book)
            if position is None:
                continue
            # R must be measured against the stop the trade was *sized* with.
            # A trailed or breakeven stop would shrink the denominator and
            # inflate every winner's R.
            original_stop = position.initial_stop_px or position.stop_px
            risk = abs(position.avg_px - original_stop) if original_stop else ZERO
            out.append(
                _Snap(
                    position=position,
                    entry_px=position.avg_px,
                    risk_per_unit=risk,
                    is_long=position.is_long,
                )
            )
        return out

    def _trades(
        self,
        decision: RiskDecision,
        intent: Intent,
        snapshots: list[_Snap],
        result: ExecutionResult,
    ) -> list[TradeRecord]:
        trades: list[TradeRecord] = []
        if intent.action not in {Action.CLOSE, Action.REDUCE}:
            return trades
        reason = intent.tags.get("reason", "exit")
        for snap in snapshots:
            position = snap.position
            if position.qty != ZERO:
                continue  # still open: a partial reduce, not a trade
            exit_px = self._exit_price(result, position.symbol)
            if exit_px <= ZERO:
                exit_px = position.avg_px
            pnl = position.realized_pnl + position.funding_net - position.fees_paid
            # R is measured on *net* PnL.  A gross R that ignores fees can be
            # positive on a losing trade, which makes expectancy a fiction.
            r_multiple = None
            risk_capital = snap.risk_per_unit * position.initial_qty
            if risk_capital > ZERO:
                r_multiple = pnl / risk_capital
            trades.append(
                TradeRecord(
                    id=new_event_id(intent.ts_ms),
                    symbol=position.symbol,
                    book=position.book,
                    strategy_id=position.strategy_id,
                    pos_side="long" if snap.is_long else "short",
                    entry_ms=position.opened_ms,
                    exit_ms=intent.ts_ms,
                    entry_px=snap.entry_px,
                    exit_px=exit_px,
                    qty=position.initial_qty,
                    pnl=pnl,
                    fees=position.fees_paid,
                    funding=position.funding_net,
                    r_multiple=r_multiple,
                    reason=reason,
                )
            )
            # Re-entry churn, not bad entries, is what usually kills a
            # breakout system: the breakout is still "true" right after a time
            # stop, so it re-enters and pays the spread again.
            minutes = self.config.risk.reentry_cooldown_minutes
            if reason.startswith("stop"):
                minutes = max(minutes, self.config.risk.stop_cooldown_minutes)
            if minutes > 0:
                until = intent.ts_ms + minutes * 60_000
                self.journal.add_cooldown(position.symbol, position.book, until)
        return trades

    @staticmethod
    def _exit_price(result: ExecutionResult, symbol: str) -> Decimal:
        for fill in reversed(result.fills):
            if fill.symbol == symbol:
                return fill.px
        return ZERO

    def _unwind(
        self,
        order: Order,
        instrument: Instrument,
        broker: Broker,
        ctx: BrokerContext,
        result: ExecutionResult,
    ) -> None:
        if order.filled_qty <= ZERO:
            return
        closing_side = Side.SELL if order.side is Side.BUY else Side.BUY
        unwind = Order(
            client_id=make_client_order_id(order.intent_id or order.client_id, 90, 1),
            symbol=order.symbol,
            inst_type=instrument.inst_type,
            side=closing_side,
            order_type=OrderType.MARKET,
            qty=order.filled_qty,
            reduce_only=True,
            status=OrderStatus.SUBMITTING,
            intent_id=order.intent_id,
            book=order.book,
            created_ms=ctx.now_ms,
            updated_ms=ctx.now_ms,
        )
        self.journal.record_order(unwind)
        broker_result = broker.place(unwind, instrument, ctx)
        if broker_result.rejected or not broker_result.fills:
            unwind.transition(OrderStatus.UNKNOWN, ts_ms=ctx.now_ms, error="unwind failed")
            self.journal.record_order(unwind)
            result.errors.append(f"unwind failed for {order.symbol}")
            self.journal.record_event(
                EventType.ERROR,
                {"kind": "unwind_failed", "symbol": order.symbol, "qty": str(order.filled_qty)},
                ts_ms=ctx.now_ms,
                symbol=order.symbol,
            )
            return
        unwind.transition(OrderStatus.ACKED, ts_ms=ctx.now_ms)
        for fill in broker_result.fills:
            unwind.apply_fill(fill.qty, fill.px, fill.fee, ts_ms=fill.ts_ms)
            result.fills.append(fill)
            self.journal.record_fill(fill)
            self.portfolio.apply_fill(
                symbol=fill.symbol,
                book=order.book,
                inst_type=instrument.inst_type,
                side=fill.side,
                qty=fill.qty,
                px=fill.px,
                fee=fill.fee,
                ts_ms=fill.ts_ms,
                strategy_id=order.strategy_id or "",
            )
        self.journal.record_order(unwind)
        self.journal.record_event(
            EventType.ERROR,
            {"kind": "hedge_unwound", "symbol": order.symbol, "qty": str(order.filled_qty)},
            ts_ms=ctx.now_ms,
            symbol=order.symbol,
        )


__all__ = ["ExecutionResult", "OrderManager"]
