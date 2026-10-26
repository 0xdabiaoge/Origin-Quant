"""Event-driven backtester.

The loop is deliberately explicit:

1. apply funding that fell due in this bar's interval,
2. fill orders queued at the previous close, at this bar's **open**,
3. walk protective exits through the bar's high/low (stop before target),
4. run strategies at the close and queue their intents for the next open,
5. mark the book and record equity.

Point 2 is what keeps the numbers honest: a signal computed on a closed bar
cannot be filled at that same bar's close.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

import numpy as np

from oq.backtest.data import SymbolSeries
from oq.backtest.metrics import EquityPoint, compute_metrics
from oq.config import Config
from oq.domain.intent import Intent
from oq.domain.portfolio import Portfolio
from oq.execution.broker import BrokerContext
from oq.execution.journal import NullJournal
from oq.execution.oms import OrderManager
from oq.execution.paper import PaperBroker
from oq.risk.context import RiskContext
from oq.risk.engine import RiskEngine
from oq.storage.storage import TradeRecord
from oq.strategy.base import Strategy, StrategyContext, SymbolView
from oq.types import ONE, ZERO, Book, D, InstType


@dataclass(slots=True)
class BacktestParams:
    initial_cash: Decimal = D("10000")
    timeframe: str = "15m"
    label: str = "backtest"
    warmup_bars: int = 240
    maker_fee_override: Decimal | None = None


@dataclass(slots=True)
class BacktestResult:
    label: str
    start_ms: int
    end_ms: int
    initial_cash: Decimal
    equity: list[EquityPoint] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    rejections: dict[str, int] = field(default_factory=dict)
    decisions: int = 0
    intents: int = 0
    funding_total: Decimal = ZERO
    open_positions: int = 0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "initial_cash": str(self.initial_cash),
            "equity": [[p.ts_ms, str(p.equity)] for p in self.equity],
            "metrics": self.metrics,
            "rejections": self.rejections,
            "decisions": self.decisions,
            "intents": self.intents,
            "funding_total": str(self.funding_total),
            "open_positions": self.open_positions,
            "trades": [
                {
                    "symbol": t.symbol,
                    "book": t.book.value,
                    "strategy_id": t.strategy_id,
                    "entry_ms": t.entry_ms,
                    "exit_ms": t.exit_ms,
                    "entry_px": str(t.entry_px),
                    "exit_px": str(t.exit_px),
                    "qty": str(t.qty),
                    "pnl": str(t.pnl),
                    "fees": str(t.fees),
                    "funding": str(t.funding),
                    "r_multiple": None if t.r_multiple is None else str(t.r_multiple),
                    "reason": t.reason,
                }
                for t in self.trades
            ],
        }


class BacktestEngine:
    def __init__(
        self,
        config: Config,
        strategies: Sequence[Strategy],
        position_manager: Strategy,
        *,
        instruments: Mapping[str, object] | None = None,
    ) -> None:
        self.config = config
        self.strategies = list(strategies)
        self.position_manager = position_manager
        self.instruments = dict(instruments or config.instrument_map())
        self.risk = RiskEngine(config, self.instruments)  # type: ignore[arg-type]
        self.broker = PaperBroker()
        self.journal = NullJournal()
        self.portfolio = Portfolio(cash=D("10000"))
        self.oms = OrderManager(config, self.instruments, self.portfolio, self.journal)  # type: ignore[arg-type]
        self._rejections: Counter[str] = Counter()
        self._decisions = 0
        self._intents = 0
        self._funding_idx: dict[str, int] = {}

    # -- public --------------------------------------------------------
    def run(self, series: Mapping[str, SymbolSeries], params: BacktestParams) -> BacktestResult:
        if not series:
            raise ValueError("no series to backtest")

        self.portfolio = Portfolio(cash=params.initial_cash)
        self.journal = NullJournal()
        self.oms = OrderManager(self.config, self.instruments, self.portfolio, self.journal)  # type: ignore[arg-type]
        self._rejections = Counter()
        self._decisions = 0
        self._intents = 0
        self._funding_idx = dict.fromkeys(series, 0)

        timeline = np.unique(np.concatenate([s.ts for s in series.values()]))
        pointers = dict.fromkeys(series, -1)
        pending: list[Intent] = []
        equity_curve: list[EquityPoint] = []
        marks: dict[str, Decimal] = {}
        warmup = max(params.warmup_bars, 1)

        for raw_ts in timeline:
            ts = int(raw_ts)
            active: dict[str, int] = {}
            for symbol, s in series.items():
                index = pointers[symbol]
                while index + 1 < s.size and s.ts[index + 1] <= ts:
                    index += 1
                pointers[symbol] = index
                active[symbol] = index

            if any(index < warmup for index in active.values()):
                continue

            marks = {
                symbol: D(float(series[symbol].close[index])) for symbol, index in active.items()
            }
            self._apply_funding(series, active, ts, marks)

            # 1. Fill yesterday's decisions at today's open.
            open_prices = {
                symbol: D(float(series[symbol].open[active[symbol]])) for symbol in series
            }
            for intent in pending:
                self._fill(intent, open_prices, active, ts, series)
            pending.clear()

            # 2. Protective exits inside the bar.
            self._protective(series, active, ts, marks)

            # 3. Decisions on the close.
            views = self._views(series, active, ts)
            ctx = StrategyContext(
                now_ms=ts,
                equity=self.portfolio.equity(marks),
                portfolio=self.portfolio,
                views=views,
                params={},
                config_hash=self.config.hash,
                bar_index=active[next(iter(series))],
            )
            for strategy in [*self.strategies, self.position_manager]:
                scoped = replace(ctx, views=self._views_for(views, strategy.book))
                for intent in strategy.generate(scoped):
                    self._intents += 1
                    pending.append(intent)

            equity = self.portfolio.mark(marks, ts)
            equity_curve.append(EquityPoint(ts_ms=ts, equity=equity))

        # Realise anything still open at the last close so the trade list is
        # complete, then re-mark: closing costs belong in the final equity.
        if self.portfolio.positions:
            self._flatten(series, pointers, int(timeline[-1]), marks)
            marks = {
                symbol: D(float(series[symbol].close[index]))
                for symbol, index in pointers.items()
                if index >= 0
            }
            final_equity = self.portfolio.mark(marks, int(timeline[-1]))
            if equity_curve:
                equity_curve[-1] = EquityPoint(ts_ms=equity_curve[-1].ts_ms, equity=final_equity)

        metrics = compute_metrics(
            equity_curve,
            self.journal.trades,
            initial_cash=params.initial_cash,
            periods_per_year=365.0,
        )
        return BacktestResult(
            label=params.label,
            start_ms=int(timeline[0]),
            end_ms=int(timeline[-1]),
            initial_cash=params.initial_cash,
            equity=equity_curve,
            trades=list(self.journal.trades),
            metrics=metrics,
            rejections=dict(self._rejections.most_common(20)),
            decisions=self._decisions,
            intents=self._intents,
            funding_total=self.portfolio.funding_net,
            open_positions=len(self.portfolio.positions),
        )

    # -- internals -----------------------------------------------------
    def _views(
        self, series: Mapping[str, SymbolSeries], active: Mapping[str, int], ts: int
    ) -> dict[str, SymbolView]:
        return {symbol: series[symbol].view(index, quote=None) for symbol, index in active.items()}

    def _views_for(self, views: Mapping[str, SymbolView], book: Book) -> dict[str, SymbolView]:
        universe = self.config.universe_map()
        out: dict[str, SymbolView] = {}
        for symbol, view in views.items():
            item = universe.get(symbol)
            if item is not None and item.allows(book):
                out[symbol] = view
        return out

    def _risk_context(
        self,
        series: Mapping[str, SymbolSeries],
        active: Mapping[str, int],
        ts: int,
        marks: Mapping[str, Decimal],
        views: Mapping[str, SymbolView],
    ) -> RiskContext:
        universe = self.config.universe_map()
        atr_map: dict[str, Decimal] = {}
        from oq.data.features import atr as atr_fn

        for symbol, view in views.items():

            def _atr_of(v=view) -> np.ndarray:
                return atr_fn(v.high, v.low, v.close, 14)

            arr = view.feature("atr:14", _atr_of)
            value = float(arr[view.i])
            if value == value and value > 0:
                atr_map[symbol] = D(value)
        return RiskContext(
            now_ms=ts,
            portfolio=self.portfolio,
            marks=marks,
            quotes={},
            data_ok=dict.fromkeys(views, True),
            atr=atr_map,
            clusters={symbol: (item.cluster or "") for symbol, item in universe.items()},
            funding={symbol: view.last_funding for symbol, view in views.items()},
            cooldowns=self.journal.cooldowns(),
            config_hash=self.config.hash,
        )

    def _fill(
        self,
        intent: Intent,
        prices: Mapping[str, Decimal],
        active: Mapping[str, int],
        ts: int,
        series: Mapping[str, SymbolSeries],
    ) -> None:
        views = self._views(series, {k: v - 1 if v > 0 else 0 for k, v in active.items()}, ts)
        # Risk is re-evaluated against the execution bar's open, so a gap that
        # invalidates the setup is caught here rather than filled blindly.
        marks = dict(prices)
        risk_ctx = self._risk_context(series, active, ts, marks, views)
        decision = self.risk.evaluate(intent, risk_ctx)
        self._decisions += 1
        if not decision.approved:
            gate = decision.gates[-1].code if decision.gates else "unknown"
            self._rejections[f"{gate}:{decision.reasons[0] if decision.reasons else ''}"] += 1
            return
        # OMS executes every leg of the decision itself; calling it once per
        # leg would double-fill a hedge and invert the book.
        broker_ctx = BrokerContext(
            now_ms=ts,
            price=next(iter(prices.values()), ZERO),
            prices=dict(prices),
            tags={
                "bar_index": str(active.get(intent.legs[0].symbol if intent.legs else "", 1) - 1)
            },
        )
        result = self.oms.execute(decision, intent, self.broker, broker_ctx)
        for error in result.errors:
            self._rejections[f"execution:{error}"] += 1

    def _protective(
        self,
        series: Mapping[str, SymbolSeries],
        active: Mapping[str, int],
        ts: int,
        marks: Mapping[str, Decimal],
    ) -> None:
        for position in list(self.portfolio.positions_of()):
            if position.book is Book.CARRY:
                continue
            index = active.get(position.symbol, 0)
            bar = series[position.symbol]
            high = D(float(bar.high[index]))
            low = D(float(bar.low[index]))
            open_px = D(float(bar.open[index]))
            entry = position.avg_px
            if position.stop_px is None:
                continue
            risk = abs(entry - position.stop_px)
            if risk <= ZERO:
                continue

            # Stop first: when a bar contains both levels, assume the bad one.
            if position.is_long and low <= position.stop_px:
                self._exit(
                    position, min(open_px, position.stop_px), "stop", ts, series, active, marks
                )
                continue
            if not position.is_long and high >= position.stop_px:
                self._exit(
                    position, max(open_px, position.stop_px), "stop", ts, series, active, marks
                )
                continue

            opened_bar = position.opened_bar if position.opened_bar is not None else index
            if index - opened_bar <= 1:
                continue  # never take a target on the entry bar

            if position.is_long:
                tp1 = entry + risk * position.tp1_r
                tp2 = entry + risk * position.tp2_r
                if not position.tp1_done and high >= tp1:
                    self._exit(
                        position,
                        max(tp1, open_px),
                        "tp1",
                        ts,
                        series,
                        active,
                        marks,
                        fraction=D("0.33"),
                    )
                elif position.tp1_done and not position.tp2_done and high >= tp2:
                    self._exit(
                        position,
                        max(tp2, open_px),
                        "tp2",
                        ts,
                        series,
                        active,
                        marks,
                        fraction=D("0.33"),
                    )
            else:
                tp1 = entry - risk * position.tp1_r
                tp2 = entry - risk * position.tp2_r
                if not position.tp1_done and low <= tp1:
                    self._exit(
                        position,
                        min(tp1, open_px),
                        "tp1",
                        ts,
                        series,
                        active,
                        marks,
                        fraction=D("0.33"),
                    )
                elif position.tp1_done and not position.tp2_done and low <= tp2:
                    self._exit(
                        position,
                        min(tp2, open_px),
                        "tp2",
                        ts,
                        series,
                        active,
                        marks,
                        fraction=D("0.33"),
                    )

    def _exit(
        self,
        position,
        price: Decimal,
        reason: str,
        ts: int,
        series: Mapping[str, SymbolSeries],
        active: Mapping[str, int],
        marks: Mapping[str, Decimal],
        fraction: Decimal | None = None,
    ) -> None:
        from oq.types import Side

        side = Side.SELL if position.is_long else Side.BUY
        intent = Intent.close_position(
            strategy_id=self.position_manager.id,
            symbol=position.symbol,
            ts_ms=ts,
            book=position.book,
            side=side,
            fraction=fraction if fraction is not None else ONE,
            reason=reason,
        )
        views = self._views(series, {k: v - 1 if v > 0 else 0 for k, v in active.items()}, ts)
        risk_ctx = self._risk_context(series, active, ts, marks, views)
        decision = self.risk.evaluate(intent, risk_ctx)
        self._decisions += 1
        if not decision.approved:
            self._rejections[f"exit:{decision.reasons[0] if decision.reasons else 'rejected'}"] += 1
            return
        index = active.get(position.symbol, 0)
        broker_ctx = BrokerContext(
            now_ms=ts,
            price=price,
            high=D(float(series[position.symbol].high[index])),
            low=D(float(series[position.symbol].low[index])),
            tags={"bar_index": str(index)},
        )
        self.oms.execute(decision, intent, self.broker, broker_ctx)

    def _apply_funding(
        self,
        series: Mapping[str, SymbolSeries],
        active: Mapping[str, int],
        ts: int,
        marks: Mapping[str, Decimal],
    ) -> None:
        for position in list(self.portfolio.positions.values()):
            key = position.symbol
            if position.inst_type is InstType.SPOT:
                continue
            s = series.get(key)
            if s is None or s.funding_ts.size == 0:
                continue
            cursor = self._funding_idx.get(key, 0)
            while cursor < s.funding_ts.size and s.funding_ts[cursor] <= ts:
                rate = D(float(s.funding_rates[cursor]))
                price = marks.get(key, position.avg_px)
                amount = -position.qty * price * rate
                self.portfolio.apply_funding(key, position.book, amount, ts)
                cursor += 1
            self._funding_idx[key] = cursor

    def _flatten(
        self,
        series: Mapping[str, SymbolSeries],
        pointers: Mapping[str, int],
        ts: int,
        marks: Mapping[str, Decimal],
    ) -> None:
        from oq.types import Side

        for position in list(self.portfolio.positions_of()):
            side = Side.SELL if position.is_long else Side.BUY
            intent = Intent.close_position(
                strategy_id=self.position_manager.id,
                symbol=position.symbol,
                ts_ms=ts,
                book=position.book,
                side=side,
                reason="end_of_test",
            )
            decision = self.risk.evaluate(
                intent,
                self._risk_context(series, pointers, ts, marks, self._views(series, pointers, ts)),
            )
            if decision.approved:
                index = pointers.get(position.symbol, 0)
                self.oms.execute(
                    decision,
                    intent,
                    self.broker,
                    BrokerContext(
                        now_ms=ts,
                        price=marks.get(position.symbol, position.avg_px),
                        high=D(float(series[position.symbol].high[index])),
                        low=D(float(series[position.symbol].low[index])),
                        tags={"bar_index": str(index)},
                    ),
                )


__all__ = ["BacktestEngine", "BacktestParams", "BacktestResult"]
