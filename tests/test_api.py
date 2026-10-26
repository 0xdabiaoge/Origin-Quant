from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from oq.app.main import build_app


@pytest.fixture()
def app_client(config, tmp_path, monkeypatch):
    cfg = config.model_copy(
        update={
            "app": config.app.model_copy(
                update={"db_path": tmp_path / "api.db", "mode": "paper", "live_trading": False}
            )
        }
    )
    monkeypatch.setenv("OQ_API_TOKEN", "")
    monkeypatch.setenv("OQ_ADMIN_PASSWORD", "")
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app) as client:
        yield client


def test_health_is_public(app_client):
    res = app_client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["mode"] == "paper"


def test_status_requires_auth_when_exposed(app_client, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "s3cret")
    assert app_client.get("/api/status").status_code == 401
    ok = app_client.get("/api/status", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert ok.json()["mode"] == "paper"


def test_bad_bearer_token_is_rejected(app_client, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "s3cret")
    res = app_client.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert res.status_code == 401


def test_dashboard_is_served(app_client):
    res = app_client.get("/")
    assert res.status_code == 200
    assert "Origin Quant" in res.text


def test_static_assets_are_served(app_client):
    assert app_client.get("/static/app.js").status_code == 200
    assert app_client.get("/static/styles.css").status_code == 200


def test_config_endpoint_redacts_secrets(app_client):
    res = app_client.get("/api/config")
    assert res.status_code == 200
    body = res.json()
    text = str(body).lower()
    for needle in ("api_key", "secret", "passphrase"):
        assert needle not in text


def test_backtest_endpoint_runs_on_synthetic_data(app_client):
    res = app_client.post(
        "/api/backtest",
        json={"days": 10, "initial_cash": "10000", "source": "synthetic", "seed": 3},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "metrics" in body
    assert "equity" in body
    assert body["metrics"]["start_equity"] == pytest.approx(10000.0)


def test_kill_and_resume_are_audited(app_client):
    assert app_client.post("/api/control/pause").status_code == 200
    status = app_client.get("/api/status").json()
    assert status["halted"] is True
    assert app_client.post("/api/control/resume").status_code == 200
    status = app_client.get("/api/status").json()
    assert status["halted"] is False


def test_decisions_endpoint_returns_a_list(app_client):
    res = app_client.get("/api/decisions")
    assert res.status_code == 200
    assert isinstance(res.json()["decisions"], list)
