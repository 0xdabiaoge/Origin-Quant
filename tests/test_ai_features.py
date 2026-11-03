"""Intelligence stays opt-in, bounded, auditable, and never submits orders."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from oq.ai.news import parse_rss
from oq.ai.proposals import Proposal, proposal_to_intent
from oq.ai.settings import AISettings
from oq.app import ai_api
from oq.data.store import ParquetStore
from oq.storage import Store


@pytest.fixture()
def ai_context(config, db, tmp_path, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "test-owner-token")
    monkeypatch.setenv("OQ_AI_KEY_PRIMARY", "test-secret-never-return")
    cfg = config.model_copy(
        update={
            "app": config.app.model_copy(update={"mode": "paper"}),
            "data": config.data.model_copy(update={"parquet_dir": tmp_path / "bars"}),
        }
    )
    ctx = SimpleNamespace(config=cfg, db=db, store=Store(db))
    ctx.audit = lambda *args: None
    app = FastAPI()
    app.state.oq = ctx
    app.include_router(ai_api.router, prefix="/api/v1")
    client = TestClient(app, headers={"Authorization": "Bearer test-owner-token"})
    return ctx, client


def _write_bar(ctx, now):
    step = 900_000
    opened = (now // step) * step - step
    path = ParquetStore(ctx.config.data.parquet_dir).bars_path("BTC-USDT-SWAP", "15m")
    path.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "ts_ms": opened,
                "open": 100.0,
                "high": 102.0,
                "low": 98.0,
                "close": 101.0,
                "volume": 5.0,
                "confirmed": True,
            }
        ]
    ).to_parquet(path)
    return opened


def test_config_is_disabled_and_does_not_return_secrets(ai_context):
    _, client = ai_context
    response = client.get("/api/v1/admin/ai/config")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["auto_paper_enabled"] is False
    assert response.json()["has_key"] is True
    assert "test-secret-never-return" not in response.text
    assert (
        client.post("/api/v1/admin/ai/analyze", json={"symbol": "BTC-USDT-SWAP"}).status_code == 409
    )
    assert (
        client.get("/api/v1/admin/ai/config", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )


def test_provider_and_secret_reference_validation(monkeypatch):
    with pytest.raises(ValidationError):
        AISettings(api_key_env="OKX_LIVE_SECRET_KEY")
    with pytest.raises(ValidationError):
        AISettings(provider="openai", endpoint="https://evil.example/v1/chat/completions")
    with pytest.raises(ValidationError):
        AISettings(provider="custom", endpoint="http://127.0.0.1/admin")
    with pytest.raises(ValidationError):
        AISettings(provider="custom", endpoint="https://other.example/v1/chat/completions")
    assert AISettings(provider="deepseek", endpoint="https://api.deepseek.com/chat/completions")


def test_rss_is_bounded_and_preserves_source_and_time():
    xml = b"""<rss><channel><item><title>News</title><description>Test</description>
        <link>https://example.com/news</link><pubDate>Fri, 30 Oct 2026 00:00:00 GMT</pubDate>
        </item></channel></rss>"""
    item = parse_rss(xml, source="Example", fetched_ms=123)[0]
    assert item["source"] == "Example" and item["fetched_ms"] == 123
    assert item["published_ms"] > 0
    with pytest.raises(ValueError):
        parse_rss(b'<!DOCTYPE x [<!ENTITY a "x">]><rss/>', source="x", fetched_ms=1)
    with pytest.raises(ValueError):
        parse_rss(
            '<!DOCTYPE x [<!ENTITY a "x">]><rss/>'.encode("utf-16"),
            source="x",
            fetched_ms=1,
        )


def test_intent_is_snapshot_bound_and_rejects_stale_or_wrong_side():
    snapshot = {"symbol": "BTC-USDT-SWAP", "captured_ms": 1000, "reference_price": "100"}
    proposal = Proposal(action="long", symbol="BTC-USDT-SWAP", stop_px="95")
    intent = proposal_to_intent(proposal, snapshot, now_ms=1100)
    assert intent is not None and intent.tags["snapshot_hash"]
    with pytest.raises(ValueError, match="expired"):
        proposal_to_intent(proposal, snapshot, now_ms=300_000)
    with pytest.raises(ValueError, match="wrong side"):
        proposal_to_intent(
            Proposal(action="long", symbol="BTC-USDT-SWAP", stop_px="105"), snapshot, now_ms=1100
        )
    with pytest.raises(ValueError, match="symbol"):
        proposal_to_intent(
            Proposal(action="short", symbol="ETH-USDT-SWAP", stop_px="105"), snapshot, now_ms=1100
        )


def test_manual_analysis_records_proposal_but_never_order(ai_context, monkeypatch):
    ctx, client = ai_context
    now = int(time.time() * 1000)
    _write_bar(ctx, now)
    monkeypatch.setattr(
        ai_api,
        "request_proposal",
        lambda settings, snapshot: (
            Proposal(action="long", symbol=snapshot["symbol"], stop_px="95", rationale="test"),
            {"total_tokens": 10},
        ),
    )
    config = client.get("/api/v1/admin/ai/config").json()
    config.pop("has_key")
    config.update({"enabled": True, "model": "test-model"})
    assert client.put("/api/v1/admin/ai/config", json=config).status_code == 200
    result = client.post("/api/v1/admin/ai/analyze", json={"symbol": "BTC-USDT-SWAP"}).json()
    assert result["status"] == "completed" and result["execution"] == "research_only"
    assert client.get("/api/v1/admin/ai/history").json()["items"][0]["id"] == result["id"]
    assert ctx.db.query_one("SELECT COUNT(*) AS n FROM orders")["n"] == 0


def test_auto_intent_requires_explicit_paper_mode_and_current_view(ai_context, monkeypatch):
    ctx, client = ai_context
    now = int(time.time() * 1000)
    opened = _write_bar(ctx, now)
    calls = []

    def fake(settings, snapshot):
        calls.append(snapshot)
        return Proposal(action="long", symbol=snapshot["symbol"], stop_px="95"), {}

    monkeypatch.setattr(ai_api, "request_proposal", fake)
    view = SimpleNamespace(ts_ms=opened, price=101.0)
    views = {"BTC-USDT-SWAP": view}
    assert ai_api.collect_paper_intent(ctx, views, now_ms=now) is None
    assert not calls
    config = client.get("/api/v1/admin/ai/config").json()
    config.pop("has_key")
    config.update({"enabled": True, "auto_paper_enabled": True, "model": "test"})
    assert client.put("/api/v1/admin/ai/config", json=config).status_code == 200
    assert ai_api.collect_paper_intent(ctx, views, now_ms=now) is not None
    ctx.config = ctx.config.model_copy(
        update={"app": ctx.config.app.model_copy(update={"mode": "live"})}
    )
    assert ai_api.collect_paper_intent(ctx, views, now_ms=now) is None
    assert len(calls) == 1


def test_news_worker_is_opt_in_and_stops_without_further_requests(ai_context, monkeypatch):
    ctx, client = ai_context
    calls = []
    monkeypatch.setattr(
        ai_api,
        "refresh_news",
        lambda: calls.append(1) or {"items": [], "fetched_ms": 123, "errors": {}},
    )
    worker = ai_api.NewsWorker(ctx)
    worker.run_once()
    assert calls == []
    config = client.get("/api/v1/admin/ai/config").json()
    config.pop("has_key")
    config["auto_news_enabled"] = True
    assert client.put("/api/v1/admin/ai/config", json=config).status_code == 200
    worker.run_once()
    assert calls == [1]
    assert client.get("/api/v1/admin/news").json()["fetched_ms"] == 123
    worker.stop()
    worker.run_once()
    assert calls == [1]


def test_news_worker_discards_results_if_stopped_during_fetch(ai_context, monkeypatch):
    ctx, client = ai_context
    config = client.get("/api/v1/admin/ai/config").json()
    config.pop("has_key")
    config["auto_news_enabled"] = True
    assert client.put("/api/v1/admin/ai/config", json=config).status_code == 200
    worker = ai_api.NewsWorker(ctx)

    def fetch_then_stop():
        worker.stop()
        return {"items": [], "fetched_ms": 555, "errors": {}}

    monkeypatch.setattr(ai_api, "refresh_news", fetch_then_stop)
    worker.run_once()
    assert client.get("/api/v1/admin/news").json()["fetched_ms"] == 0
