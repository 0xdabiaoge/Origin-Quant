from __future__ import annotations

import threading
from types import SimpleNamespace

from oq.app.orchestrator import Orchestrator
from oq.data.models import FundingPoint, Quote
from oq.data.store import ParquetStore
from oq.domain.intent import Intent
from oq.domain.order import Order
from oq.domain.portfolio import Portfolio
from oq.execution.broker import BrokerContext
from oq.execution.journal import NullJournal
from oq.execution.oms import OrderManager
from oq.execution.paper import PaperBroker
from oq.risk.context import RiskContext
from oq.risk.engine import RiskEngine
from oq.types import Book, D, InstType, OrderStatus, OrderType, Side


def _context(portfolio, **extra):
    values = dict(
        now_ms=1_700_000_000_000,
        portfolio=portfolio,
        marks={"BTC-USDT-SWAP": D("60000")},
        data_ok={"BTC-USDT-SWAP": True},
        atr={"BTC-USDT-SWAP": D("120")},
        funding={"BTC-USDT-SWAP": D("0.0002")},
    )
    values.update(extra)
    return RiskContext(**values)


def test_controls_survive_restart_and_resume_does_not_clear_kill(config, db, store):
    orch = Orchestrator(config, db, store, None)
    orch.halt("operator pause")
    orch.engage_kill_switch("operator kill")
    restored = Orchestrator(config, db, store, None)
    assert restored.halted and restored._kill
    restored.resume()
    restored = Orchestrator(config, db, store, None)
    assert not restored.halted and restored._kill
    restored.reset_kill_switch()
    assert not Orchestrator(config, db, store, None)._kill


def test_live_requires_a_valid_fresh_quote(config, instruments, portfolio):
    config = config.model_copy(update={"app": config.app.model_copy(update={"mode": "live"})})
    engine = RiskEngine(config, instruments)
    ctx = _context(portfolio)
    assert not engine._gate_spread(
        None, [type("Plan", (), {"symbol": "BTC-USDT-SWAP"})()], ctx
    ).passed
    plan = type("Plan", (), {"symbol": "BTC-USDT-SWAP"})()
    stale = Quote(
        symbol=plan.symbol,
        ts_ms=ctx.now_ms - 1_000_000,
        bid=D("59999"),
        ask=D("60001"),
        last=D("60000"),
    )
    assert not engine._gate_spread(
        None, [plan], _context(portfolio, quotes={plan.symbol: stale})
    ).passed
    valid = Quote(
        symbol=plan.symbol, ts_ms=ctx.now_ms, bid=D("59999"), ask=D("60001"), last=D("60000")
    )
    assert engine._gate_spread(
        None, [plan], _context(portfolio, quotes={plan.symbol: valid})
    ).passed


def test_exit_is_allowed_when_equity_is_negative(config, instruments):
    portfolio = Portfolio(cash=D("-100"))
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
    )
    intent = Intent.close_position(
        strategy_id="pm", symbol="BTC-USDT-SWAP", ts_ms=2, book=Book.DIRECTIONAL, side=Side.SELL
    )
    result = RiskEngine(config, instruments).evaluate(intent, _context(portfolio))
    assert result.approved, result.reasons


def test_ambiguous_first_leg_never_submits_second(config, instruments):
    portfolio = Portfolio(cash=D("10000"))
    journal = NullJournal()
    manager = OrderManager(config, instruments, portfolio, journal)
    intent = Intent.open_pair(
        strategy_id="carry_basis",
        ts_ms=1_700_000_000_000,
        spot_symbol="BTC-USDT",
        perp_symbol="BTC-USDT-SWAP",
        notional_pct=D("0.05"),
    )
    marks = {"BTC-USDT": D("60000"), "BTC-USDT-SWAP": D("60000")}
    decision = RiskEngine(config, instruments).evaluate(
        intent, _context(portfolio, marks=marks, data_ok=dict.fromkeys(marks, True))
    )
    assert decision.approved, decision.reasons

    class TimeoutBroker(PaperBroker):
        calls = 0

        def place(self, order, instrument, ctx):
            self.calls += 1
            raise TimeoutError("response lost")

    broker = TimeoutBroker()
    result = manager.execute(decision, intent, broker, BrokerContext(now_ms=1, price=D("60000")))
    assert broker.calls == 1
    assert result.orders[0].status is OrderStatus.UNKNOWN
    assert result.errors


