from __future__ import annotations

from decimal import Decimal

import pytest

from oq.types import (
    Action,
    Book,
    D,
    Instrument,
    InstType,
    OrderStatus,
    Side,
    check_transition,
    q_down,
    q_nearest,
    q_up,
)


def test_quantizers_never_round_risk_up():
    assert q_down(D("1.234"), D("0.01")) == D("1.23")
    assert q_down(D("-1.234"), D("0.01")) == D("-1.23")  # sign is preserved; callers abs() first
    assert q_up(D("1.231"), D("0.01")) == D("1.24")
    assert q_nearest(D("1.235"), D("0.01")) == D("1.24")


def test_instrument_rounds_down_to_lot(btc: Instrument):
    assert btc.round_qty(D("0.00019")) == D("0.0001")
    assert btc.round_qty(D("0.00009")) == D("0")
    assert btc.round_px(D("62000.07")) == D("62000.1")


def test_contracts_round_trip(btc: Instrument):
    qty = D("0.0123")
    assert btc.contracts(qty) == D("1.23")  # raw ratio; the lot grid is applied by round_qty
    assert btc.notional(qty, D("60000")) == D("738")
    assert btc.contract_lot == D("0.01")  # 0.0001 base lot / 0.01 ctVal
    assert btc.round_contracts(D("0.01234")) == D("1.23")
    assert btc.base_from_contracts(D("1.23")) == D("0.0123")


def test_fee_uses_absolute_notional(btc: Instrument):
    assert btc.fee(
        D("-1000"), liquidity=__import__("oq.types", fromlist=["Liquidity"]).Liquidity.TAKER
    ) == D("0.5")


def test_order_state_machine_refuses_illegal_jumps():
    check_transition(OrderStatus.SUBMITTING, OrderStatus.ACKED)
    check_transition(OrderStatus.ACKED, OrderStatus.PARTIAL)
    with pytest.raises(ValueError):
        check_transition(OrderStatus.FILLED, OrderStatus.SUBMITTING)
    with pytest.raises(ValueError):
        check_transition(OrderStatus.CANCELED, OrderStatus.FILLED)


def test_unknown_is_reachable_from_every_live_state():
    for status in (OrderStatus.SUBMITTING, OrderStatus.ACKED, OrderStatus.PARTIAL):
        check_transition(status, OrderStatus.UNKNOWN)


def test_enum_values_are_stable():
    assert Side.BUY.value == "buy"
    assert Book.CARRY.value == "carry"
    assert Action.MOVE_STOP.value == "move_stop"
    assert InstType.SWAP.value == "SWAP"


def test_bps_and_pct():
    from oq.types import bps, pct

    assert bps(8) == D("0.0008")  # 8 bps as a fraction
    assert pct(25) == D("0.25")  # 25 percent as a fraction


def test_decimal_from_float_is_never_binary_noise():
    assert D(0.1) == Decimal("0.1")
    assert D(1e-8) == Decimal("1E-8")
    assert D("0.1") + D("0.2") == D("0.3")
