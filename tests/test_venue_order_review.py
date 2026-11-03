"""Manual evidence is auditable and cannot silently resolve local orders."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oq.app.admin_api import router
from oq.domain.order import Order
from oq.execution.okx_adapter import OkxAdapter
from oq.execution.venue_readback import ReviewConflict, record_order_review
from oq.storage import Store
from oq.types import Book, D, InstType, OrderStatus, OrderType, Side


@pytest.fixture()
def order_store(db, config):
    store = Store(db, config_hash=config.hash)
    order = Order(
        client_id="oq123",
        symbol="BTC-USDT-SWAP",
        inst_type=InstType.SWAP,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=D("0.01"),
        status=OrderStatus.UNKNOWN,
        created_ms=1000,
        updated_ms=1001,
        book=Book.DIRECTIONAL,
    )
    with db.tx() as conn:
        store.save_order(conn, order)
    return store


def venue_row(config):
    return {
        "instId": "BTC-USDT-SWAP",
        "clOrdId": "oq123",
        "ordId": "1234",
        "side": "buy",
        "state": "filled",
        "sz": str(config.instrument("BTC-USDT-SWAP").contracts(D("0.01"))),
        "accFillSz": str(config.instrument("BTC-USDT-SWAP").contracts(D("0.01"))),
    }


def test_record_review_is_idempotent_and_keeps_order_blocked(order_store, config):
    calls = []

    def order(*args, **kwargs):
        calls.append(1)
        return venue_row(config)

    rest = SimpleNamespace(demo=True, order=order)
    before = order_store.open_orders()
    arguments = dict(actor="operator", client_id="oq123", review_id=str(uuid.uuid4()))
    first = record_order_review(order_store, rest, config.instrument_map(), **arguments)
    second = record_order_review(order_store, rest, config.instrument_map(), **arguments)
    assert first == second and len(calls) == 1
    assert first["observation"]["venue_state"] == "filled"
    assert first["account_provenance_verified"] is False
    assert not first["reconciled"] and not first["ledger_changed"]
    assert not first["entry_block_released"]
    assert order_store.open_orders() == before
    assert order_store.db.query_one("SELECT COUNT(*) AS n FROM fills")["n"] == 0
    assert order_store.db.query_one("SELECT COUNT(*) AS n FROM positions")["n"] == 0
    assert order_store.db.query_one("SELECT COUNT(*) AS n FROM audit")["n"] == 1
    with pytest.raises(ReviewConflict):
        record_order_review(
            order_store, rest, config.instrument_map(), **{**arguments, "actor": "other"}
        )


def test_stale_local_order_cannot_be_recorded(order_store, config):
    def order(*args, **kwargs):
        with order_store.db.tx() as conn:
            conn.execute("UPDATE orders SET status='partial' WHERE client_id='oq123'")
        return venue_row(config)

    review_id = str(uuid.uuid4())
    with pytest.raises(ReviewConflict, match="changed"):
        record_order_review(
            order_store,
            SimpleNamespace(demo=True, order=order),
            config.instrument_map(),
            actor="operator",
            client_id="oq123",
            review_id=review_id,
        )
    assert order_store.get_kv(f"venue.order_review.{review_id}") is None
    assert order_store.db.query_one("SELECT COUNT(*) AS n FROM audit")["n"] == 0


def test_review_endpoint_requires_owner_and_records_failed_readback(
    order_store, config, monkeypatch
):
    monkeypatch.setenv("OQ_API_TOKEN", "token")

    def error(*args, **kwargs):
        raise TimeoutError("private exchange detail")

    rest = SimpleNamespace(has_credentials=True, demo=True, order=error)
    broker = OkxAdapter(config, rest, allow_live=False)
    cfg = config.model_copy(update={"app": config.app.model_copy(update={"mode": "live"})})
    app = FastAPI()
    app.state.oq = SimpleNamespace(
        config=cfg, orchestrator=SimpleNamespace(broker=broker), store=order_store
    )
    app.include_router(router, prefix="/api/v1")
    body = {"client_id": "oq123", "review_id": str(uuid.uuid4())}
    with TestClient(app) as client:
        assert client.post("/api/v1/admin/venue/order-review", json=body).status_code == 401
        response = client.post(
            "/api/v1/admin/venue/order-review", json=body, headers={"Authorization": "Bearer token"}
        )
    assert response.status_code == 200
    assert response.json()["observation"]["observation"] == "unknown"
    assert "private exchange detail" not in response.text
    assert order_store.open_orders()[0]["status"] == "unknown"