def test_stop_price_is_sent_to_broker_for_directional_entry(config, instruments, portfolio):
    intent = Intent.open_long(
        strategy_id="trend_donchian",
        symbol="BTC-USDT-SWAP",
        ts_ms=1_700_000_000_000,
        stop_px=D("59640"),
        ref_px=D("60000"),
    )
    decision = RiskEngine(config, instruments).evaluate(intent, _context(portfolio))
    assert decision.approved, decision.reasons
    manager = OrderManager(config, instruments, portfolio, NullJournal())
    order = manager._build_order(
        0, decision.legs[0], intent, decision, BrokerContext(now_ms=1, price=D("60000"))
    )
    assert order.tags["stop_px"] == "59640"


def test_funding_uses_position_start_and_separate_book_cursors(config, db, store, tmp_path):
    config = config.model_copy(
        update={"data": config.data.model_copy(update={"parquet_dir": tmp_path / "pq"})}
    )
    orch = Orchestrator(config, db, store, None)
    now = 1_800_000_000_000
    opened = now - 1000
    for book in (Book.DIRECTIONAL, Book.CARRY):
        orch.portfolio.apply_fill(
            symbol="BTC-USDT-SWAP",
            book=book,
            inst_type=InstType.SWAP,
            side=Side.SELL,
            qty=D("0.01"),
            px=D("60000"),
            fee=D("0"),
            ts_ms=opened,
        )
    ParquetStore(config.data.parquet_dir).write_funding(
        "BTC-USDT-SWAP",
        [
            FundingPoint(symbol="BTC-USDT-SWAP", ts_ms=opened - 1000, rate=D("0.1")),
            FundingPoint(symbol="BTC-USDT-SWAP", ts_ms=opened + 100, rate=D("0.001")),
        ],
    )
    orch._apply_due_funding(now, {"BTC-USDT-SWAP": D("60000")})
    assert orch.portfolio.funding_net == D("1.2")
    assert store.get_kv("funding:carry:BTC-USDT-SWAP") == opened + 100
    assert store.get_kv("funding:directional:BTC-USDT-SWAP") == opened + 100
    orch._apply_due_funding(now, {"BTC-USDT-SWAP": D("60000")})
    assert orch.portfolio.funding_net == D("1.2")


def test_live_pending_orders_block_new_risk(config, db, store):
    config = config.model_copy(update={"app": config.app.model_copy(update={"mode": "live"})})
    orch = Orchestrator(config, db, store, None)
    orch.portfolio.cash = D("10000")
    now = 1_700_000_000_000
    symbol = "BTC-USDT-SWAP"
    quote = Quote(symbol=symbol, ts_ms=now, bid=D("59999"), ask=D("60001"), last=D("60000"))
    orch._build_views = lambda _: (
        {symbol: SimpleNamespace(price=D("60000"), last_funding=D("0"))},
        {symbol: quote},
        set(),
    )
    orch._atr_map = lambda _: {symbol: D("120")}
    orch._apply_due_funding = lambda *_: None
    intent = Intent.open_long(
        strategy_id="trend_donchian",
        symbol=symbol,
        ts_ms=now,
        stop_px=D("59640"),
        ref_px=D("60000"),
    )
    orch.strategies = [
        SimpleNamespace(id="trend_donchian", book=Book.DIRECTIONAL, generate=lambda _: [intent])
    ]
    orch.position_manager = SimpleNamespace(id="pm", book=Book.DIRECTIONAL, generate=lambda _: [])
    pending = Order(
        client_id="oq-unresolved",
        symbol=symbol,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=D("0.01"),
        status=OrderStatus.UNKNOWN,
        created_ms=now,
        updated_ms=now,
    )
    with db.tx() as conn:
        store.save_order(conn, pending)
    report = orch.run_once(now)
    assert report.approved == 0 and report.rejected == 1
    assert store.recent_decisions()[0]["gates"][-1]["code"] == "trading_halt"
    assert len(store.open_orders()) == 1


