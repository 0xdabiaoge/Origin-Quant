from __future__ import annotations

from fastapi.testclient import TestClient

from oq.app.main import build_app


def test_public_data_and_local_auth_are_private_by_default(config, tmp_path, monkeypatch):
    monkeypatch.setenv("OQ_ALLOW_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("OQ_API_TOKEN", "")
    monkeypatch.setenv("OQ_ADMIN_PASSWORD", "operator-password")
    monkeypatch.setenv("OQ_ADMIN_PASSWORD_HASH", "")
    cfg = config.model_copy(
        update={
            "root": tmp_path,
            "app": config.app.model_copy(update={"db_path": tmp_path / "security.db"}),
        }
    )
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/app/overview").status_code == 401
        metrics = client.get("/api/v1/public/metrics").json()
        assert metrics["stats"] is None and metrics["equity"] == []
        assert (
            client.post(
                "/api/auth/login", json={"username": "arbitrary", "password": "operator-password"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/login", json={"username": "operator", "password": "operator-password"}
            ).status_code
            == 200
        )
        assert client.get("/api/v1/admin/users").status_code == 200


def test_role_password_and_deletion_revoke_existing_cookie(config, tmp_path, monkeypatch):
    monkeypatch.setenv("OQ_ALLOW_LOOPBACK_AUTH", "0")
    monkeypatch.setenv("OQ_API_TOKEN", "")
    monkeypatch.setenv("OQ_ADMIN_PASSWORD", "operator-password")
    monkeypatch.setenv("OQ_ADMIN_PASSWORD_HASH", "")
    cfg = config.model_copy(
        update={
            "root": tmp_path,
            "app": config.app.model_copy(update={"db_path": tmp_path / "sessions.db"}),
        }
    )
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app) as admin, TestClient(app) as user:
        assert (
            admin.post(
                "/api/auth/login", json={"username": "operator", "password": "operator-password"}
            ).status_code
            == 200
        )
        assert (
            admin.post(
                "/api/v1/admin/users",
                json={"username": "reader", "password": "reader-password", "role": "viewer"},
            ).status_code
            == 200
        )

        def log_in(password):
            return user.post("/api/auth/login", json={"username": "reader", "password": password})

        assert log_in("reader-password").status_code == 200
        assert (
            admin.put("/api/v1/admin/users/reader/role", json={"role": "trader"}).status_code == 200
        )
        assert user.get("/api/v1/app/overview").status_code == 401
        assert log_in("reader-password").status_code == 200
        assert (
            admin.put(
                "/api/v1/admin/users/reader/password", json={"password": "new-reader-password"}
            ).status_code
            == 200
        )
        assert user.get("/api/v1/app/overview").status_code == 401
        assert log_in("new-reader-password").status_code == 200
        assert admin.delete("/api/v1/admin/users/reader").status_code == 200
        assert user.get("/api/v1/app/overview").status_code == 401
