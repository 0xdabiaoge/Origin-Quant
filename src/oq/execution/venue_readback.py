"""Bounded, explicitly requested venue observations; never modify orders or ledger.

Readback is evidence for an operator, not an automatic reconciliation. OKX
permits client IDs to be reused after terminal orders; a matching client ID
alone cannot prove historical identity. Protection rows are observations only:
they cannot establish coverage, ownership, trigger validity or future fills.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from oq.types import InstType

MAX_ORDERS = 10
MAX_SYMBOLS = 5
TIME_BUDGET_S = 20
ORDER_STATES = {"live", "partially_filled", "filled", "canceled", "mmp_canceled"}
OPEN_STATUSES = {"submitting", "acked", "partial", "unknown"}


class ReviewConflict(ValueError):
    """The requested local evidence changed or the request ID was reused."""


class ReviewOrderNotFound(ValueError):
    """There is no unresolved local order for the requested ID."""


def _number(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() and number >= 0 else None


def observe_order(rest: Any, order: dict[str, Any], instruments: dict[str, Any]) -> dict[str, Any]:
    """Look up an existing local order, without changing its state or retrying placement."""
    symbol = str(order.get("symbol") or "")
    client_id = str(order.get("client_id") or "")
    result: dict[str, Any] = {
        "symbol": symbol,
        "client_id": client_id,
        "local_status": str(order.get("status") or "unknown"),
        "observation": "unknown",
        "venue_state": None,
        "venue_filled_base": None,
        "note": "只读观察；本地状态和账本未更改。",
    }
    if not symbol or not client_id or symbol not in instruments:
        result["note"] = "本地订单标识或合约信息不完整。"
        return result
    venue_id = str(order.get("venue_order_id") or "")
    try:
        row = rest.order(symbol, client_id=client_id, venue_id=venue_id or None)
    except Exception:
        result["note"] = "交易所查询失败；不能推断订单不存在或未成交。"
        return result
    if not isinstance(row, dict) or not row:
        result["note"] = "交易所未返回可核实的订单；不能推断订单不存在或未成交。"
        return result

    state = str(row.get("state") or "")
    remote_id = str(row.get("ordId") or "")
    instrument = instruments[symbol]
    size = _number(row.get("sz"))
    filled = _number(row.get("accFillSz"))
    local_size = _number(order.get("qty"))
    if (
        row.get("instId") != symbol
        or row.get("clOrdId") != client_id
        or not remote_id
        or (venue_id and remote_id != venue_id)
        or row.get("side") != order.get("side")
        or state not in ORDER_STATES
        or size is None
        or filled is None
        or local_size is None
        or filled > size
    ):
        result["note"] = "交易所订单字段缺失或与本地不一致；需人工核对。"
        return result
    base_size = instrument.base_from_contracts(size) if instrument.is_swap else size
    if base_size != local_size:
        result["note"] = "交易所订单数量与本地不一致；需人工核对单位和合约信息。"
        return result
    base_filled = instrument.base_from_contracts(filled) if instrument.is_swap else filled
    result.update(
        observation="matching_fields",
        venue_order_id=remote_id,
        venue_state=state,
        venue_filled_base=str(base_filled),
        note="字段匹配仅为观察；client ID 可能复用，未核实成交明细或更新账本。",
    )
    return result


def observe_protection(
    rest: Any, position: Any, *, deadline: float | None = None
) -> dict[str, Any]:
    """List stop-leg fields; never infer that a local position is protected."""
    result: dict[str, Any] = {
        "symbol": position.symbol,
        "book": position.book.value,
        "local_qty_base": str(position.qty),
        "read_status": "unknown",
        "protection_status": "unknown",
        "legs": [],
        "note": "未核实交易所实际仓位、归属和全部保护覆盖；不能据此认定已保护。",
    }
    if position.inst_type is not InstType.SWAP:
        result["note"] = "仅检查永续合约条件单；现货保护状态未知。"
        return result
    rows: list[dict] = []
    truncated = False
    for kind in ("oco", "conditional"):
        if deadline is not None and time.monotonic() >= deadline:
            result["note"] = "查询时间预算已用尽；保护状态未知。"
            return result
        try:
            page = rest.algo_orders(position.symbol, order_type=kind)
        except Exception:
            result["note"] = "交易所保护单查询失败；不能认定没有保护单或已受保护。"
            return result
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            result["note"] = "交易所保护单响应无效；保护状态未知。"
            return result
        truncated = truncated or len(page) >= 100
        rows.extend(page)
    for row in rows:
        if row.get("instId") != position.symbol or not row.get("slTriggerPx"):
            continue
        result["legs"].append(
            {
                "algo_id": str(row.get("algoId") or ""),
                "state": str(row.get("state") or "unknown"),
                "side": str(row.get("side") or "unknown"),
                "position_side": str(row.get("posSide") or "unknown"),
                "stop_trigger": str(row.get("slTriggerPx") or ""),
                "trigger_type": str(row.get("slTriggerPxType") or "unknown"),
                "venue_size": str(row.get("sz") or "unknown"),
                "size_unit": "contracts",
                "attribution": "unknown",
            }
        )
    result["read_status"] = "partial" if truncated else "observed"
    return result


def readback(store: Any, rest: Any, instruments: dict[str, Any]) -> dict[str, Any]:
    """Fetch bounded observations, returning no claim that the venue is reconciled."""
    orders = store.open_orders()
    positions = store.load_positions()
    chosen_positions = positions[:MAX_SYMBOLS]
    deadline = time.monotonic() + TIME_BUDGET_S
    observed_orders = []
    for order in orders[:MAX_ORDERS]:
        if time.monotonic() >= deadline:
            observed_orders.append(
                {
                    "symbol": str(order.get("symbol") or ""),
                    "client_id": str(order.get("client_id") or ""),
                    "local_status": str(order.get("status") or "unknown"),
                    "observation": "unknown",
                    "venue_state": None,
                    "venue_filled_base": None,
                    "note": "查询时间预算已用尽，未查询此订单。",
                }
            )
        else:
            observed_orders.append(observe_order(rest, order, instruments))
    protection = [
        observe_protection(rest, position, deadline=deadline) for position in chosen_positions
    ]
    return {
        "observed_ms": int(time.time() * 1000),
        "read_only": True,
        "ledger_changed": False,
        "reconciled": False,
        "orders_truncated": len(orders) > MAX_ORDERS,
        "positions_truncated": len(positions) > MAX_SYMBOLS,
        "time_budget_exceeded": time.monotonic() >= deadline,
        "orders": observed_orders,
        "protection": protection,
    }


def record_order_review(
    store: Any,
    rest: Any,
    instruments: dict[str, Any],
    *,
    actor: str,
    client_id: str,
    review_id: str,
) -> dict[str, Any]:
    """Persist a specific readback for an operator without resolving the order.

    The current account/environment is not bound to historical orders, and
    order snapshots alone do not prove complete fills. This records evidence
    and never changes orders, positions, fills or the live-entry block. The
    review ID makes retries idempotent; reuse for another order is rejected.
    """
    key = f"venue.order_review.{review_id}"
    existing = store.get_kv(key)
    if isinstance(existing, dict):
        if existing.get("client_id") != client_id or existing.get("actor") != actor:
            raise ReviewConflict("review ID already used")
        return existing
    row = store.db.query_one("SELECT * FROM orders WHERE client_id=?", (client_id,))
    if row is None or row["status"] not in OPEN_STATUSES:
        raise ReviewOrderNotFound("unresolved order not found")
    original = dict(row)
    observation = observe_order(rest, original, instruments)
    record = {
        "review_id": review_id,
        "actor": actor,
        "client_id": client_id,
        "observed_ms": int(time.time() * 1000),
        "venue_environment": "demo" if rest.demo else "live",
        "account_provenance_verified": False,
        "observation": observation,
        "ledger_changed": False,
        "reconciled": False,
        "entry_block_released": False,
        "note": "证据已记录，账户归属及成交明细未核实；订单状态和开仓限制均未改变。",
    }
    with store.db.tx() as conn:
        prior = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if prior is not None:
            try:
                saved = json.loads(prior["value"])
            except (ValueError, TypeError) as exc:
                raise ReviewConflict("review record is invalid") from exc
            if (
                not isinstance(saved, dict)
                or saved.get("client_id") != client_id
                or saved.get("actor") != actor
            ):
                raise ReviewConflict("review ID already used")
            return saved
        current = conn.execute("SELECT * FROM orders WHERE client_id=?", (client_id,)).fetchone()
        if current is None or dict(current) != original:
            raise ReviewConflict("local order changed while querying")
        store.set_kv(conn, key, record)
        store.audit(conn, actor, "venue.order_review", record)
    return record
