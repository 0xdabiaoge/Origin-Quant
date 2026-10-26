from __future__ import annotations

import time

import numpy as np
import pytest

from oq.app.orchestrator import Orchestrator
from oq.clock import tf_to_ms
from oq.data.models import Bar
from oq.data.store import ParquetStore
from oq.types import D


class _NoopIngestor:
    """The orchestrator only needs an ingestor when it fetches data itself."""

    def sync_bars(self, *a, **kw):  # pragma: no cover - never called here
        raise AssertionError("ingestor should not be used by _build_views")


def _write_bars(store: ParquetStore, symbol: str, timeframe: str, end_ms: int, bars: int) -> None:
    step = tf_to_ms(timeframe)
    ts = np.array([end_ms - (bars - 1 - i) * step for i in range(bars)], dtype=np.int64)
    base = np.linspace(60_000.0, 61_000.0, bars)
    bars = [
        Bar(
            symbol=symbol,
            timeframe=timeframe,
            ts_ms=int(ts[i]),
            open=D(float(base[i])),
            high=D(float(base[i] + 50)),
            low=D(float(base[i] - 50)),
            close=D(float(base[i] + 10)),
            volume=D("1"),
        )
        for i in range(bars)
    ]
    store.write_bars(symbol, timeframe, bars)


@pytest.fixture()
def orchestrator(config, db, store, tmp_path):
    cfg = config.model_copy(
        update={"data": config.data.model_copy(update={"parquet_dir": tmp_path / "pq"})}
    )
    return Orchestrator(cfg, db, store, _NoopIngestor())  # type: ignore[arg-type]


def test_a_bar_is_fresh_for_one_interval_after_it_closes(orchestrator):
    """Regression: freshness compared now to the bar's *open* time.

    A 15m bar is timestamped with its open, so it only exists once the interval
    has elapsed. Measuring staleness from the open made every symbol look stale
    for the whole bar -- paper trading silently stopped between boundaries.
    """
    cfg = orchestrator.config
    timeframe = cfg.data.timeframe
    step = tf_to_ms(timeframe)
    # The newest bar opened a full interval ago and therefore just closed.
    # Under the old "now - open" test it was already a full interval stale.
    last_open = ((int(time.time() * 1000) - step) // step) * step
    _write_bars(ParquetStore(cfg.data.parquet_dir), "BTC-USDT-SWAP", timeframe, last_open, 300)

    closed_at = last_open + step
    now = closed_at + 60_000  # one minute after the close
    views, _, stale = orchestrator._build_views(now)

    assert "BTC-USDT-SWAP" in views
    assert "BTC-USDT-SWAP" not in stale
    # The other symbols have no file at all, so they are stale, not flat.
    assert "ETH-USDT-SWAP" in stale
    # The bar had already been open for a whole interval -- the old comparison
    # (now - last_open) would have called it stale despite just closing.
    assert now - last_open > cfg.data.staleness_seconds * 1000


def test_a_bar_that_closed_long_ago_is_stale(orchestrator):
    cfg = orchestrator.config
    timeframe = cfg.data.timeframe
    step = tf_to_ms(timeframe)
    old_open = ((int(time.time() * 1000) - 60 * 60_000) // step) * step
    _write_bars(ParquetStore(cfg.data.parquet_dir), "BTC-USDT-SWAP", timeframe, old_open, 300)

    views, _, stale = orchestrator._build_views(old_open + step * 30)

    assert "BTC-USDT-SWAP" not in views
    assert "BTC-USDT-SWAP" in stale


def test_missing_symbols_are_stale_not_assumed_flat(orchestrator):
    views, _, stale = orchestrator._build_views(int(time.time() * 1000))
    assert views == {}
    assert stale  # "cannot read" must never be treated as "no position"
