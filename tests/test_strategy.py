from __future__ import annotations

import numpy as np

from oq.data.features import atr, ema, rolling_max_prev, rsi
from oq.strategy.base import SymbolView
from oq.strategy.registry import build_position_manager, build_strategies
from oq.types import D


def test_ema_is_causal(series):
    close = series["BTC-USDT-SWAP"].close
    full = ema(close, 21)
    # Truncating the future must not change any past value.
    for cut in (400, 800):
        partial = ema(close[:cut], 21)
        np.testing.assert_allclose(partial, full[:cut], rtol=0, atol=1e-9)


def test_atr_is_causal(series):
    s = series["BTC-USDT-SWAP"]
    full = atr(s.high, s.low, s.close, 14)
    partial = atr(s.high[:500], s.low[:500], s.close[:500], 14)
    np.testing.assert_allclose(partial, full[:500], rtol=0, atol=1e-9)


def test_donchian_channel_excludes_the_current_bar(series):
    s = series["BTC-USDT-SWAP"]
    lookback = 48
    upper = rolling_max_prev(s.high, lookback)
    i = 600
    assert upper[i] == s.high[i - lookback : i].max()
    assert upper[i] <= s.high[: i + 1].max()


def test_rsi_bounds(series):
    close = series["BTC-USDT-SWAP"].close
    values = rsi(close, 14)
    finite = values[np.isfinite(values)]
    assert finite.size > 0
    assert finite.min() >= 0 and finite.max() <= 100


def test_strategies_are_deterministic(config, series):
    from oq.domain.portfolio import Portfolio
    from oq.strategy.base import StrategyContext

    strategies = build_strategies(config)
    views = {symbol: series[symbol].view(series[symbol].size - 1) for symbol in series}
    ctx = StrategyContext(
        now_ms=views["BTC-USDT-SWAP"].ts_ms,
        equity=D("10000"),
        portfolio=Portfolio(cash=D("10000")),
        views=views,
        params={},
        config_hash=config.hash,
    )
    first = [i.id for strategy in strategies for i in strategy.generate(ctx)]
    second = [i.id for strategy in strategies for i in strategy.generate(ctx)]
    assert first == second


def test_intent_id_is_stable_across_runs(config):
    from oq.domain.intent import Intent

    def build():
        return Intent.open_long(
            strategy_id="trend_donchian",
            symbol="BTC-USDT-SWAP",
            ts_ms=1_700_000_000_000,
            stop_px=D("59640"),
            ref_px=D("60000"),
        )

    assert build().id == build().id


def test_position_manager_closes_a_position_that_breaches_its_stop(series):
    from oq.domain.portfolio import Portfolio
    from oq.strategy.base import StrategyContext
    from oq.types import Book, InstType, Side

    portfolio = Portfolio(cash=D("10000"))
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
        time_stop_bars=1000,
    )
    n = 60
    position = portfolio.get("BTC-USDT-SWAP", Book.DIRECTIONAL)
    position.opened_bar = n - 1
    flat = np.full(n, 60000.0)
    view = SymbolView(
        symbol="BTC-USDT-SWAP",
        timeframe="15m",
        ts_ms=1_700_000_000_000,
        open=flat,
        high=flat + 20,
        low=flat - 1200,  # trades through the 59,000 stop
        close=flat,
        volume=np.ones(n),
        index=n - 1,
    )
    ctx = StrategyContext(
        now_ms=view.ts_ms,
        equity=D("10000"),
        portfolio=portfolio,
        views={"BTC-USDT-SWAP": view},
        params={},
        config_hash="x",
        bar_index=view.i,
    )
    intents = build_position_manager().generate(ctx)
    assert intents
    assert intents[0].action.value == "close"
    assert "stop" in intents[0].tags["reason"]


def test_position_manager_moves_stop_to_breakeven_at_one_r():
    from oq.domain.portfolio import Portfolio
    from oq.strategy.base import StrategyContext, SymbolView
    from oq.types import Book, InstType, Side

    portfolio = Portfolio(cash=D("10000"))
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
        time_stop_bars=1000,
    )
    position = portfolio.get("BTC-USDT-SWAP", Book.DIRECTIONAL)
    n = 300
    position.opened_bar = n - 1
    close = np.linspace(60000, 61100, n)
    view = SymbolView(
        symbol="BTC-USDT-SWAP",
        timeframe="15m",
        ts_ms=1_700_000_000_000,
        open=close,
        high=close + 10,
        low=close - 10,
        close=close,
        volume=np.ones(n),
        index=n - 1,
    )
    ctx = StrategyContext(
        now_ms=1_700_000_000_000,
        equity=D("10000"),
        portfolio=portfolio,
        views={"BTC-USDT-SWAP": view},
        params={},
        config_hash="x",
        bar_index=n - 1,
    )
    intents = build_position_manager().generate(ctx)
    assert intents
    assert intents[0].action.value == "move_stop"
    assert intents[0].stop_px > D("60000")


def test_carry_strategy_needs_a_paid_basis(config, series):
    from oq.domain.portfolio import Portfolio
    from oq.strategy.base import StrategyContext
    from oq.strategy.registry import build_strategies

    carry = next(s for s in build_strategies(config) if s.id == "carry_basis")
    views = {symbol: series[symbol].view(series[symbol].size - 1) for symbol in series}
    ctx = StrategyContext(
        now_ms=views["BTC-USDT"].ts_ms,
        equity=D("10000"),
        portfolio=Portfolio(cash=D("10000")),
        views=views,
        params={},
        config_hash=config.hash,
    )
    intents = carry.generate(ctx)
    assert isinstance(intents, list)
    for intent in intents:
        assert intent.action.value in {"open", "close"}
        if intent.action.value == "open":
            assert {leg.side.value for leg in intent.legs} == {"buy", "sell"}