def test_paper_ai_runs_after_exit_and_through_ordinary_risk(config, db, store, monkeypatch):
    from oq.app import ai_api

    orch = Orchestrator(config, db, store, None)
    now = 1_700_000_000_000
    symbol = "BTC-USDT-SWAP"
    orch.portfolio.apply_fill(
        symbol=symbol,
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
    )
    orch._build_views = lambda _: (
        {symbol: SimpleNamespace(price=D("60000"), last_funding=D("0"))},
        {},
        set(),
    )
    orch._atr_map = lambda _: {symbol: D("120")}
    orch._apply_due_funding = lambda *_: None
    orch.strategies = []
    exit_intent = Intent.close_position(
        strategy_id="pm", symbol=symbol, ts_ms=now, book=Book.DIRECTIONAL, side=Side.SELL
    )
    orch.position_manager = SimpleNamespace(
        id="pm", book=Book.DIRECTIONAL, generate=lambda _: [exit_intent]
    )
    ai_intent = Intent.open_long(
        strategy_id="ai", symbol=symbol, ts_ms=now, stop_px=D("59640"), ref_px=D("60000")
    )

    def collect(ctx, views, *, now_ms):
        assert ctx.portfolio.get(symbol, Book.DIRECTIONAL) is None
        return ai_intent

    monkeypatch.setattr(ai_api, "collect_paper_intent", collect)
    with db.tx() as conn:
        store.set_kv(conn, ai_api.CONFIG_KEY, {"enabled": True, "auto_paper_enabled": True})
    report = orch.run_once(now)
    assert report.intents == 2
    decisions = store.recent_decisions()
    assert {item["strategy_id"] for item in decisions} == {"pm", "ai"}
    assert all(item["gates"] for item in decisions)
    assert (
        next(item for item in decisions if item["strategy_id"] == "ai")["gates"][0]["code"]
        == "same_cycle_exit"
    )


def test_position_exits_execute_before_entry_generation_and_block_same_cycle_reentry(
    config, db, store
):
    orch = Orchestrator(config, db, store, None)
    now = 1_700_000_000_000
    symbol = "BTC-USDT-SWAP"
    orch.portfolio.apply_fill(
        symbol=symbol,
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
    )
    orch._build_views = lambda _: (
        {symbol: SimpleNamespace(price=D("60000"), last_funding=D("0"))},
        {},
        set(),
    )
    orch._atr_map = lambda _: {symbol: D("120")}
    orch._apply_due_funding = lambda *_: None
    exit_intent = Intent.close_position(
        strategy_id="pm", symbol=symbol, ts_ms=now, book=Book.DIRECTIONAL, side=Side.SELL
    )
    orch.position_manager = SimpleNamespace(
        id="pm", book=Book.DIRECTIONAL, generate=lambda _: [exit_intent]
    )
    entry = Intent.open_long(
        strategy_id="trend", symbol=symbol, ts_ms=now, stop_px=D("59640"), ref_px=D("60000")
    )
    seen = []

    def generate(ctx):
        seen.append(ctx.portfolio.get(symbol, Book.DIRECTIONAL))
        return [entry]

    orch.strategies = [SimpleNamespace(id="trend", book=Book.DIRECTIONAL, generate=generate)]
    report = orch.run_once(now)
    assert seen == [None], "the protective exit must complete before strategy generation"
    assert report.intents == 2 and report.rejected == 1
    decision = next(item for item in store.recent_decisions() if item["strategy_id"] == "trend")
    assert decision["gates"][0]["code"] == "same_cycle_exit"
    assert orch.portfolio.get(symbol, Book.DIRECTIONAL) is None


