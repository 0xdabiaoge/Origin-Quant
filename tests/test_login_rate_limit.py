"""Login throttling applies to both login routes without trusting forwarded IPs."""

from fastapi.testclient import TestClient

from oq.app.auth import LoginLimiter
from oq.app.main import build_app


def test_limit_expires_and_success_reset(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("oq.app.auth.time.monotonic", lambda: clock[0])
    limiter = LoginLimiter(limit=2, window_s=60)
    assert limiter.allow("peer")
    assert limiter.allow("peer")
    assert not limiter.allow("peer")
    assert limiter.allow("another")
    clock[0] += 60
    assert limiter.allow("peer")
    limiter.reset("peer")
    assert limiter.allow("peer")


def test_login_routes_share_peer_limit(config, tmp_path, monkeypatch):
    monkeypatch.setenv("OQ_ALLOW_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("OQ_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("OQ_ADMIN_PASSWORD_HASH", "")
    cfg = config.model_copy(
        update={
            "root": tmp_path,
            "app": config.app.model_copy(update={"db_path": tmp_path / "auth.db"}),
        }
    )
    app = build_app(cfg, start_scheduler=False)
    app.state.login_limiter = LoginLimiter(limit=2, window_s=60)
    with TestClient(app) as client:
        for index in range(2):
            response = client.post(
                "/api/auth/login",
                json={"username": "bad", "password": "bad"},
                headers={"X-Forwarded-For": f"192.0.2.{index + 1}"},
            )
            assert response.status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "operator", "password": "test-password"}
            ).status_code
            == 429
        )
