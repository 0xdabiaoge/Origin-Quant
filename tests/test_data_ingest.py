from __future__ import annotations

import pytest

from oq.data.ingest import Ingestor
from oq.data.models import Bar, FundingPoint
from oq.data.okx_rest import OkxRest
from oq.data.store import ParquetStore
from oq.types import D


def test_incremental_candles_start_at_latest_and_page_back_to_existing(
    config, tmp_path, monkeypatch
):
    now = 1_800_000_000_000
    monkeypatch.setattr("oq.data.ingest.time.time", lambda: now / 1000)
    symbol = "BTC-USDT-SWAP"
    bars = [
        Bar(symbol, "15m", now - (250 - i) * 900_000, D("100"), D("101"), D("99"), D("100"), D("1"))
        for i in range(251)
    ]
    store = ParquetStore(tmp_path / "pq")
    store.write_bars(symbol, "15m", [bars[0]])

    class FakeRest:
        def __init__(self):
            self.calls = []

        def candles(self, symbol, timeframe, *, limit, after_ms, history):
            self.calls.append(after_ms)
            eligible = [bar for bar in bars if after_ms is None or bar.ts_ms < after_ms]
            return eligible[-limit:]

    rest = FakeRest()
    report = Ingestor(config, store, rest).sync_bars(symbol, "15m", days=5)
    assert rest.calls[0] is None
    assert len(rest.calls) == 3
    assert report.total == len(bars)
    assert store.read_bars(symbol, "15m")["ts_ms"].tolist() == [b.ts_ms for b in bars]


def test_candle_page_limit_does_not_write_a_partial_gap(config, tmp_path, monkeypatch):
    now = 1_800_000_000_000
    monkeypatch.setattr("oq.data.ingest.time.time", lambda: now / 1000)
    symbol = "BTC-USDT-SWAP"
    bars = [
        Bar(symbol, "15m", now - (250 - i) * 900_000, D("100"), D("101"), D("99"), D("100"), D("1"))
        for i in range(251)
    ]
    store = ParquetStore(tmp_path / "pq")
    store.write_bars(symbol, "15m", [bars[0]])

    class FakeRest:
        def candles(self, symbol, timeframe, *, limit, after_ms, history):
            eligible = [bar for bar in bars if after_ms is None or bar.ts_ms < after_ms]
            return eligible[-limit:]

    with pytest.raises(RuntimeError, match="pagination limit"):
        Ingestor(config, store, FakeRest()).sync_bars(symbol, "15m", days=5, max_requests=1)
    assert store.read_bars(symbol, "15m")["ts_ms"].tolist() == [bars[0].ts_ms]


def test_funding_history_pages_backwards(config, tmp_path, monkeypatch):
    now = 1_800_000_000_000
    monkeypatch.setattr("oq.data.ingest.time.time", lambda: now / 1000)
    symbol = "BTC-USDT-SWAP"
    points = [FundingPoint(symbol, now - (250 - i) * 3_600_000, D("0.001")) for i in range(251)]

    class FakeRest:
        def __init__(self):
            self.calls = []

        def funding_history(self, symbol, *, limit, after_ms):
            self.calls.append(after_ms)
            return [p for p in points if after_ms is None or p.ts_ms < after_ms][-limit:]

    rest = FakeRest()
    store = ParquetStore(tmp_path / "pq")
    assert Ingestor(config, store, rest).sync_funding(symbol, days=11) == len(points)
    assert len(rest.calls) == 4 and rest.calls[0] is None
    assert store.read_funding(symbol)["ts_ms"].tolist() == [p.ts_ms for p in points]


def test_funding_pagination_limit_does_not_write_partial_history(config, tmp_path, monkeypatch):
    now = 1_800_000_000_000
    monkeypatch.setattr("oq.data.ingest.time.time", lambda: now / 1000)
    symbol = "BTC-USDT-SWAP"

    class FakeRest:
        def funding_history(self, symbol, *, limit, after_ms):
            return [FundingPoint(symbol, now, D("0.001"))]

    store = ParquetStore(tmp_path / "pq")
    with pytest.raises(RuntimeError, match="pagination limit"):
        Ingestor(config, store, FakeRest()).sync_funding(symbol, days=3, max_requests=1)
    assert store.read_funding(symbol).empty


def test_funding_history_uses_after_and_realized_rate(config, monkeypatch):
    rest = OkxRest(config.venues["okx"])
    seen = {}

    def fake_request(method, path, *, params):
        seen.update(params)
        return [{"fundingTime": "100", "fundingRate": "0.005", "realizedRate": "0.003"}]

    monkeypatch.setattr(rest, "_request", fake_request)
    try:
        history = rest.funding_history("BTC-USDT-SWAP", after_ms=200)
        assert seen["after"] == 200 and "before" not in seen
        assert history[0].rate == D("0.003")
    finally:
        rest.close()


def test_funding_history_rejects_missing_actual_rate(config, monkeypatch):
    rest = OkxRest(config.venues["okx"])
    monkeypatch.setattr(
        rest,
        "_request",
        lambda *args, **kwargs: [{"fundingTime": "100", "fundingRate": "0.005"}],
    )
    try:
        with pytest.raises(ValueError, match="missing realized funding rate"):
            rest.funding_history("BTC-USDT-SWAP")
    finally:
        rest.close()
