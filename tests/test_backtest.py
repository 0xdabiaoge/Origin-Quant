from __future__ import annotations

from decimal import Decimal

import pytest

from oq.backtest import BacktestEngine, BacktestParams, load_synthetic, trading_symbols
from oq.strategy.registry import build_position_manager, build_strategies
from oq.types import ZERO, D


@pytest.fixture(scope="module")
def result(config):
    series = load_synthetic(
        config,
        symbols=trading_symbols(config),
        timeframe="15m",
        days=30,
        seed=5,
        start_ms=1_700_000_000_000,
    )
    engine = BacktestEngine(config, build_strategies(config), build_position_manager())
    return engine.run(series, BacktestParams(initial_cash=D("10000"), label="test-30d")), engine


def test_backtest_produces_trades(result):
    res, _engine = result
    assert res.decisions > 0
    assert res.equity
    assert res.metrics["start_equity"] == pytest.approx(10000.0, rel=1e-9)


def test_every_fill_respects_the_lot_grid(result, config):
    _res, engine = result
    instruments = config.instrument_map()
    for fill in engine.journal.fills:
        instrument = instruments[fill.symbol]
        qty = fill.qty
        assert qty > ZERO
        # lot alignment: qty / lot is an integer for spot, and for swaps the
        # implied contract count sits on the contract lot.
        if instrument.is_swap:
            contracts = instrument.contracts(qty)
            lot = instrument.contract_lot
            assert (contracts / lot) == (contracts / lot).to_integral_value(), (
                f"{fill.symbol} qty {qty} -> {contracts} contracts is off the {lot} grid"
            )
        else:
            assert (qty / instrument.lot_size) == (qty / instrument.lot_size).to_integral_value()


def test_no_position_is_left_in_dust(result):
    res, engine = result
    # Everything must be closed by the end-of-test flatten.
    assert engine.portfolio.positions == {}
    assert res.open_positions == 0


def test_r_multiple_sign_matches_pnl(result):
    res, _engine = result
    for trade in res.trades:
        if trade.r_multiple is None:
            continue
        if trade.pnl > ZERO:
            assert trade.r_multiple > 0, f"{trade.symbol} pnl {trade.pnl} but R {trade.r_multiple}"
        if trade.pnl < ZERO:
            assert trade.r_multiple < 0, f"{trade.symbol} pnl {trade.pnl} but R {trade.r_multiple}"


def test_realised_trade_pnl_reconciles_with_equity(result):
    res, _engine = result
    total = sum((t.pnl for t in res.trades), ZERO)
    equity_change = res.equity[-1].equity - res.initial_cash
    assert abs(total - equity_change) < Decimal("0.05"), (total, equity_change)


def test_backtest_is_deterministic(config):
    series = load_synthetic(
        config,
        symbols=trading_symbols(config),
        timeframe="15m",
        days=12,
        seed=9,
        start_ms=1_700_000_000_000,
    )
    runs = []
    for _ in range(2):
        engine = BacktestEngine(config, build_strategies(config), build_position_manager())
        res = engine.run(series, BacktestParams(initial_cash=D("10000"), label="det"))
        runs.append(
            (res.metrics["total_return"], len(res.trades), [str(p.equity) for p in res.equity[:50]])
        )

    assert runs[0] == runs[1]


def test_equity_never_becomes_negative_in_a_normal_run(result):
    res, _engine = result
    assert all(point.equity > ZERO for point in res.equity)


def test_fees_are_charged_on_both_sides(result):
    res, _engine = result
    assert res.metrics["fees"] > 0
    for trade in res.trades:
        assert trade.fees > ZERO


def test_decision_count_matches_risk_engine_calls(result):
    res, _engine = result
    assert res.decisions >= res.intents


def test_funding_only_applies_after_entry_to_each_book_at_a_causal_price(config):
    from oq.backtest.data import from_bars
    from oq.data.models import Bar, FundingPoint
    from oq.types import Book, InstType, Side

    symbol = "BTC-USDT-SWAP"
    bars = [
        Bar(symbol, "15m", timestamp, D(open_px), D("1000"), D("1"), D("999"), D("1"))
        for timestamp, open_px in ((1000, "100"), (2000, "200"), (3000, "300"))
    ]
    funding = [
        FundingPoint(symbol, 1500, D("0.5")),  # before either position existed
        FundingPoint(symbol, 2500, D("0.01")),
    ]
    series = {symbol: from_bars(symbol, bars, funding)}
    engine = BacktestEngine(config, [], build_position_manager())
    for book in (Book.CARRY, Book.DIRECTIONAL):
        engine.portfolio.apply_fill(
            symbol=symbol,
            book=book,
            inst_type=InstType.SWAP,
            side=Side.SELL,
            qty=D("1"),
            px=D("200"),
            fee=ZERO,
            ts_ms=1700,
        )

    engine._apply_funding(series, {symbol: 2}, 3000, {symbol: D("999")})
    assert engine.portfolio.funding_net == D("4")  # 2 books * 1 * 200 * 0.01
    assert engine.portfolio.get(symbol, Book.CARRY).funding_net == D("2")
    assert engine.portfolio.get(symbol, Book.DIRECTIONAL).funding_net == D("2")

    engine._apply_funding(series, {symbol: 2}, 3000, {symbol: D("999")})
    assert engine.portfolio.funding_net == D("4")