def test_position_manager_error_halts_new_entries(config, db, store):
    orch = Orchestrator(config, db, store, None)
    now = 1_700_000_000_000
    symbol = "BTC-USDT-SWAP"
    orch._build_views = lambda _: (
        {symbol: SimpleNamespace(price=D("60000"), last_funding=D("0"))},
        {},
        set(),
    )
    orch._atr_map = lambda _: {symbol: D("120")}
    orch._apply_due_funding = lambda *_: None

    def fail(_):
        raise ValueError("cannot evaluate exits")

    orch.position_manager = SimpleNamespace(id="pm", book=Book.DIRECTIONAL, generate=fail)
    entry = Intent.open_long(
        strategy_id="trend", symbol=symbol, ts_ms=now, stop_px=D("59640"), ref_px=D("60000")
    )
    orch.strategies = [
        SimpleNamespace(id="trend", book=Book.DIRECTIONAL, generate=lambda _: [entry])
    ]
    report = orch.run_once(now)
    assert report.rejected == 1 and report.fills == 0
    assert store.recent_decisions()[0]["gates"][-1]["code"] == "trading_halt"


def test_control_change_after_evaluation_is_recorded_as_rejected(config, db, store, monkeypatch):
    from oq.domain.intent import GateResult, RiskDecision

    orch = Orchestrator(config, db, store, None)
    now = 1_700_000_000_000
    symbol = "BTC-USDT-SWAP"
    orch._build_views = lambda _: (
        {symbol: SimpleNamespace(price=D("60000"), last_funding=D("0"))},
        {},
        set(),
    )
    orch._atr_map = lambda _: {symbol: D("120")}
    orch._apply_due_funding = lambda *_: None
    orch.position_manager = SimpleNamespace(id="pm", book=Book.DIRECTIONAL, generate=lambda _: [])
    entry = Intent.open_long(
        strategy_id="trend", symbol=symbol, ts_ms=now, stop_px=D("59640"), ref_px=D("60000")
    )
    orch.strategies = [
        SimpleNamespace(id="trend", book=Book.DIRECTIONAL, generate=lambda _: [entry])
    ]

    def approve_and_kill(intent, ctx):
        orch.engage_kill_switch("test concurrent control")
        return RiskDecision(intent.id, True, (), (GateResult("risk", True),), action=intent.action)

    monkeypatch.setattr(orch.risk, "evaluate", approve_and_kill)
    report = orch.run_once(now)
    assert report.rejected == 1 and report.approved == report.fills == 0
    decision = store.recent_decisions()[0]
    assert not decision["approved"]
    assert decision["gates"][-1]["code"] == "trading_control_changed"


def test_concurrent_cycle_is_rejected_without_touching_the_first(config, db, store):
    orch = Orchestrator(config, db, store, None)
    entered = threading.Event()
    release = threading.Event()

    def build(_):
        entered.set()
        assert release.wait(3)
        return {}, {}, set()

    orch._build_views = build
    results = []
    worker = threading.Thread(target=lambda: results.append(orch.run_once(1_700_000_000_000)))
    worker.start()
    try:
        assert entered.wait(3)
        assert orch.run_once(1_700_000_000_001).skipped == "cycle_in_progress"
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert results[0].skipped == "no_data"


def test_manual_flatten_during_cycle_sets_persistent_kill_and_requires_retry(config, db, store):
    orch = Orchestrator(config, db, store, None)
    assert orch._cycle_lock.acquire(blocking=False)
    try:
        outcome = orch.flatten({}, "manual emergency")
        assert outcome["status"] == "cycle_in_progress"
        assert outcome["venue_flat_confirmed"] is False
        assert orch._kill
        assert Orchestrator(config, db, store, None)._kill
    finally:
        orch._cycle_lock.release()
