from __future__ import annotations

import time

import numpy as np
import pytest

from oq.app.orchestrator import Orchestrator
from oq.clock import tf_to_ms
from oq.data.models import Bar
from oq.data.store import ParquetStore
from oq.types import ZERO, D


class _NoopIngestor:
    """The orchestrator only needs an ingestor when it fetches data itself."""

    def sync_bars(self, *a, **kw):  # pragma: no cover - never called here
        raise AssertionError("ingestor should not be used by _build_views")


def _write_bars(
    store: ParquetStore, symbol: str, timeframe: str, last_open_ms: int, bars: int
) -> None:
    step = tf_to_ms(timeframe)
    base = np.linspace(60_000.0, 61_000.0, bars)
    rows = [
        Bar(
            symbol=symbol,
            timeframe=timeframe,
            ts_ms=last_open_ms - (bars - 1 - i) * step,
            open=D(float(base[i])),
            high=D(float(base[i] + 50)),
            low=D(float(base[i] - 50)),
            close=D(float(base[i] + 10)),
            volume=D("1"),
        )
        for i in range(bars)
    ]
    store.write_bars(symbol, timeframe, rows)


@pytest.fixture()
def orchestrator(config, db, store, tmp_path):
    cfg = config.model_copy(
        update={"data": config.data.model_copy(update={"parquet_dir": tmp_path / "pq"})}
    )
    return Orchestrator(cfg, db, store, _NoopIngestor())  # type: ignore[arg-type]


def _boundary(now: int, timeframe: str) -> int:
    step = tf_to_ms(timeframe)
    return (now // step) * step


def test_the_last_closed_bar_stays_fresh_for_the_whole_bar(orchestrator):
    """Regression: freshness compared now to the bar's *open* timestamp.

    A 15m bar closes one interval after it opens, so the newest bar that should
    exist at any moment is the one stamped ``floor(now) - interval``. Comparing
    ``now`` to the open time made every symbol look stale for all but the first
    180 seconds of each bar, and paper cycles were skipped with ``no_data``.
    """
    cfg = orchestrator.config
    timeframe = cfg.data.timeframe
    step = tf_to_ms(timeframe)
    store = ParquetStore(cfg.data.parquet_dir)

    for offset_s in (60, 300, 700):  # early, middle and late in the bar
        now = 1_800_000_000_000 + offset_s * 1000
        expected_open = _boundary(now, timeframe) - step
        _write_bars(store, "BTC-USDT-SWAP", timeframe, expected_open, 300)

        views, _, stale = orchestrator._build_views(now)

        assert "BTC-USDT-SWAP" in views, f"bar should be fresh {offset_s}s into the interval"
        assert "BTC-USDT-SWAP" not in stale


def test_a_missing_bar_is_stale_once_the_grace_has_passed(orchestrator):
    cfg = orchestrator.config
    timeframe = cfg.data.timeframe
    step = tf_to_ms(timeframe)
    now = 1_800_000_000_000 + 600_000  # 10 minutes past the boundary
    expected_open = _boundary(now, timeframe) - step
    _write_bars(
        ParquetStore(cfg.data.parquet_dir),
        "BTC-USDT-SWAP",
        timeframe,
        expected_open - 4 * step,  # four bars behind
        300,
    )

    views, _, stale = orchestrator._build_views(now)

    assert "BTC-USDT-SWAP" not in views
    assert "BTC-USDT-SWAP" in stale


def test_a_late_bar_gets_a_short_publication_grace(orchestrator):
    """A bar may arrive seconds after its boundary; do not panic instantly."""
    cfg = orchestrator.config
    timeframe = cfg.data.timeframe
    step = tf_to_ms(timeframe)
    boundary = 1_800_000_000_000
    # 10 seconds after the boundary the previous bar is missing, but the venue
    # has not had time to publish it yet.
    now = boundary + 10_000
    _write_bars(
        ParquetStore(cfg.data.parquet_dir),
        "BTC-USDT-SWAP",
        timeframe,
        boundary - 2 * step,
        300,
    )

    views, _, stale = orchestrator._build_views(now)

    assert "BTC-USDT-SWAP" in views
    assert "BTC-USDT-SWAP" not in stale


def test_missing_symbols_are_stale_not_assumed_flat(orchestrator):
    views, _, stale = orchestrator._build_views(int(time.time() * 1000))
    assert views == {}
    assert stale  # "cannot read" must never be treated as "no position"


def test_funding_is_persisted_with_its_marker(orchestrator):
    """Regression: funding was applied in memory but never written.

    The "already charged" marker committed immediately while the portfolio
    state was only saved when an order filled, so a cycle that collected
    funding without trading lost the money on the next load -- and the marker
    stopped it from ever being charged again.
    """
    from oq.data.models import FundingPoint
    from oq.domain.portfolio import Portfolio
    from oq.types import Book, InstType, Side

    cfg = orchestrator.config
    now = 1_800_000_000_000
    settlement = now - 3_600_000  # one hour ago

    portfolio: Portfolio = orchestrator.portfolio
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.CARRY,
        inst_type=InstType.SWAP,
        side=Side.SELL,
        qty=D("0.01"),  # apply_fill takes a positive size and applies the side
        px=D("60000"),
        fee=D("0"),
        ts_ms=now - 7_200_000,
    )
    ParquetStore(cfg.data.parquet_dir).write_funding(
        "BTC-USDT-SWAP", [FundingPoint(symbol="BTC-USDT-SWAP", ts_ms=settlement, rate=D("0.0002"))]
    )

    orchestrator._apply_due_funding(now, {"BTC-USDT-SWAP": D("60000")})

    # A short position *receives* funding when the rate is positive.
    assert portfolio.funding_net > ZERO
    charged = portfolio.funding_net

    reloaded = orchestrator.store.load_portfolio()
    assert reloaded.funding_net == charged, "funding must survive a reload"
    assert reloaded.cash == portfolio.cash
    assert orchestrator.store.get_kv("funding:BTC-USDT-SWAP") == settlement

    # Re-running must not charge the same settlement twice.
    orchestrator._apply_due_funding(now, {"BTC-USDT-SWAP": D("60000")})
    assert portfolio.funding_net == charged
