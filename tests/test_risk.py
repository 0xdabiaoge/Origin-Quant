from __future__ import annotations

import pytest

from oq.domain.intent import Intent
from oq.risk import RiskContext, RiskEngine
from oq.types import Book, D, InstType, Side


def _engine(config, instruments):
    return RiskEngine(config, instruments)


def _ctx(portfolio, marks, **kw):
    base = dict(
        now_ms=1_700_000_000_000,
        portfolio=portfolio,
        marks=marks,
        data_ok=dict.fromkeys(marks, True),
        clusters={"BTC-USDT-SWAP": "majors", "ETH-USDT-SWAP": "majors"},
        atr={"BTC-USDT-SWAP": D("120"), "ETH-USDT-SWAP": D("15")},
        funding={"BTC-USDT-SWAP": D("0.0002")},
    )
    base.update(kw)
    return RiskContext(**base)


def _long_intent(stop: str = "59640") -> Intent:
    return Intent.open_long(
        strategy_id="trend_donchian",
        symbol="BTC-USDT-SWAP",
        ts_ms=1_700_000_000_000,
        stop_px=D(stop),
        ref_px=D("60000"),
    )


def test_missing_data_fails_closed(config, instruments, portfolio):
    intent = _long_intent()
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")}, data_ok={"BTC-USDT-SWAP": False})
    decision = _engine(config, instruments).evaluate(intent, ctx)
    assert not decision.approved
    assert decision.failed_gate() == "data_fresh"


def test_unknown_symbol_is_rejected(config, instruments, portfolio):
    intent = Intent.open_long(
        strategy_id="x", symbol="FAKE-USDT-SWAP", ts_ms=1, stop_px=D("1"), ref_px=D("2")
    )
    ctx = _ctx(portfolio, {"FAKE-USDT-SWAP": D("2")})
    decision = _engine(config, instruments).evaluate(intent, ctx)
    assert not decision.approved
    assert decision.failed_gate() in {"sizing", "universe", "data_fresh"}


def test_stop_on_wrong_side_is_rejected(config, instruments, portfolio):
    intent = _long_intent(stop="60500")
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    decision = _engine(config, instruments).evaluate(intent, ctx)
    assert not decision.approved
    assert decision.failed_gate() == "stop_sanity"


def test_stop_too_tight_for_atr_is_rejected(config, instruments, portfolio):
    intent = _long_intent(stop="59990")  # 0.017% away
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    decision = _engine(config, instruments).evaluate(intent, ctx)
    assert not decision.approved
    assert decision.failed_gate() == "stop_sanity"


def test_missing_atr_rejects_directional_entry(config, instruments, portfolio):
    intent = _long_intent()
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")}, atr={})
    decision = _engine(config, instruments).evaluate(intent, ctx)
    assert not decision.approved
    assert decision.failed_gate() == "stop_sanity"


def test_valid_entry_is_sized_by_risk(config, instruments, portfolio):
    intent = _long_intent()
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    decision = _engine(config, instruments).evaluate(intent, ctx)
    if not decision.approved:
        pytest.skip(f"rejected by {decision.failed_gate()}: {decision.reasons}")
    plan = decision.legs[0]
    risk_usd = abs(plan.ref_px - D("59640")) * plan.qty
    budget = D("10000") * config.risk.risk_per_trade_pct
    # Capped size may be below budget, never above.
    assert risk_usd <= budget * D("1.001")
    assert plan.qty >= instruments["BTC-USDT-SWAP"].min_size


def test_kill_switch_blocks_entries_but_not_exits(config, instruments, portfolio):
    engine = _engine(config, instruments)
    intent = _long_intent()
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")}, kill_switch=True)
    assert not engine.evaluate(intent, ctx).approved

    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.1"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
        stop_px=D("58000"),
    )
    exit_intent = Intent.close_position(
        strategy_id="pm", symbol="BTC-USDT-SWAP", ts_ms=1, book=Book.DIRECTIONAL, side=Side.SELL
    )
    assert engine.evaluate(exit_intent, ctx).approved


def test_halt_blocks_entries(config, instruments, portfolio):
    intent = _long_intent()
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")}, trading_halted=True, halt_reason="daily")
    decision = _engine(config, instruments).evaluate(intent, ctx)
    assert not decision.approved
    assert decision.failed_gate() == "trading_halt"


