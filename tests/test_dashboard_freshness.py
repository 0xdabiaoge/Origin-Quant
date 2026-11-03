from __future__ import annotations

import time

import pandas as pd
from fastapi.testclient import TestClient

from oq.app.main import build_app
from oq.clock import tf_to_ms
from oq.data.store import ParquetStore
from oq.types import Book, D, InstType, Side


def test_dashboard_does_not_present_missing_marks_as_equity(config, tmp_path, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "test-token")
    cfg = config.model_copy(
        update={
            "root": tmp_path,
            "app": config.app.model_copy(update={"db_path": tmp_path / "dashboard.db"}),
            "data": config.data.model_copy(update={"parquet_dir": tmp_path / "market"}),
        }
    )
    app = build_app(cfg, start_scheduler=False)
    ctx = app.state.oq
    ctx.orchestrator.portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("1"),
        px=D("100"),
        fee=D("0"),
        ts_ms=1,
        stop_px=D("90"),
    )
    ctx.marks = {"BTC-USDT-SWAP": D("110")}
    ctx.last_cycle_ms = int(time.time() * 1000) - 2 * tf_to_ms(cfg.data.timeframe)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        data = client.get("/api/v1/app/positions").json()
        assert data["equity"] is None and data["heat"] is None
        assert data["positions"][0]["mark_px"] is None
        assert client.get("/api/v1/app/overview").json()["equity"] is None
        status = client.get("/api/status").json()
        assert status["equity"] is None and status["positions"][0]["unrealized"] is None
        ctx.last_cycle_ms = int(time.time() * 1000)
        assert client.get("/api/v1/app/positions").json()["equity"] is not None


def test_data_dashboard_ignores_unconfirmed_and_open_bars(config, tmp_path, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "test-token")
    cfg = config.model_copy(
        update={
            "root": tmp_path,
            "app": config.app.model_copy(update={"db_path": tmp_path / "data.db"}),
            "data": config.data.model_copy(update={"parquet_dir": tmp_path / "market"}),
        }
    )
    now = int(time.time() * 1000)
    step = tf_to_ms(cfg.data.timeframe)
    expected = (now // step) * step - step
    path = ParquetStore(cfg.data.parquet_dir).bars_path("BTC-USDT-SWAP", cfg.data.timeframe)
    path.parent.mkdir(parents=True)
    pd.DataFrame(
        {"ts_ms": [expected - step, expected, expected + step], "confirmed": [True, False, True]}
    ).to_parquet(path)
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        rows = client.get("/api/v1/app/data").json()["instruments"]
    row = next(item for item in rows if item["symbol"] == "BTC-USDT-SWAP")
    assert row["last_bar_ms"] == expected - step and row["stale"] is True
