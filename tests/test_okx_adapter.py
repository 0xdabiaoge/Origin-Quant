from __future__ import annotations

import pytest

from oq.data.okx_rest import OkxRest
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