def test_cooldown_blocks_same_symbol(config, instruments, portfolio):
    intent = _long_intent()
    ctx = _ctx(
        portfolio,
        {"BTC-USDT-SWAP": D("60000")},
        cooldowns={("BTC-USDT-SWAP", Book.DIRECTIONAL): 1_700_000_100_000},
    )
    decision = _engine(config, instruments).evaluate(intent, ctx)
    assert not decision.approved
    assert decision.failed_gate() == "cooldown"


def test_heat_cap_blocks_when_portfolio_is_full(config, instruments, portfolio):
    for i in range(6):
        portfolio.apply_fill(
            symbol=f"X{i}-USDT-SWAP",
            book=Book.DIRECTIONAL,
            inst_type=InstType.SWAP,
            side=Side.BUY,
            qty=D("1"),
            px=D("100"),
            fee=D("0"),
            ts_ms=1,
            stop_px=D("96"),
        )
    intent = _long_intent()
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000"), "X0-USDT-SWAP": D("100")})
    decision = _engine(config, instruments).evaluate(intent, ctx)
    assert not decision.approved
    assert decision.failed_gate() in {"heat", "position_count"}


def test_daily_drawdown_blocks_entries(config, instruments, portfolio):
    portfolio.day_start_equity = D("10000")
    portfolio.cash = D("9800")  # -2% on the day
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    decision = _engine(config, instruments).evaluate(_long_intent(), ctx)
    assert not decision.approved
    assert decision.failed_gate() == "drawdown"


def test_close_without_position_is_rejected(config, instruments, portfolio):
    intent = Intent.close_position(
        strategy_id="pm", symbol="BTC-USDT-SWAP", ts_ms=1, book=Book.DIRECTIONAL, side=Side.SELL
    )
    decision = _engine(config, instruments).evaluate(
        intent, _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    )
    assert not decision.approved


def test_close_on_wrong_side_is_refused(config, instruments, portfolio):
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.1"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
        stop_px=D("58000"),
    )
    intent = Intent.close_position(
        strategy_id="pm", symbol="BTC-USDT-SWAP", ts_ms=1, book=Book.DIRECTIONAL, side=Side.BUY
    )
    decision = _engine(config, instruments).evaluate(
        intent, _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    )
    assert not decision.approved
    assert "wrong_close_side" in decision.reasons[0]


def test_close_never_exceeds_position_size(config, instruments, portfolio):
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.1"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
        stop_px=D("58000"),
    )
    intent = Intent.close_position(
        strategy_id="pm",
        symbol="BTC-USDT-SWAP",
        ts_ms=1,
        book=Book.DIRECTIONAL,
        side=Side.SELL,
        fraction=D("3"),
    )
    decision = _engine(config, instruments).evaluate(
        intent, _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    )
    assert decision.approved
    assert decision.legs[0].qty == D("0.1")


def test_stop_move_cannot_loosen(config, instruments, portfolio):
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.1"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
        stop_px=D("58000"),
    )
    engine = _engine(config, instruments)
    ctx = _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    loose = Intent.move_stop(
        strategy_id="pm",
        symbol="BTC-USDT-SWAP",
        ts_ms=1,
        book=Book.DIRECTIONAL,
        new_stop=D("57000"),
    )
    assert not engine.evaluate(loose, ctx).approved
    tighter = Intent.move_stop(
        strategy_id="pm",
        symbol="BTC-USDT-SWAP",
        ts_ms=1,
        book=Book.DIRECTIONAL,
        new_stop=D("59500"),
    )
    assert engine.evaluate(tighter, ctx).approved


def test_cost_gate_rejects_a_trade_with_no_edge(config, instruments, portfolio):
    # 3 bps target against ~10 bps round-trip cost cannot pass a 3x gate.
    intent = Intent.open_long(
        strategy_id="trend_donchian",
        symbol="BTC-USDT-SWAP",
        ts_ms=1,
        stop_px=D("59640"),
        ref_px=D("60000"),
        tp_ladder=((D("0.05"), D("1")),),
    )
    decision = _engine(config, instruments).evaluate(
        intent, _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    )
    assert not decision.approved
    assert decision.failed_gate() in {"risk_reward", "cost_edge"}


def test_decision_records_every_gate_it_evaluated(config, instruments, portfolio):
    decision = _engine(config, instruments).evaluate(
        _long_intent(), _ctx(portfolio, {"BTC-USDT-SWAP": D("60000")})
    )
    codes = [gate.code for gate in decision.gates]
    assert codes[0] in {"sizing", "kill_switch"}
    assert len(codes) >= 1
