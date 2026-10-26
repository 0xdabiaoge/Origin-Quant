"""The trading loop.

One process, one clock, one writer.  A cycle is:

    data -> views -> strategies -> risk -> execution -> accounting -> events

Nothing in the chain can be skipped: if data is stale the cycle refuses to
trade, if risk rejects the intent the decision is still recorded, and if the
venue does not acknowledge an order the order stays ``UNKNOWN`` for the
reconciler rather than being assumed filled or failed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from oq.clock import tf_to_ms
from oq.config import Config
from oq.data.ingest import Ingestor
from oq.data.models import Quote
from oq.data.store import ParquetStore
from oq.domain.events import EventType, new_event_id
from oq.domain.intent import Intent
from oq.domain.portfolio import Portfolio
from oq.execution.broker import Broker, BrokerContext
from oq.execution.journal import Journal, StoreJournal
from oq.execution.oms import ExecutionResult, OrderManager
from oq.execution.paper import InstantQuoteBroker
from oq.risk.context import RiskContext
from oq.risk.engine import RiskEngine
from oq.strategy.base import Strategy, StrategyContext, SymbolView
from oq.strategy.registry import build_position_manager, build_strategies
from oq.types import ZERO, Book, D, Instrument

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CycleReport:
    cycle_id: str
    ts_ms: int
    equity: Decimal
    intents: int = 0
    approved: int = 0
    rejected: int = 0
    fills: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors and not self.skipped


class Orchestrator:
    def __init__(
        self,
        config: Config,
        db,
        store,
        ingestor: Ingestor,
        *,
        broker: Broker | None = None,
        journal: Journal | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.store = store
        self.ingestor = ingestor
        self.instruments: dict[str, Instrument] = config.instrument_map()
        starting_cash = ZERO if config.app.mode == "live" else config.app.paper_cash
        self.portfolio: Portfolio = store.load_portfolio(default_cash=starting_cash)
        self.journal = journal or StoreJournal(db, store)
        self.oms = OrderManager(config, self.instruments, self.portfolio, self.journal)
        self.risk = RiskEngine(config, self.instruments)
        self.strategies: list[Strategy] = build_strategies(config)
        self.position_manager = build_position_manager()
        self.broker: Broker = broker or InstantQuoteBroker()
        self._halted = False
        self._halt_reason = ""
        self._kill = False

    # -- state ---------------------------------------------------------
    @property
    def halted(self) -> bool:
        return self._halted

    def halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        self.journal.record_event(
            EventType.RISK_HALT, {"reason": reason}, ts_ms=int(time.time() * 1000)
        )

    def resume(self) -> None:
        self._halted = False
        self._halt_reason = ""
        self.journal.record_event(
            EventType.RISK_HALT, {"reason": "manual resume"}, ts_ms=int(time.time() * 1000)
        )

    def engage_kill_switch(self, reason: str = "manual") -> None:
        self._kill = True
        self.journal.record_event(
            EventType.KILL_SWITCH, {"reason": reason}, ts_ms=int(time.time() * 1000)
        )

    # -- cycle ---------------------------------------------------------
    def run_once(self, now_ms: int | None = None) -> CycleReport:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        cycle_id = new_event_id(now)
        report = CycleReport(cycle_id=cycle_id, ts_ms=now, equity=self.portfolio.equity({}))

        self.journal.record_event(
            EventType.CYCLE_START,
            {"cycle_id": cycle_id, "mode": self.config.app.mode},
            ts_ms=now,
            cycle_id=cycle_id,
        )

        try:
            views, quotes, stale = self._build_views(now)
        except Exception as exc:  # data layer must never take the loop down silently
            log.exception("data refresh failed")
            report.errors.append(f"data: {exc}")
            self._finish(report, now)
            return report

        if not views:
            report.skipped = "no_data"
            self._finish(report, now)
            return report
        if stale:
            report.skipped = f"stale:{','.join(sorted(stale)[:5])}"
            self._finish(report, now)
            return report

        marks = {symbol: view.price for symbol, view in views.items()}
        atr_map = self._atr_map(views)
        self._apply_due_funding(now, marks)

        risk_ctx = RiskContext(
            now_ms=now,
            portfolio=self.portfolio,
            marks=marks,
            quotes=quotes,
            data_ok=dict.fromkeys(views, True),
            atr=atr_map,
            clusters={s: (item.cluster or "") for s, item in self.config.universe_map().items()},
            funding={s: v.last_funding for s, v in views.items()},
            cooldowns=self.journal.cooldowns(),
            trading_halted=self._halted,
            halt_reason=self._halt_reason,
            kill_switch=self._kill,
            config_hash=self.config.hash,
        )
        self._check_drawdown(risk_ctx, now)

        equity = self.portfolio.equity(marks)
        ctx = StrategyContext(
            now_ms=now,
            equity=equity,
            portfolio=self.portfolio,
            views=views,
            params={},
            config_hash=self.config.hash,
        )

        intents: list[Intent] = []
        for strategy in [*self.strategies, self.position_manager]:
            scoped = StrategyContext(
                now_ms=ctx.now_ms,
                equity=ctx.equity,
                portfolio=ctx.portfolio,
                views=self._views_for(views, strategy.book),
                params=ctx.params,
                config_hash=ctx.config_hash,
            )
            try:
                produced = list(strategy.generate(scoped))
            except Exception as exc:
                log.exception("strategy %s failed", strategy.id)
                report.errors.append(f"strategy:{strategy.id}:{exc}")
                continue
            intents.extend(produced)

        report.intents = len(intents)
        for intent in intents:
            intent.tags["cycle_id"] = cycle_id
            decision = self.risk.evaluate(intent, risk_ctx)
            self._record_decision(intent, decision, now, cycle_id)
            if not decision.approved:
                report.rejected += 1
                continue
            report.approved += 1
            if self.config.app.mode == "backtest":
                continue
            broker_ctx = BrokerContext(
                now_ms=now,
                price=marks.get(intent.legs[0].symbol, ZERO) if intent.legs else ZERO,
                prices=marks,
                quote=quotes.get(intent.legs[0].symbol) if intent.legs else None,
                tags={"cycle_id": cycle_id},
            )
            result: ExecutionResult = self.oms.execute(decision, intent, self.broker, broker_ctx)
            report.fills += len(result.fills)
            report.errors.extend(result.errors)

        self.portfolio.mark(marks, now)
        self.journal.record_equity(now, self.portfolio.snapshot(marks))
        report.equity = self.portfolio.equity(marks)
        self._finish(report, now)
        return report

    # -- helpers -------------------------------------------------------
    def _finish(self, report: CycleReport, now: int) -> None:
        """Close the cycle and make every in-memory change durable in one commit.

        The portfolio is mutated in place by fills, funding and marking, so it
        has to be written here. Persisting only the event used to leave the
        ledger claiming work that the projections never recorded: funding was
        applied in memory, its "already charged" marker was committed, and the
        money vanished on the next load.
        """
        with self.db.tx() as conn:
            self.store.emit(
                conn,
                EventType.CYCLE_END,
                {
                    "cycle_id": report.cycle_id,
                    "intents": report.intents,
                    "approved": report.approved,
                    "rejected": report.rejected,
                    "fills": report.fills,
                    "errors": report.errors,
                    "skipped": report.skipped,
                    "equity": str(report.equity),
                },
                ts_ms=now,
                cycle_id=report.cycle_id,
            )
            self.store.sync_positions(conn, self.portfolio)
            self.store.save_portfolio_state(conn, self.portfolio)

    def _views_for(self, views: Mapping[str, SymbolView], book: Book) -> dict[str, SymbolView]:
        universe = self.config.universe_map()
        out: dict[str, SymbolView] = {}
        for symbol, view in views.items():
            item = universe.get(symbol)
            if item is not None and item.allows(book):
                out[symbol] = view
        return out

    def _build_views(self, now: int) -> tuple[dict[str, SymbolView], dict[str, Quote], set[str]]:
        universe = self.config.universe_map()
        store = ParquetStore(self.config.data.parquet_dir)
        timeframe = self.config.data.timeframe
        views: dict[str, SymbolView] = {}
        quotes: dict[str, Quote] = {}
        stale: set[str] = set()
        freshness_ms = self.config.data.staleness_seconds * 1000
        bar_ms = tf_to_ms(timeframe)
        # The newest bar that *should* already be closed. A bar stamped `ts`
        # covers [ts, ts+bar) and closes at ts+bar, so at time `now` the newest
        # closed bar is the one that opened at floor(now)-bar.
        #
        # Staleness therefore means "a bar we should already have is missing",
        # not "the last close was more than N seconds ago". The latter would
        # only be true in the few seconds after each boundary and would make
        # the engine refuse to trade for the rest of the bar.
        expected_open_ms = (now // bar_ms) * bar_ms - bar_ms
        # Grace: the venue may publish the bar a little after the boundary.
        publish_deadline_ms = (now // bar_ms) * bar_ms + freshness_ms

        for symbol in universe:
            try:
                frame = store.read_bars(symbol, timeframe)
            except FileNotFoundError:
                stale.add(symbol)
                continue
            if frame is None or frame.empty:
                stale.add(symbol)
                continue
            import numpy as np

            ts = frame["ts_ms"].to_numpy(dtype=np.int64)
            last_open_ms = int(ts[-1])
            missing_bar = last_open_ms < expected_open_ms
            if missing_bar and now > publish_deadline_ms:
                stale.add(symbol)
                continue
            funding_frame = store.read_funding(symbol)
            funding_ts = (
                funding_frame["ts_ms"].to_numpy(dtype=np.int64)
                if "ts_ms" in funding_frame
                else None
            )
            funding_rates = (
                funding_frame["rate"].to_numpy(dtype=np.float64)
                if "rate" in funding_frame
                else None
            )
            views[symbol] = SymbolView(
                symbol=symbol,
                timeframe=timeframe,
                ts_ms=int(ts[-1]),
                open=frame["open"].to_numpy(dtype=np.float64),
                high=frame["high"].to_numpy(dtype=np.float64),
                low=frame["low"].to_numpy(dtype=np.float64),
                close=frame["close"].to_numpy(dtype=np.float64),
                volume=frame["volume"].to_numpy(dtype=np.float64),
                index=len(ts) - 1,
                funding_rates=funding_rates,
                funding_ts=funding_ts,
                quote=quotes.get(symbol),
            )
        return views, quotes, stale

    def _atr_map(self, views: Mapping[str, SymbolView]) -> dict[str, Decimal]:
        from oq.data.features import atr

        out: dict[str, Decimal] = {}
        for symbol, view in views.items():

            def _atr_of(v=view) -> np.ndarray:
                return atr(v.high, v.low, v.close, 14)

            series = view.feature("atr:14", _atr_of)
            value = float(series[view.i])
            if value == value and value > 0:
                out[symbol] = D(value)
        return out

    def _apply_due_funding(self, now: int, marks: Mapping[str, Decimal]) -> None:
        """Charge every funding settlement that fell due since the last cycle.

        Applied only to the carry book: directional swaps pay funding too, but
        their holding period is short enough that mixing it in obscures the
        carry PnL.

        The per-symbol "already charged" marker, the funding rows and the
        portfolio state commit together. Writing the marker on its own would
        make an interrupted cycle lose the payment permanently while refusing
        to charge it again.
        """
        store = ParquetStore(self.config.data.parquet_dir)
        applied: list[tuple[int, str, Book, Decimal, Decimal]] = []

        for position in self.portfolio.positions_of(Book.CARRY):
            last = int(self.store.get_kv(f"funding:{position.symbol}", 0) or 0)
            frame = store.read_funding(position.symbol)
            if frame is None or frame.empty or "ts_ms" not in frame:
                continue
            for raw_ts, raw_rate in zip(
                frame["ts_ms"].to_numpy(dtype=np.int64),
                frame["rate"].to_numpy(dtype=np.float64),
                strict=False,
            ):
                ts_ms = int(raw_ts)
                if ts_ms <= last or ts_ms > now:
                    continue
                rate = D(float(raw_rate))
                amount = -position.qty * (marks.get(position.symbol) or position.avg_px) * rate
                self.portfolio.apply_funding(position.symbol, position.book, amount, ts_ms)
                applied.append((ts_ms, position.symbol, position.book, amount, rate))

        if not applied:
            return

        with self.db.tx() as conn:
            for ts_ms, symbol, book, amount, rate in applied:
                self.store.save_funding(
                    conn, ts_ms=ts_ms, symbol=symbol, book=book, amount=amount, rate=rate
                )
                self.store.emit(
                    conn,
                    EventType.FUNDING,
                    {"symbol": symbol, "amount": str(amount), "rate": str(rate)},
                    ts_ms=ts_ms,
                    symbol=symbol,
                )
                self.store.set_kv(conn, f"funding:{symbol}", ts_ms)
            self.store.sync_positions(conn, self.portfolio)
            self.store.save_portfolio_state(conn, self.portfolio)

    def _check_drawdown(self, risk_ctx: RiskContext, now: int) -> None:
        risk = self.config.risk
        day = self.portfolio.day_pnl(risk_ctx.marks)
        if day <= -risk.daily_loss_flatten_pct:
            self._flatten(risk_ctx.marks, now, f"daily loss {day:.2%}")
        elif day <= -risk.daily_loss_halt_pct and not self._halted:
            self.halt(f"daily loss {day:.2%}")
        if self.portfolio.drawdown(risk_ctx.marks) >= risk.max_drawdown_pct and not self._halted:
            self.halt(f"drawdown {self.portfolio.drawdown(risk_ctx.marks):.2%}")

    def _flatten(self, marks: Mapping[str, Decimal], now: int, reason: str) -> None:
        from oq.types import Side

        self.engage_kill_switch(reason)
        for position in list(self.portfolio.positions_of()):
            side = Side.SELL if position.is_long else Side.BUY
            intent = Intent.close_position(
                strategy_id="orchestrator",
                symbol=position.symbol,
                ts_ms=now,
                book=position.book,
                side=side,
                reason=reason,
            )
            risk_ctx = RiskContext(
                now_ms=now,
                portfolio=self.portfolio,
                marks=marks,
                data_ok={},
                trading_halted=True,
            )
            decision = self.risk.evaluate(intent, risk_ctx)
            if not decision.approved:
                continue
            self.oms.execute(
                decision,
                intent,
                self.broker,
                BrokerContext(
                    now_ms=now,
                    price=marks.get(position.symbol, position.avg_px),
                    prices=dict(marks),
                ),
            )

    def _record_decision(self, intent: Intent, decision, now: int, cycle_id: str) -> None:
        try:
            with self.db.tx() as conn:
                self.store.save_decision(
                    conn,
                    decision_id=f"{cycle_id}:{intent.id[:12]}",
                    ts_ms=now,
                    cycle_id=cycle_id,
                    strategy_id=intent.strategy_id,
                    book=intent.book,
                    action=intent.action.value,
                    symbols=[leg.symbol for leg in intent.legs],
                    approved=decision.approved,
                    intent=intent.as_dict(),
                    risk={
                        "legs": [
                            {
                                "symbol": leg.symbol,
                                "qty": str(leg.qty),
                                "notional": str(leg.notional),
                                "risk_usd": str(leg.risk_usd),
                            }
                            for leg in decision.legs
                        ],
                        "adjustments": list(decision.adjustments),
                    },
                    gates=[g.as_dict() for g in decision.gates],
                )
                self.store.emit(
                    conn,
                    EventType.RISK_DECISION,
                    {"intent": intent.as_dict(), "decision": decision.as_dict()},
                    ts_ms=now,
                    cycle_id=cycle_id,
                    symbol=intent.legs[0].symbol if intent.legs else None,
                )
        except Exception:
            log.exception("failed to persist decision")


__all__ = ["CycleReport", "Orchestrator"]
