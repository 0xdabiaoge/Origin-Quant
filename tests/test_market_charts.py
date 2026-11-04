from __future__ import annotations

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from oq.app.admin_data import decision_reason_histogram
from oq.app.main import build_app
from oq.app.market_api import aggregate_bars, confirmed_bars
from oq.data.store import ParquetStore

STEP = 900_000


def bars(count=8):
    return pd.DataFrame(
        [
            {
                "ts_ms": i * STEP,
                "open": 100 + i,
                "high": 110 + i,
                "low": 90 + i,
                "close": 101 + i,
                "volume": 10 + i,
                "confirmed": True,
            }
            for i in range(count)
        ]
    )


def test_aggregation_uses_ohlc_and_sums_volume():
    result = aggregate_bars(bars(), STEP, STEP * 4, STEP * 8)
    assert result == [
        {"ts_ms": 0, "open": 100, "high": 113, "low": 90, "close": 104, "volume": 46},
        {"ts_ms": STEP * 4, "open": 104, "high": 117, "low": 94, "close": 108, "volume": 62},
    ]


def test_aggregation_rejects_gaps_and_incomplete_buckets():
    frame = bars(11).drop(index=1)
    result = aggregate_bars(frame, STEP, STEP * 4, STEP * 11 + 5)
    assert [r["ts_ms"] for r in result] == [STEP * 4]


def test_only_confirmed_valid_closed_candles_are_charted():
    frame = bars()
    frame["close"] = frame["close"].astype(float)
    frame.loc[1, "confirmed"] = False
    frame.loc[2, "close"] = float("inf")
    frame.loc[3, "high"] = 1
    frame.loc[4, "volume"] = -1
    frame.loc[5, "ts_ms"] += 1
    assert confirmed_bars(frame, STEP, 7 * STEP).ts_ms.tolist() == [0, 6 * STEP]


@pytest.fixture
def client(config, tmp_path, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "chart-token")
    cfg = config.model_copy(
        update={
            "root": tmp_path,
            "app": config.app.model_copy(update={"db_path": tmp_path / "charts.db"}),
            "data": config.data.model_copy(update={"parquet_dir": tmp_path / "market"}),
        }
    )
    monkeypatch.setattr("oq.app.market_api.time.time", lambda: 101 * STEP / 1000)
    path = ParquetStore(cfg.data.parquet_dir).bars_path("BTC-USDT-SWAP", "15m")
    path.parent.mkdir(parents=True)
    bars(101).to_parquet(path)
    with TestClient(build_app(cfg, start_scheduler=False)) as result:
        yield result


def test_chart_api_requires_auth_and_bounds_inputs(client):
    endpoint = "/api/v1/app/market/candles"
    assert client.get(endpoint, params={"symbol": "BTC-USDT-SWAP"}).status_code == 401
    headers = {"Authorization": "Bearer chart-token"}
    assert (
        client.get(endpoint, params={"symbol": "../../etc/passwd"}, headers=headers).status_code
        == 404
    )
    assert (
        client.get(
            endpoint, params={"symbol": "BTC-USDT-SWAP", "limit": 1001}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.get(
            endpoint, params={"symbol": "BTC-USDT-SWAP", "timeframe": "1m"}, headers=headers
        ).status_code
        == 422
    )


def test_chart_api_returns_bounded_real_store_data_and_freshness(client):
    headers = {"Authorization": "Bearer chart-token"}
    data = client.get(
        "/api/v1/app/market/candles",
        params={"symbol": "BTC-USDT-SWAP", "timeframe": "1h", "limit": 2},
        headers=headers,
    ).json()
    assert len(data["items"]) == 2
    assert data["items"][-1]["ts_ms"] == 96 * STEP
    assert data["stale"] is False and data["confirmed_only"] is True
    quotes = client.get("/api/v1/app/market/summary", headers=headers).json()["items"]
    btc = next(q for q in quotes if q["symbol"] == "BTC-USDT-SWAP")
    assert btc["change_24h_pct"] == pytest.approx((201 / 105 - 1) * 100)
    missing = next(q for q in quotes if q["symbol"] == "ETH-USDT-SWAP")
    assert missing["close"] is None and missing["change_24h_pct"] is None and missing["stale"]


def test_rejection_chart_counts_only_failed_gates(db):
    with db.tx() as conn:
        conn.execute("CREATE TEMP TABLE decisions (ts_ms INTEGER, approved INTEGER, gates TEXT)")
        conn.executemany(
            "INSERT INTO decisions VALUES (?, ?, ?)",
            [
                (
                    1,
                    0,
                    json.dumps(
                        [{"code": "spread", "passed": True}, {"code": "cost_edge", "passed": False}]
                    ),
                ),
                (2, 0, json.dumps([{"code": "spread", "ok": False}])),
                (3, 1, json.dumps([{"code": "universe", "passed": True}])),
            ],
        )
        assert decision_reason_histogram(conn) == [
            {"gate": "cost_edge", "n": 1},
            {"gate": "spread", "n": 1},
        ]
        assert decision_reason_histogram(conn, limit=1) == []
