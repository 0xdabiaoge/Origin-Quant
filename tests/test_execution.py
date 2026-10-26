from __future__ import annotations

from oq.domain.intent import Intent
from oq.domain.portfolio import Portfolio
from oq.execution.broker import BrokerContext, BrokerResult
from oq.execution.journal import NullJournal
from oq.execution.oms import OrderManager
from oq.execution.paper import PaperBroker
from oq.risk import RiskContext, RiskEngine
from oq.types import Book, D, InstType, Side


def _setup(config, instruments, portfolio):
    journal = NullJournal()
    oms = OrderManager(config, instruments, portfolio, journal)
    return oms, journal, RiskEngine(config, instruments)


def _ctx(portfolio, marks, **kw):
    base = dict(
        now_ms=1_700_000_000_000,
        portfolio=portfolio,
        marks=marks,
        data_ok=dict.fromkeys(marks, True),
        atr={"BTC-USDT-SWAP": D("120"), "ETH-USDT-SWAP": D("15")},
        funding={"BTC-USDT-SWAP": D("0.0002")},
    )
    base.update(kw)
    return RiskContext(**base)


def test_one_intent_produces_exactly_one_fill_per_leg(config, instruments):
    """Regression: executing once per leg doubled every hedge and inverted the book."""
    portfolio = Portfolio(cash=D("10000"))
    oms, journal, engine = _setup(config, instruments, portfolio)
    intent = Intent.open_pair(
        strategy_id="carry_basis",
        ts_ms=1_700_000_000_000,
        spot_symbol="BTC-USDT",
        perp_symbol="BTC-USDT-SWAP",
        notional_pct=D("0.05"),
        reason="test",
    )
    marks = {"BTC-USDT": D("60000"), "BTC-USDT-SWAP": D("60000")}
    decision = engine.evaluate(intent, _ctx(portfolio, marks))
    assert decision.approved, decision.reasons

    ctx = BrokerContext(now_ms=1_700_000_000_000, price=D("60000"), prices=marks)
    result = oms.execute(decision, intent, PaperBroker(), ctx)

    assert len(result.fills) == 2
    assert {f.symbol for f in result.fills} == {"BTC-USDT", "BTC-USDT-SWAP"}
    assert len(journal.fills) == 2
    spot = portfolio.get("BTC-USDT", Book.CARRY)
    perp = portfolio.get("BTC-USDT-SWAP", Book.CARRY)
    assert spot is not None and spot.qty > 0
    assert perp is not None and perp.qty < 0
    # delta neutral to within one lot
    assert abs(spot.qty + perp.qty) <= D("0.0002")


def test_close_cannot_flip_a_position(config, instruments):
    portfolio = Portfolio(cash=D("10000"))
    oms, _journal, engine = _setup(config, instruments, portfolio)
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
        stop_px=D("59000"),
    )
    marks = {"BTC-USDT-SWAP": D("60000")}
    intent = Intent.close_position(
        strategy_id="pm",
        symbol="BTC-USDT-SWAP",
        ts_ms=2,
        book=Book.DIRECTIONAL,
        side=Side.SELL,
        fraction=D("5"),
    )
    decision = engine.evaluate(intent, _ctx(portfolio, marks))
    assert decision.approved
    oms.execute(decision, intent, PaperBroker(), BrokerContext(now_ms=2, price=D("60000")))
    assert portfolio.get("BTC-USDT-SWAP", Book.DIRECTIONAL) is None
    assert portfolio.get("BTC-USDT-SWAP", Book.DIRECTIONAL) is None


