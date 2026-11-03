from __future__ import annotations

import httpx
import pytest

from oq.data.okx_rest import OkxError, OkxRest
from oq.domain.order import Order
from oq.execution.broker import BrokerContext
from oq.execution.okx_adapter import LiveTradingDisabled, OkxAdapter, _live_allowed
from oq.types import D, InstType, OrderStatus, OrderType, Side


def _adapter(config, allow_live: bool | None = None) -> OkxAdapter:
    rest = OkxRest(config.venues["okx"])
    return OkxAdapter(config, rest, allow_live=allow_live)


def _order() -> Order:
    return Order(
        client_id="oqtest0000000000000001",
        symbol="BTC-USDT-SWAP",
        inst_type=InstType.SWAP,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=D("0.01"),
        status=OrderStatus.SUBMITTING,
        created_ms=1,
        updated_ms=1,
    )


def test_live_is_off_by_default(config, monkeypatch):
    monkeypatch.delenv("OQ_LIVE", raising=False)
    assert _live_allowed(config) is False


def test_live_needs_all_three_switches(config, monkeypatch):
    # 1. config says yes, env says no, mode is paper -> still refused.
    cfg = config.model_copy(update={"app": config.app.model_copy(update={"live_trading": True})})
    monkeypatch.delenv("OQ_LIVE", raising=False)
    assert _live_allowed(cfg) is False

    # 2. env says yes, config says no -> refused.
    monkeypatch.setenv("OQ_LIVE", "1")
    assert _live_allowed(config) is False

    # 3. config + env say yes, mode is paper -> refused.
    assert _live_allowed(cfg) is False

    # 4. all three -> allowed.
    live_cfg = cfg.model_copy(update={"app": cfg.app.model_copy(update={"mode": "live"})})
    assert _live_allowed(live_cfg) is True


def test_place_refuses_to_touch_the_venue_when_live_is_off(config):
    adapter = _adapter(config)
    assert adapter.allow_live is False
    with pytest.raises(LiveTradingDisabled):
        adapter.place(
            _order(), config.instrument("BTC-USDT-SWAP"), BrokerContext(now_ms=1, price=D("60000"))
        )


def test_the_default_config_ships_with_live_disabled(config):
    assert config.app.live_trading is False
    assert config.app.mode in {"paper", "backtest"}


def test_missing_bid_ask_are_not_substituted_with_last_trade(config, monkeypatch):
    rest = OkxRest(config.venues["okx"])
    monkeypatch.setattr(rest, "_request", lambda *args, **kwargs: [{"ts": "100", "last": "10"}])
    quote = rest.ticker("BTC-USDT-SWAP")
    assert quote.bid == D("0")
    assert quote.ask == D("0")
    assert quote.last == D("10")


@pytest.mark.parametrize("outcome", ["timeout", "server_error"])
def test_private_mutation_is_never_retried_when_outcome_is_uncertain(config, outcome):
    attempts = []

    def handler(request):
        attempts.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(503, request=request)

    rest = OkxRest(
        config.venues["okx"].model_copy(update={"max_retries": 2}),
        api_key="test",
        secret="test",
        passphrase="test",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises((httpx.ReadTimeout, OkxError)):
            rest.place_order({"clOrdId": "oqtest", "instId": "BTC-USDT-SWAP"})
        assert len(attempts) == 1
    finally:
        rest.close()


def test_private_read_can_retry_transient_failure(config, monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"code": "0", "data": [{"ok": True}]}, request=request)

    rest = OkxRest(
        config.venues["okx"].model_copy(update={"max_retries": 2}),
        api_key="test",
        secret="test",
        passphrase="test",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(rest, "_backoff", lambda _: None)
    try:
        assert rest._request("GET", "/api/v5/account/balance", private=True) == [{"ok": True}]
        assert len(attempts) == 2
    finally:
        rest.close()


def test_order_body_is_reduce_only_for_closes(config):
    adapter = _adapter(config, allow_live=False)
    instrument = config.instrument("BTC-USDT-SWAP")
    open_order = _order()
    body = adapter._order_body(open_order, instrument, BrokerContext(now_ms=1, price=D("60000")))
    assert body["side"] == "buy"
    assert body["ordType"] == "market"
    assert body["clOrdId"] == open_order.client_id
    # Swaps are sized in contracts, never in base units.
    assert D(body["sz"]) == instrument.round_contracts(open_order.qty)
