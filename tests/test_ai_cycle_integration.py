"""The opt-in paper AI goes through the real orchestrator, risk and OMS."""

from __future__ import annotations

import time

from oq.ai.proposals import Proposal
from oq.ai.settings import AISettings
from oq.app import ai_api
from oq.app.orchestrator import Orchestrator
from oq.data.models import Bar
from oq.data.store import ParquetStore
from oq.types import D


def test_ai_paper_proposal_is_recorded_then_passes_real_risk_and_oms(
    config, db, store, tmp_path, monkeypatch
):
    symbol = "BTC-USDT-SWAP"
    cfg = config.model_copy(
        update={
            "universe": [entry for entry in config.universe if entry.symbol == symbol],
            "data": config.data.model_copy(update={"parquet_dir": tmp_path / "market"}),
            "app": config.app.model_copy(update={"mode": "paper"}),
        }
    )
    now = int(time.time() * 1000)
    step = 900_000
    last = (now // step) * step - step
    bars = [
        Bar(
            symbol=symbol,
            timeframe="15m",
            ts_ms=last - step * (29 - index),
            open=D("61000"),
            high=D("61100"),
            low=D("60900"),
            close=D("61000"),
            volume=D("1"),
            confirmed=True,
        )
        for index in range(30)
    ]
    ParquetStore(cfg.data.parquet_dir).write_bars(symbol, "15m", bars)
    with db.tx() as conn:
        store.set_kv(
            conn,
            ai_api.CONFIG_KEY,
            AISettings(enabled=True, auto_paper_enabled=True, model="fake").model_dump(),
        )
    monkeypatch.setattr(
        ai_api,
        "request_proposal",
        lambda settings, snapshot: (
            Proposal(action="long", symbol=symbol, stop_px="60680", rationale="test"),
            {"total_tokens": 10},
        ),
    )
    orch = Orchestrator(cfg, db, store, None)
    orch.strategies = []
    report = orch.run_once(now_ms=now)
    history = store.get_kv(ai_api.HISTORY_KEY, [])
    assert history[0]["status"] == "completed"
    assert history[0]["execution"] == "paper_candidate_only"
    assert report.intents == 1 and report.approved == 1 and report.fills == 1, report.errors
    assert db.query_one("SELECT COUNT(*) AS n FROM decisions")["n"] == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM fills")["n"] == 1
