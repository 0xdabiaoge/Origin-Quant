"""Tests for the v1 API surface: roles, sessions, and the admin console.

The point of these is not coverage for its own sake — every one of them pins a
rule that, if it broke, would let the console disagree with the ledger or let a
viewer move money.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from oq.app.auth import hash_password
from oq.app.main import build_app


@pytest.fixture()
def client(config, tmp_path, monkeypatch):
    cfg = config.model_copy(
        update={
            "app": config.app.model_copy(
                update={"db_path": tmp_path / "v1.db", "mode": "paper", "live_trading": False}
            )
        }
    )
    monkeypatch.setenv("OQ_API_TOKEN", "")
    monkeypatch.setenv("OQ_ADMIN_PASSWORD_HASH", hash_password("correct-horse"))
    monkeypatch.setenv("OQ_ADMIN_PASSWORD", "")
    monkeypatch.setenv("OQ_ALLOW_LOCAL_WRITES", "1")
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app) as c:
        yield c


def login(client: TestClient, username: str = "operator", password: str = "correct-horse") -> None:
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text


# -- public surface ---------------------------------------------------------
def test_brand_and_metrics_need_no_login(client):
    brand = client.get("/api/v1/public/brand")
    assert brand.status_code == 200
    assert brand.json()["name"] == "Origin Quant"

    metrics = client.get("/api/v1/public/metrics")
    assert metrics.status_code == 200
    # The public surface must never leak the book.
    assert "positions" not in metrics.json()
    assert "orders" not in metrics.json()


def test_public_metrics_are_the_only_unauthenticated_data(client, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "s3cret")
    assert client.get("/api/v1/public/metrics").status_code == 200
    assert client.get("/api/v1/app/overview").status_code == 401
    assert client.get("/api/v1/admin/users").status_code == 401


# -- login ------------------------------------------------------------------
def test_login_rejects_a_wrong_password(client):
    res = client.post("/api/auth/login", json={"username": "operator", "password": "nope"})
    assert res.status_code == 401


def test_login_sets_a_session_and_me_reports_the_role(client):
    login(client)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "owner"


def test_logout_clears_the_session_cookie(client):
    login(client)
    assert client.get("/api/v1/auth/me").json()["via"] == "cookie"
    client.post("/api/auth/logout")
    # Loopback still has read access by design, but it no longer rides a session.
    assert client.get("/api/v1/auth/me").json()["via"] != "cookie"


# -- user management --------------------------------------------------------
def test_owner_can_create_a_user_who_can_then_log_in(client):
    login(client)
    created = client.post(
        "/api/v1/admin/users",
        json={"username": "trader1", "password": "longenough", "role": "trader"},
    )
    assert created.status_code == 200, created.text

    users = client.get("/api/v1/admin/users").json()["items"]
    assert [u["username"] for u in users] == ["trader1"]

    client.post("/api/auth/logout")
    login(client, "trader1", "longenough")
    assert client.get("/api/v1/auth/me").json()["role"] == "trader"


def test_duplicate_username_is_refused(client):
    login(client)
    body = {"username": "dup", "password": "longenough", "role": "viewer"}
    assert client.post("/api/v1/admin/users", json=body).status_code == 200
    assert client.post("/api/v1/admin/users", json=body).status_code == 409


def test_short_passwords_are_refused(client):
    login(client)
    res = client.post(
        "/api/v1/admin/users", json={"username": "weak", "password": "short", "role": "viewer"}
    )
    assert res.status_code == 422


def test_unknown_role_is_refused(client):
    login(client)
    res = client.post(
        "/api/v1/admin/users",
        json={"username": "weird", "password": "longenough", "role": "admin"},
    )
    assert res.status_code == 400


def test_viewer_cannot_manage_users(client):
    login(client)
    client.post(
        "/api/v1/admin/users",
        json={"username": "looker", "password": "longenough", "role": "viewer"},
    )
    client.post("/api/auth/logout")
    login(client, "looker", "longenough")

    assert client.get("/api/v1/app/overview").status_code == 200
    assert client.get("/api/v1/admin/users").status_code == 403
    assert (
        client.post(
            "/api/v1/admin/users",
            json={"username": "x", "password": "longenough", "role": "owner"},
        ).status_code
        == 403
    )


def test_the_last_user_cannot_be_deleted(client):
    """Deleting the final account would lock every human out, so it is refused."""
    login(client)
    client.post(
        "/api/v1/admin/users",
        json={"username": "solo", "password": "longenough", "role": "owner"},
    )
    assert client.delete("/api/v1/admin/users/solo").status_code == 400


# -- audit ------------------------------------------------------------------
def test_creating_a_user_is_audited(client):
    login(client)
    client.post(
        "/api/v1/admin/users",
        json={"username": "audited", "password": "longenough", "role": "viewer"},
    )
    audit = client.get("/api/v1/admin/audit").json()
    assert audit["total"] >= 1
    assert any(row["action"] == "user.create" for row in audit["items"])


# -- danger zone ------------------------------------------------------------
def test_danger_actions_require_the_confirmation_word(client):
    login(client)
    bad = client.post("/api/v1/admin/danger/kill", json={"confirm": "nope"})
    assert bad.status_code == 400

    good = client.post("/api/v1/admin/danger/kill", json={"confirm": "kill"})
    assert good.status_code == 200


def test_unknown_danger_action_is_404(client):
    login(client)
    res = client.post("/api/v1/admin/danger/selfdestruct", json={"confirm": "SELFDESTRUCT"})
    assert res.status_code == 404


def test_danger_actions_are_audited(client):
    login(client)
    client.post("/api/v1/admin/danger/pause", json={"confirm": "PAUSE"})
    audit = client.get("/api/v1/admin/audit").json()
    assert any(row["action"] == "danger.pause" for row in audit["items"])


# -- local write guard ------------------------------------------------------
def test_loopback_reads_are_allowed_but_writes_can_be_blocked(config, tmp_path, monkeypatch):
    cfg = config.model_copy(
        update={"app": config.app.model_copy(update={"db_path": tmp_path / "guard.db"})}
    )
    monkeypatch.setenv("OQ_API_TOKEN", "")
    monkeypatch.setenv("OQ_ALLOW_LOCAL_WRITES", "0")
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app) as c:
        assert c.get("/api/v1/app/overview").status_code == 200
        assert c.post("/api/v1/admin/danger/kill", json={"confirm": "KILL"}).status_code == 403


# -- read models ------------------------------------------------------------
def test_overview_and_workspace_endpoints_answer(client):
    login(client)
    for path in (
        "/api/v1/app/overview",
        "/api/v1/app/positions",
        "/api/v1/app/orders",
        "/api/v1/app/decisions",
        "/api/v1/app/equity",
        "/api/v1/app/data",
        "/api/v1/admin/overview",
        "/api/v1/admin/config",
        "/api/v1/admin/backups",
    ):
        assert client.get(path).status_code == 200, path


def test_backup_creates_a_file(client):
    login(client)
    res = client.post("/api/v1/admin/backup")
    assert res.status_code == 200
    assert res.json()["bytes"] > 0
    listed = client.get("/api/v1/admin/backups").json()["items"]
    assert res.json()["file"] in [b["file"] for b in listed]
