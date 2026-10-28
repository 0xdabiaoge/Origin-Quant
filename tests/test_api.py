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


def test_spa_routes_render_the_app_shell(app_client):
    """History-mode routes must return the SPA, not a 404."""
    for path in ("/", "/app", "/admin/users", "/docs", "/login"):
        res = app_client.get(path)
        assert res.status_code == 200, path
        assert '<div id="app">' in res.text, path


def test_unknown_api_paths_stay_404(app_client):
    """A typo in a fetch must fail loudly instead of returning HTML."""
    assert app_client.get("/api/v1/does-not-exist").status_code == 404


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


def test_the_health_endpoint_reports_the_build_version(app_client, monkeypatch):
    """Regression: /api/health used the module constant, not the build stamp.

    The container was stamped 1.0.1 while the health endpoint kept reporting
    1.0.0 from ``oq.version.__version__``, so the one screen an operator trusts
    to tell them what is running was lying.
    """
    monkeypatch.setenv("OQ_VERSION", "7.7.7")
    body = app_client.get("/api/health").json()
    assert body["version"] == "7.7.7"


def test_the_package_declares_the_built_spa_as_package_data():
    """`pip install .` must carry the UI.

    This is a packaging rule, not a runtime one: it shipped v1.0.3 with a
    server whose index fallback quietly returned JSON instead of the app.
    """
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    globs = data["tool"]["setuptools"]["package-data"]["oq"]
    assert any("app/web" in g for g in globs), globs


def test_the_admin_logs_endpoint_reads_the_configured_log_dir(config, tmp_path, monkeypatch):
    """The console tails OQ_LOG_DIR; without it the page is permanently empty."""
    from fastapi.testclient import TestClient

    from oq.app.main import build_app

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "engine.log").write_text("line one\nline two\n", encoding="utf-8")

    cfg = config.model_copy(
        update={"app": config.app.model_copy(update={"db_path": tmp_path / "logs.db"})}
    )
    monkeypatch.setenv("OQ_API_TOKEN", "")
    monkeypatch.setenv("OQ_LOG_DIR", str(log_dir))
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app) as c:
        body = c.get("/api/v1/admin/logs").json()
    assert body["available"] is True
    assert body["lines"] == ["line one", "line two"]
