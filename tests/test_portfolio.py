from __future__ import annotations

from oq.domain.portfolio import Portfolio
from oq.domain.position import Position
from oq.types import Book, D, InstType, Side


def _fill(pf: Portfolio, **kw):
    params = dict(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("60000"),
        fee=D("0.3"),
        ts_ms=1_700_000_000_000,
    )
    params.update(kw)
    return pf.apply_fill(**params)


def test_swap_open_does_not_consume_cash_only_fee(portfolio: Portfolio):
    _fill(portfolio, fee=D("0.3"))
    assert portfolio.cash == D("9999.7")


def test_swap_close_realises_pnl(portfolio: Portfolio):
    _fill(portfolio, fee=D("0"))
    _fill(portfolio, side=Side.SELL, px=D("61000"), fee=D("0"))
    assert portfolio.cash == D("10010")
    assert portfolio.positions == {}


def test_spot_buy_uses_cash_and_equity_tracks_mark(portfolio: Portfolio):
    _fill(
        portfolio,
        symbol="BTC-USDT",
        inst_type=InstType.SPOT,
        qty=D("0.1"),
        px=D("60000"),
        fee=D("6"),
    )
    assert portfolio.cash == D("3994")
    assert portfolio.equity({"BTC-USDT": D("60000")}) == D("9994")
    assert portfolio.equity({"BTC-USDT": D("66000")}) == D("10594")


def test_spot_sell_adds_cash(portfolio: Portfolio):
    _fill(
        portfolio,
        symbol="BTC-USDT",
        inst_type=InstType.SPOT,
        qty=D("0.1"),
        px=D("60000"),
        fee=D("0"),
    )
    _fill(
        portfolio,
        symbol="BTC-USDT",
        inst_type=InstType.SPOT,
        side=Side.SELL,
        qty=D("0.1"),
        px=D("61000"),
        fee=D("0"),
    )
    assert portfolio.cash == D("10100")  # 4000 left + 6100 proceeds
    assert portfolio.positions == {}


def test_fee_is_charged_even_on_the_closing_fill(portfolio: Portfolio):
    _fill(portfolio, fee=D("0"))
    position, _ = _fill(portfolio, side=Side.SELL, px=D("60000"), fee=D("1"))
    assert position.fees_paid == D("1")
    assert portfolio.cash == D("9999")


def test_short_position_pnl_sign(portfolio: Portfolio):
    _fill(portfolio, side=Side.SELL, qty=D("0.01"), px=D("60000"), fee=D("0"))
    _fill(portfolio, side=Side.BUY, qty=D("0.01"), px=D("59000"), fee=D("0"))
    assert portfolio.cash == D("10010")


def test_averaging_up_recomputes_entry(portfolio: Portfolio):
    _fill(portfolio, qty=D("0.01"), px=D("60000"), fee=D("0"))
    _fill(portfolio, qty=D("0.01"), px=D("62000"), fee=D("0"))
    position = portfolio.get("BTC-USDT-SWAP", Book.DIRECTIONAL)
    assert position is not None
    assert position.avg_px == D("61000")
    assert position.qty == D("0.02")


def test_partial_close_keeps_the_rest_open(portfolio: Portfolio):
    _fill(portfolio, qty=D("0.02"), px=D("60000"), fee=D("0"))
    _fill(portfolio, side=Side.SELL, qty=D("0.01"), px=D("61000"), fee=D("0"))
    position = portfolio.get("BTC-USDT-SWAP", Book.DIRECTIONAL)
    assert position is not None and position.qty == D("0.01")
    assert portfolio.cash == D("10010")


def test_funding_received_and_paid(portfolio: Portfolio):
    _fill(portfolio, qty=D("0.01"), px=D("60000"), fee=D("0"))
    portfolio.apply_funding("BTC-USDT-SWAP", Book.DIRECTIONAL, D("-0.5"), 1_700_000_000_000)
    assert portfolio.cash == D("9999.5")
    assert portfolio.funding_net == D("-0.5")


def test_close_all_is_accounting_only_and_balances(portfolio: Portfolio):
    _fill(portfolio, qty=D("0.01"), px=D("60000"), fee=D("0"))
    closed = portfolio.close_all({"BTC-USDT-SWAP": D("61000")})
    assert len(closed) == 1
    assert portfolio.positions == {}
    assert portfolio.cash == D("10010")


def test_drawdown_uses_peak_not_start(portfolio: Portfolio):
    marks = {"BTC-USDT-SWAP": D("60000")}
    portfolio.mark(marks, 1_700_000_000_000)
    _fill(portfolio, qty=D("1"), px=D("60000"), fee=D("0"))
    portfolio.mark({"BTC-USDT-SWAP": D("66000")}, 1_700_000_000_000)
    dd = portfolio.drawdown({"BTC-USDT-SWAP": D("62700")})
    assert dd > D("0.20")


def test_day_and_week_rollover_resets_baseline(portfolio: Portfolio):
    marks = {"BTC-USDT-SWAP": D("60000")}
    day0 = 1_700_000_000_000
    portfolio.mark(marks, day0)
    _fill(portfolio, qty=D("0.01"), px=D("60000"), fee=D("0"))
    portfolio.mark({"BTC-USDT-SWAP": D("61000")}, day0)
    assert portfolio.day_pnl({"BTC-USDT-SWAP": D("61000")}) == D("0.001")
    next_day = day0 + 86_400_000
    portfolio.mark({"BTC-USDT-SWAP": D("61000")}, next_day)
    assert portfolio.day_pnl({"BTC-USDT-SWAP": D("61000")}) == D("0")


def test_position_move_stop_only_tightens():
    position = Position(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        strategy_id="test",
        qty=D("1"),
        avg_px=D("100"),
        opened_ms=1,
        updated_ms=1,
        stop_px=D("90"),
    )
    assert position.move_stop(D("95")) is True
    assert position.stop_px == D("95")
    assert position.move_stop(D("92")) is False
    assert position.stop_px == D("95")


def test_short_move_stop_only_tightens_downwards():
    position = Position(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        strategy_id="test",
        qty=D("-1"),
        avg_px=D("100"),
        opened_ms=1,
        updated_ms=1,
        stop_px=D("110"),
    )
    assert position.move_stop(D("105")) is True
    assert position.move_stop(D("108")) is False


def test_open_risk_is_distance_times_quantity(portfolio: Portfolio):
    _fill(portfolio, qty=D("2"), px=D("100"), fee=D("0"), stop_px=D("95"))
    assert portfolio.open_risk() == D("10")


def test_snapshot_serialises_decimals_as_strings(portfolio: Portfolio):
    _fill(portfolio, qty=D("0.01"), px=D("60000"), fee=D("0.3"))
    snapshot = portfolio.snapshot({"BTC-USDT-SWAP": D("60000")})
    assert isinstance(snapshot["equity"], str)
    assert isinstance(snapshot["positions"], list)
