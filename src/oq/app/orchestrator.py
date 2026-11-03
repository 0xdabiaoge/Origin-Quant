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
import queue
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

import numpy as np

from oq.clock import tf_to_ms
from oq.config import Config
from oq.data.ingest import Ingestor
from oq.data.models import Quote
from oq.data.store import ParquetStore
from oq.domain.events import EventType, new_event_id
from oq.domain.intent import GateResult, Intent, RiskDecision
from oq.domain.portfolio import Portfolio
from oq.execution.broker import Broker, BrokerContext
from oq.execution.journal import Journal, StoreJournal
from oq.execution.oms import ExecutionResult, OrderManager
from oq.execution.paper import InstantQuoteBroker
from oq.risk.context import RiskContext
from oq.risk.engine import RiskEngine
from oq.strategy.base import Strategy, StrategyContext, SymbolView
from oq.strategy.registry import build_position_manager, build_strategies
from oq.types import ZERO, Action, Book, D, Instrument, InstType

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
        state = store.get_kv("trading_controls", {})
        if not isinstance(state, dict):
            state = {"halted": True, "reason": "invalid persisted trading controls", "kill": True}
        self._halted = bool(state.get("halted", False))
        self._halt_reason = str(state.get("reason", ""))
        self._kill = bool(state.get("kill", False))
        self.last_marks: dict[str, Decimal] = {}
        self._ai_worker_lock = threading.Lock()
        self._cycle_lock = threading.Lock()

    # -- state ---------------------------------------------------------
    @property
    def halted(self) -> bool:
        return self._halted

    def halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        self._persist_controls(EventType.RISK_HALT, reason)

    def resume(self) -> None:
        self._halted = False
        self._halt_reason = ""
        self._persist_controls(EventType.RISK_HALT, "manual resume; kill switch unchanged")

    def engage_kill_switch(self, reason: str = "manual") -> None:
        self._kill = True
        self._persist_controls(EventType.KILL_SWITCH, reason)

    def reset_kill_switch(self, reason: str = "manual reset") -> None:
        """Explicitly reset the kill switch; ordinary resume does not reset it."""
        self._kill = False
        self._persist_controls(EventType.KILL_SWITCH, reason)

    def _persist_controls(self, event_type: EventType, reason: str) -> None:
        state = {"halted": self._halted, "reason": self._halt_reason, "kill": self._kill}
        with self.db.tx() as conn:
            self.store.set_kv(conn, "trading_controls", state)
            self.store.emit(
                conn, event_type, {**state, "event_reason": reason}, ts_ms=int(time.time() * 1000)
            )

    # -- cycle ---------------------------------------------------------
    def run_once(self, now_ms: int | None = None) -> CycleReport:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        if not self._cycle_lock.acquire(blocking=False):
            return CycleReport(
                cycle_id=new_event_id(now),
                ts_ms=now,
                equity=self.portfolio.equity({}),
                skipped="cycle_in_progress",
            )
        try:
            return self._run_once(now)
        finally:
            self._cycle_lock.release()

    def _run_once(self, now_ms: int | None = None) -> CycleReport:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        # A skipped/failed cycle must not republish a previous mark as fresh.
        self.last_marks = {}
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
            # Do not open on an incomplete universe, but keep managing existing
            # positions whose own data is available.
            report.skipped = f"stale:{','.join(sorted(stale)[:5])}"

        marks = {symbol: view.price for symbol, view in views.items()}
        if not stale:
            self.last_marks = dict(marks)
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
            trading_halted=self._halted or bool(stale),
            halt_reason=self._halt_reason or ("incomplete market data" if stale else ""),
            kill_switch=self._kill,
            config_hash=self.config.hash,
        )
        if not stale:
            self._check_drawdown(risk_ctx, now)

        exited_symbols: set[str] = set()
        protection_failed = False

        def generate(strategy: Strategy) -> list[Intent]:
            nonlocal protection_failed
            scoped = StrategyContext(
                now_ms=now,
                equity=self.portfolio.equity(marks),
                portfolio=self.portfolio,
                views=self._views_for(views, strategy.book),
                params={},
                config_hash=self.config.hash,
            )
            try:
                return list(strategy.generate(scoped))
            except Exception as exc:
                log.exception("strategy %s failed", strategy.id)
                report.errors.append(f"strategy:{strategy.id}:{exc}")
                if strategy is self.position_manager:
                    protection_failed = True
                return []

        def execute_intent(intent: Intent, intent_now: int) -> None:
            intent.tags["cycle_id"] = cycle_id
            symbols = {str(leg.symbol) for leg in intent.legs}
            if intent.action in {Action.CLOSE, Action.REDUCE}:
                # Even a rejected/unknown exit prevents a same-cycle re-entry.
                exited_symbols.update(symbols)
            if intent.action is Action.OPEN and symbols & exited_symbols:
                reason = "entry blocked after a same-cycle exit attempt"
                decision = RiskDecision(
                    intent_id=intent.id,
                    approved=False,
                    legs=(),
                    gates=(GateResult("same_cycle_exit", False, reason),),
                    action=intent.action,
                    reasons=(reason,),
                )
                self._record_decision(intent, decision, intent_now, cycle_id)
                report.rejected += 1
                return
            pending = self.config.app.mode == "live" and bool(self.store.open_orders())
            current_risk = replace(
                risk_ctx,
                now_ms=intent_now,
                cooldowns=self.journal.cooldowns(),
                trading_halted=self._halted or bool(stale) or pending or protection_failed,
                halt_reason=(
                    "unresolved orders require reconciliation"
                    if pending
                    else "position management failed"
                    if protection_failed
                    else self._halt_reason or ("incomplete market data" if stale else "")
                ),
                kill_switch=self._kill,
            )
            decision = self.risk.evaluate(intent, current_risk)
            if decision.approved and intent.action is Action.OPEN and (self._halted or self._kill):
                # A control request may arrive after risk evaluation. Record
                # the actual rejection rather than an approved phantom order.
                reason = "entry canceled: trading control changed"
                decision = replace(
                    decision,
                    approved=False,
                    gates=(*decision.gates, GateResult("trading_control_changed", False, reason)),
                    reasons=(*decision.reasons, reason),
                )
            self._record_decision(intent, decision, intent_now, cycle_id)
            if not decision.approved:
                report.rejected += 1
                return
            report.approved += 1
            if self.config.app.mode == "backtest":
                return
            broker_ctx = BrokerContext(
                now_ms=intent_now,
                price=marks.get(intent.legs[0].symbol, ZERO) if intent.legs else ZERO,
                prices=marks,
                quote=quotes.get(intent.legs[0].symbol) if intent.legs else None,
                tags={"cycle_id": cycle_id},
            )
            result: ExecutionResult = self.oms.execute(decision, intent, self.broker, broker_ctx)
            report.fills += len(result.fills)
            report.errors.extend(result.errors)

        # Manage existing positions before producing any new strategy entries.
        # The same market snapshot is used throughout the cycle, while the
        # portfolio and cooldown state reflect completed earlier executions.
        protective = generate(self.position_manager)
        report.intents += len(protective)
        for intent in protective:
            execute_intent(intent, now)

        intents: list[Intent] = []
        if not stale:
            for strategy in self.strategies:
                intents.extend(generate(strategy))
        report.intents += len(intents)
        for intent in intents:
            if intent.action is not Action.OPEN:
                execute_intent(intent, now)

        confirmation_settings = None
        confirmation_settings_invalid = False
        if self.config.app.mode == "paper" and any(i.action is Action.OPEN for i in intents):
            from oq.app.ai_api import _settings

            try:
                confirmation_settings = _settings(self)
            except Exception:
                # Corrupt/unreadable policy must not turn a configured review
                # into permission to open positions.
                confirmation_settings_invalid = True
                log.exception("paper AI confirmation settings unavailable")

        def reject_confirmation(intent: Intent, reason: str) -> None:
            intent.tags["cycle_id"] = cycle_id
            decision = RiskDecision(
                intent_id=intent.id,
                approved=False,
                legs=(),
                gates=(GateResult("ai_confirmation", False, reason),),
                action=intent.action,
                reasons=(reason,),
            )
            self._record_decision(intent, decision, int(time.time() * 1000), cycle_id)
            report.rejected += 1

        for intent in intents:
            if intent.action is Action.OPEN:
                if confirmation_settings_invalid and intent.book is Book.DIRECTIONAL:
                    reject_confirmation(intent, "AI confirmation settings unavailable")
                    continue
                if (
                    confirmation_settings is not None
                    and confirmation_settings.paper_confirmation_enabled
                    and intent.book is Book.DIRECTIONAL
                    and confirmation_settings.auto_symbol
                    in {str(leg.symbol) for leg in intent.legs}
                    and not stale
                    and not protection_failed
                    and not self._halted
                    and not self._kill
                    and not ({str(leg.symbol) for leg in intent.legs} & exited_symbols)
                ):
                    from oq.app.ai_api import _settings, paper_confirmation_applies

                    if not paper_confirmation_applies(confirmation_settings, intent):
                        reject_confirmation(intent, "unsupported AI confirmation intent")
                        continue
                    review = self._confirm_paper_ai(views, intent, now)
                    if not review.confirmed or not review.record_id:
                        reject_confirmation(intent, f"AI confirmation: {review.reason}")
                        continue
                    try:
                        if _settings(self) != confirmation_settings:
                            reject_confirmation(intent, "AI confirmation settings changed")
                            continue
                    except Exception:
                        reject_confirmation(intent, "AI confirmation settings unavailable")
                        continue
                    current_ms = int(time.time() * 1000)
                    step = tf_to_ms(self.config.data.timeframe)
                    current_open = (current_ms // step) * step - step
                    if views[confirmation_settings.auto_symbol].ts_ms != current_open:
                        reject_confirmation(intent, "AI confirmation candle expired")
                        continue
                    intent.tags["ai_confirmation_id"] = review.record_id
                    execute_intent(intent, current_ms)
                else:
                    execute_intent(intent, now)

        if (
            not stale
            and not protection_failed
            and self.config.app.mode == "paper"
            and not self._halted
            and not self._kill
        ):
            try:
                ai_intent = self._collect_paper_ai(views, now)
                if ai_intent is not None:
                    report.intents += 1
                    execute_intent(ai_intent, int(time.time() * 1000))
            except Exception:
                # AI failure cannot undo or prevent already processed exits.
                log.exception("paper AI candidate failed")
                report.errors.append("ai candidate unavailable")

        if not stale:
            self.portfolio.mark(marks, now)
            self.journal.record_equity(now, self.portfolio.snapshot(marks))
            report.equity = self.portfolio.equity(marks)
        self._finish(report, now)
        return report

    def _collect_paper_ai(self, views: dict[str, SymbolView], now: int) -> Intent | None:
        """Wait at most 15 seconds, then discard late AI results for this cycle."""
        from oq.app.ai_api import CONFIG_KEY, collect_paper_intent

        settings = self.store.get_kv(CONFIG_KEY, {})
        if (
            not isinstance(settings, dict)
            or not settings.get("enabled")
            or not settings.get("auto_paper_enabled")
        ):
            return None
        if not self._ai_worker_lock.acquire(blocking=False):
            return None
        result: queue.Queue[Intent | None] = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                result.put(collect_paper_intent(self, views, now_ms=now))
            except Exception:
                log.exception("paper AI request failed")
                result.put(None)
            finally:
                self._ai_worker_lock.release()

        threading.Thread(target=run, name="oq-paper-ai", daemon=True).start()
        try:
            return result.get(timeout=15)
        except queue.Empty:
            log.warning("paper AI candidate timed out; late result will not be executed")
            return None

    def _confirm_paper_ai(self, views: dict[str, SymbolView], intent: Intent, now: int):
        """Wait at most 15 seconds; a late review cannot authorize an entry."""
        from oq.app.ai_api import PaperConfirmation, confirm_paper_strategy_intent

        if not self._ai_worker_lock.acquire(blocking=False):
            return PaperConfirmation(False, "worker_busy")
        result: queue.Queue[PaperConfirmation] = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                result.put(confirm_paper_strategy_intent(self, views, intent, now_ms=now))
            except Exception:
                log.exception("paper AI confirmation failed")
                result.put(PaperConfirmation(False, "review_unavailable"))
            finally:
                self._ai_worker_lock.release()

        threading.Thread(target=run, name="oq-paper-confirm", daemon=True).start()
        try:
            return result.get(timeout=15)
        except queue.Empty:
            return PaperConfirmation(False, "timeout")

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
        for symbol in universe:
            try:
                frame = store.read_bars(symbol, timeframe)
            except FileNotFoundError:
                stale.add(symbol)
                continue
            if frame is None or frame.empty:
                stale.add(symbol)
                continue
            # Only closed and venue-confirmed candles may inform a decision.
            # A fresh timestamp alone does not imply that its bar has closed.
            if "confirmed" not in frame.columns or "ts_ms" not in frame.columns:
                stale.add(symbol)
                continue
            frame = frame.loc[frame["confirmed"].eq(True) & (frame["ts_ms"] <= expected_open_ms)]
            if frame.empty:
                stale.add(symbol)
                continue
            import numpy as np

            ts = frame["ts_ms"].to_numpy(dtype=np.int64)
            last_open_ms = int(ts[-1])
            if last_open_ms < expected_open_ms:
                # Publication delay is not evidence that an old bar is the
                # latest closed bar. Never open or manage from stale OHLC.
                stale.add(symbol)
                continue
            if self.config.app.mode == "live":
                try:
                    rest = self.ingestor.rest
                    if rest is not None:
                        quotes[symbol] = rest.ticker(symbol)
                except Exception:
                    # The risk gate rejects new live entries without a quote.
                    log.warning("quote unavailable for %s", symbol)
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

        Apply to each swap book separately; never charge a settlement preceding
        the current position. Marks are an estimate, not exchange settlement
        history, and live accounting needs venue reconciliation.

        The per-symbol "already charged" marker, the funding rows and the
        portfolio state commit together. Writing the marker on its own would
        make an interrupted cycle lose the payment permanently while refusing
        to charge it again.
        """
        store = ParquetStore(self.config.data.parquet_dir)
        applied: list[tuple[int, str, Book, Decimal, Decimal]] = []

        for position in self.portfolio.positions_of():
            if position.inst_type is not InstType.SWAP:
                continue
            key = f"funding:{position.book.value}:{position.symbol}"
            last = int(self.store.get_kv(key, 0) or 0)
            if position.book is Book.CARRY:
                # Preserve existing carry markers from pre-book-aware versions.
                last = max(last, int(self.store.get_kv(f"funding:{position.symbol}", 0) or 0))
            last = max(last, position.opened_ms)
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
                self.store.set_kv(conn, f"funding:{book.value}:{symbol}", ts_ms)
                if book is Book.CARRY:
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

    def flatten(self, marks: Mapping[str, Decimal], reason: str) -> dict:
        """Serialize manual flatten with cycles without waiting for I/O.

        A busy cycle is halted immediately; the caller must retry flatten once
        it finishes. An already submitted order may still complete.
        """
        if not self._cycle_lock.acquire(blocking=False):
            self.engage_kill_switch(reason)
            return {"status": "cycle_in_progress", "outcomes": [], "venue_flat_confirmed": False}
        try:
            return self._flatten(marks, int(time.time() * 1000), reason)
        finally:
            self._cycle_lock.release()

    def _flatten(self, marks: Mapping[str, Decimal], now: int, reason: str) -> dict:
        from oq.types import Side

        self.engage_kill_switch(reason)
        outcomes = []
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
                outcomes.append(
                    {
                        "symbol": position.symbol,
                        "book": position.book.value,
                        "status": "rejected",
                        "errors": list(decision.reasons),
                    }
                )
                continue
            result = self.oms.execute(
                decision,
                intent,
                self.broker,
                BrokerContext(
                    now_ms=now,
                    price=marks.get(position.symbol, position.avg_px),
                    prices=dict(marks),
                ),
            )
            remaining = self.portfolio.get(position.symbol, position.book)
            outcomes.append(
                {
                    "symbol": position.symbol,
                    "book": position.book.value,
                    "status": "uncertain" if result.errors else "locally_recorded",
                    "errors": result.errors,
                    "remaining_local_qty": str(remaining.qty) if remaining else "0",
                    "order_ids": [order.client_id for order in result.orders],
                }
            )
        return {"outcomes": outcomes, "venue_flat_confirmed": False}

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