def test_trade_is_recorded_only_on_a_full_close(config, instruments):
    portfolio = Portfolio(cash=D("10000"))
    oms, journal, engine = _setup(config, instruments, portfolio)
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.1"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
        stop_px=D("59000"),
        strategy_id="trend_donchian",
        tp1_r=D("1.5"),
    )
    marks = {"BTC-USDT-SWAP": D("60000")}
    partial = Intent.close_position(
        strategy_id="pm",
        symbol="BTC-USDT-SWAP",
        ts_ms=2,
        book=Book.DIRECTIONAL,
        side=Side.SELL,
        fraction=D("0.33"),
        reason="tp1",
    )
    decision = engine.evaluate(partial, _ctx(portfolio, marks))
    oms.execute(decision, partial, PaperBroker(), BrokerContext(now_ms=2, price=D("60000")))
    assert journal.trades == []
    position = portfolio.get("BTC-USDT-SWAP", Book.DIRECTIONAL)
    assert position is not None and position.tp1_done is True

    final = Intent.close_position(
        strategy_id="pm",
        symbol="BTC-USDT-SWAP",
        ts_ms=3,
        book=Book.DIRECTIONAL,
        side=Side.SELL,
        reason="stop",
    )
    decision = engine.evaluate(final, _ctx(portfolio, marks))
    oms.execute(decision, final, PaperBroker(), BrokerContext(now_ms=3, price=D("59500")))
    assert len(journal.trades) == 1
    trade = journal.trades[0]
    assert trade.reason == "stop"
    assert trade.r_multiple is not None and trade.r_multiple < 0


def test_exit_sets_a_cooldown(config, instruments):
    portfolio = Portfolio(cash=D("10000"))
    oms, journal, engine = _setup(config, instruments, portfolio)
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
        stop_px=D("59000"),
    )
    intent = Intent.close_position(
        strategy_id="pm",
        symbol="BTC-USDT-SWAP",
        ts_ms=1_700_000_000_000,
        book=Book.DIRECTIONAL,
        side=Side.SELL,
        reason="stop 59000",
    )
    decision = engine.evaluate(intent, _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")}))
    oms.execute(
        decision, intent, PaperBroker(), BrokerContext(now_ms=1_700_000_000_000, price=D("59000"))
    )
    cooldowns = journal.cooldowns()
    assert ("BTC-USDT-SWAP", Book.DIRECTIONAL) in cooldowns
    assert cooldowns[("BTC-USDT-SWAP", Book.DIRECTIONAL)] > 1_700_000_000_000


def test_move_stop_is_applied_to_the_position(config, instruments):
    portfolio = Portfolio(cash=D("10000"))
    oms, _journal, engine = _setup(config, instruments, portfolio)
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
        stop_px=D("59000"),
    )
    intent = Intent.move_stop(
        strategy_id="pm",
        symbol="BTC-USDT-SWAP",
        ts_ms=2,
        book=Book.DIRECTIONAL,
        new_stop=D("60100"),
    )
    decision = engine.evaluate(intent, _ctx(portfolio, {"BTC-USDT-SWAP": D("60500")}))
    assert decision.approved, decision.reasons
    oms.execute(decision, intent, PaperBroker(), BrokerContext(now_ms=2, price=D("60500")))
    position = portfolio.get("BTC-USDT-SWAP", Book.DIRECTIONAL)
    assert position.stop_px == D("60100")


def test_unwind_closes_the_filled_leg_when_the_sibling_fails(config, instruments):
    portfolio = Portfolio(cash=D("10000"))
    oms, _journal, engine = _setup(config, instruments, portfolio)

    class RejectingBroker(PaperBroker):
        def place(self, order, instrument, ctx):
            if order.symbol == "BTC-USDT-SWAP":
                return BrokerResult(rejected="venue said no")
            return super().place(order, instrument, ctx)

    intent = Intent.open_pair(
        strategy_id="carry_basis",
        ts_ms=1_700_000_000_000,
        spot_symbol="BTC-USDT",
        perp_symbol="BTC-USDT-SWAP",
        notional_pct=D("0.05"),
    )
    marks = {"BTC-USDT": D("60000"), "BTC-USDT-SWAP": D("60000")}
    decision = engine.evaluate(intent, _ctx(portfolio, marks))
    assert decision.approved
    result = oms.execute(
        decision,
        intent,
        RejectingBroker(),
        BrokerContext(now_ms=1_700_000_000_000, price=D("60000"), prices=marks),
    )
    assert result.errors
    assert portfolio.get("BTC-USDT", Book.CARRY) is None
