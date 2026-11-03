"""Venue readback observes evidence only and never changes the local ledger."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oq.app.admin_api import router
from oq.app.main import build_app
from oq.data.okx_rest import OkxRest
from oq.domain.position import Position
from oq.execution.okx_adapter import OkxAdapter
from oq.execution.venue_readback import observe_order, observe_protection, readback
from oq.types import Book, D, InstType


def _position() -> Position:
    return Position(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        strategy_id="test",
        qty=D("0.01"),
        avg_px=D("60000"),
        opened_ms=1,
        updated_ms=1,
    )


def _local_order() -> dict:
    return {
        "client_id": "oq123",
        "symbol": "BTC-USDT-SWAP",
        "status": "unknown",
        "side": "buy",
        "qty": "0.01",
        "venue_order_id": "",
    }


def _remote_order(config) -> dict:
    qty = config.instrument("BTC-USDT-SWAP").contracts(D("0.01"))
    return {
        "instId": "BTC-USDT-SWAP",
        "clOrdId": "oq123",
        "ordId": "1234",
        "side": "buy",
        "state": "filled",
        "sz": str(qty),
        "accFillSz": str(qty),
    }


def test_order_readback_reports_observed_fields_without_resolving(config):
    remote = _remote_order(config)
    calls = []

    def order(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return remote

    row = observe_order(SimpleNamespace(order=order), _local_order(), config.instrument_map())
    assert row["local_status"] == "unknown"
    assert row["observation"] == "matching_fields"
    assert row["venue_state"] == "filled" and D(row["venue_filled_base"]) == D("0.01")
    assert "未核实成交明细" in row["note"]
    assert calls == [("BTC-USDT-SWAP", {"client_id": "oq123", "venue_id": None})]


def test_order_missing_mismatched_or_failed_stays_unknown(config):
    mismatch = {**_remote_order(config), "side": "sell"}
    for remote in ({}, mismatch, {**_remote_order(config), "accFillSz": "NaN"}):
        row = observe_order(
            SimpleNamespace(order=lambda *a, value=remote, **k: value),
            _local_order(),
            config.instrument_map(),
        )
        assert row["observation"] == "unknown"
        assert row["venue_state"] is None

    def fail(*args, **kwargs):
        raise TimeoutError("private exception text is not exposed")

    row = observe_order(SimpleNamespace(order=fail), _local_order(), config.instrument_map())
    assert row["observation"] == "unknown"
    assert "private exception" not in str(row)


def test_protection_is_unknown_even_with_an_observed_leg():
    calls = []

    def orders(symbol, *, order_type):
        calls.append(order_type)
        if order_type == "oco":
            return [
                {
                    "instId": symbol,
                    "algoId": "a",
                    "state": "live",
                    "slTriggerPx": "59000",
                    "sz": "1",
                }
            ]
        return []

    result = observe_protection(SimpleNamespace(algo_orders=orders), _position())
    assert calls == ["oco", "conditional"]
    assert result["read_status"] == "observed"
    assert result["protection_status"] == "unknown"
    assert result["legs"][0]["attribution"] == "unknown"


def test_protection_query_failure_does_not_claim_unprotected():
    def orders(symbol, *, order_type):
        if order_type == "conditional":
            raise TimeoutError()
        return []

    result = observe_protection(SimpleNamespace(algo_orders=orders), _position())
    assert result["read_status"] == "unknown" and result["protection_status"] == "unknown"


def test_readback_never_mutates_store(config):
    class Store:
        def open_orders(self):
            return [_local_order()]

        def load_positions(self):
            return [_position()]

    rest = SimpleNamespace(
        order=lambda *a, **k: _remote_order(config),
        algo_orders=lambda *a, **k: [],
    )
    result = readback(Store(), rest, config.instrument_map())
    assert result["read_only"] and not result["ledger_changed"] and not result["reconciled"]


def test_algo_readback_queries_both_order_types_without_private_mutation(config):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": "0", "data": []}, request=request)

    rest = OkxRest(
        config.venues["okx"],
        api_key="k",
        secret="s",
        passphrase="p",
        transport=httpx.MockTransport(handler),
    )
    try:
        rest.algo_orders("BTC-USDT-SWAP", order_type="oco")
        rest.algo_orders("BTC-USDT-SWAP", order_type="conditional")
    finally:
        rest.close()
    assert [r.method for r in requests] == ["GET", "GET"]
    assert [r.url.params["ordType"] for r in requests] == ["oco", "conditional"]


def test_venue_readback_requires_live_adapter(config, tmp_path, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "token")
    cfg = config.model_copy(
        update={
            "root": tmp_path,
            "app": config.app.model_copy(update={"db_path": tmp_path / "test.db", "mode": "paper"}),
        }
    )
    app = build_app(cfg, start_scheduler=False)
    with TestClient(app) as client:
        assert client.post("/api/v1/admin/venue/readback").status_code == 401
        result = client.post(
            "/api/v1/admin/venue/readback", headers={"Authorization": "Bearer token"}
        )
        assert result.status_code == 409


def test_venue_readback_api_observes_only(config, monkeypatch):
    monkeypatch.setenv("OQ_API_TOKEN", "token")
    rest = SimpleNamespace(
        has_credentials=True,
        demo=True,
        order=lambda *a, **k: _remote_order(config),
        algo_orders=lambda *a, **k: [],
    )
    broker = OkxAdapter(config, rest, allow_live=False)
    store = SimpleNamespace(
        open_orders=lambda: [_local_order()], load_positions=lambda: [_position()]
    )
    cfg = config.model_copy(update={"app": config.app.model_copy(update={"mode": "live"})})
    ctx = SimpleNamespace(config=cfg, orchestrator=SimpleNamespace(broker=broker), store=store)
    audits = []
    ctx.audit = lambda *args: audits.append(args)
    app = FastAPI()
    app.state.oq = ctx
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/admin/venue/readback", headers={"Authorization": "Bearer token"}
        )
    assert result.status_code == 200
    body = result.json()
    assert body["venue_environment"] == "demo" and body["reconciled"] is False
    assert body["orders"][0]["venue_state"] == "filled"
    assert body["protection"][0]["protection_status"] == "unknown"
    assert audits[0][1] == "venue.readback"
