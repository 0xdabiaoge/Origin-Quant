from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from oq.app import notifications_api
from oq.app.main import build_app
from oq.domain.events import EventType
from oq.notifications import telegram


@pytest.fixture()
def notification_context(config, tmp_path, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "test-owner")
    monkeypatch.delenv("OQ_NOTIFY_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("OQ_NOTIFY_TELEGRAM_CHAT_ID", raising=False)
    cfg = config.model_copy(
        update={
            "root": tmp_path,
            "app": config.app.model_copy(
                update={"db_path": tmp_path / "notify.db", "mode": "paper"}
            ),
        }
    )
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app) as client:
        yield app.state.oq, client


def _credentials(monkeypatch):
    monkeypatch.setenv("OQ_NOTIFY_TELEGRAM_TOKEN", "123456789:" + "A" * 32)
    monkeypatch.setenv("OQ_NOTIFY_TELEGRAM_CHAT_ID", "-123456789")


def _emit(ctx, event_type, symbol="BTC-USDT-SWAP"):
    with ctx.db.tx() as conn:
        ctx.store.emit(conn, event_type, {"secret": "never-send-this"}, ts_ms=123, symbol=symbol)


def test_notifications_default_off_and_owner_only(notification_context, monkeypatch):
    ctx, client = notification_context
    sent = []
    monkeypatch.setattr(notifications_api, "send_telegram", sent.append)
    _emit(ctx, EventType.KILL_SWITCH)
    ctx.notification_worker.run_once()
    assert sent == []
    assert client.get("/api/v1/admin/notifications/config").status_code == 401
    headers = {"Authorization": "Bearer test-owner"}
    state = client.get("/api/v1/admin/notifications/config", headers=headers).json()
    assert state["enabled"] is False and state["credentials_available"] is False
    response = client.put(
        "/api/v1/admin/notifications/config", json={"enabled": True}, headers=headers
    )
    assert response.status_code == 409
    assert client.post("/api/v1/admin/notifications/test", headers=headers).status_code == 409


def test_worker_sends_new_risk_event_without_payload_and_keeps_cursor(
    notification_context, monkeypatch
):
    ctx, client = notification_context
    _credentials(monkeypatch)
    sent = []
    monkeypatch.setattr(notifications_api, "send_telegram", sent.append)
    _emit(ctx, EventType.KILL_SWITCH)
    headers = {"Authorization": "Bearer test-owner"}
    enabled = client.put(
        "/api/v1/admin/notifications/config",
        json={"enabled": True, "poll_interval_s": 5},
        headers=headers,
    )
    assert enabled.status_code == 200
    ctx.notification_worker.run_once()
    assert sent == []  # events before enabling are not sent
    _emit(ctx, EventType.ERROR)
    ctx.notification_worker._last_attempt = float("-inf")
    ctx.notification_worker.run_once()
    assert len(sent) == 1 and "error" in sent[0] and "BTC-USDT-SWAP" in sent[0]
    assert "never-send-this" not in sent[0]
    assert ctx.store.get_kv(notifications_api._STATUS)["last_result"] == "acknowledged"
    ctx.notification_worker._last_attempt = float("-inf")
    ctx.notification_worker.run_once()
    assert len(sent) == 1


def test_failed_delivery_retries_without_advancing_cursor(notification_context, monkeypatch):
    ctx, client = notification_context
    _credentials(monkeypatch)
    headers = {"Authorization": "Bearer test-owner"}
    assert (
        client.put(
            "/api/v1/admin/notifications/config", json={"enabled": True}, headers=headers
        ).status_code
        == 200
    )
    _emit(ctx, EventType.ORDER_UNKNOWN)
    attempts = []

    def sender(text):
        attempts.append(text)
        if len(attempts) == 1:
            raise RuntimeError("private detail should not leak")

    monkeypatch.setattr(notifications_api, "send_telegram", sender)
    before = ctx.store.get_kv(notifications_api._CURSOR)
    ctx.notification_worker.run_once()
    assert ctx.store.get_kv(notifications_api._CURSOR) == before
    assert ctx.store.get_kv(notifications_api._STATUS)["last_result"] == "failed"
    ctx.notification_worker._last_attempt = float("-inf")
    ctx.notification_worker.run_once()
    assert len(attempts) == 2
    assert ctx.store.get_kv(notifications_api._CURSOR) > before
    assert "private detail" not in str(
        client.get("/api/v1/admin/notifications/config", headers=headers).json()
    )


def test_manual_test_is_explicit_and_audited(notification_context, monkeypatch):
    ctx, client = notification_context
    _credentials(monkeypatch)
    sender = MagicMock()
    monkeypatch.setattr(notifications_api, "send_telegram", sender)
    headers = {"Authorization": "Bearer test-owner"}
    assert (
        client.put(
            "/api/v1/admin/notifications/config", json={"enabled": True}, headers=headers
        ).status_code
        == 200
    )
    assert client.post("/api/v1/admin/notifications/test", headers=headers).json()["ok"]
    assert sender.call_count == 1
    assert (
        ctx.db.query_one("SELECT action FROM audit WHERE action = 'notification.test'") is not None
    )


def test_telegram_transport_uses_fixed_host_without_leaking_failure(monkeypatch):
    _credentials(monkeypatch)
    observed = {}

    class Response:
        status = 200

        def read(self, limit):
            return b'{"ok": true}'

    class Connection:
        def __init__(self, host, timeout):
            observed.update(host=host, timeout=timeout)

        def request(self, method, path, body, headers):
            observed.update(method=method, body=body)

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(telegram.http.client, "HTTPSConnection", Connection)
    telegram.send_telegram("test")
    assert observed["host"] == "api.telegram.org" and observed["method"] == "POST"

    def fail(*args, **kwargs):
        raise OSError("secret transport URL")

    monkeypatch.setattr(telegram.http.client, "HTTPSConnection", fail)
    with pytest.raises(RuntimeError, match="notification delivery failed") as error:
        telegram.send_telegram("test")
    assert "secret transport URL" not in str(error.value)
