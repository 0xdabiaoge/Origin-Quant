"""Append-only event definitions.

Events are the audit trail and the replay source.  Every state change that
matters — a decision, a gate rejection, an order, a fill, a funding payment —
is written as an event *in the same transaction* as its projection.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    CYCLE_START = "cycle_start"
    CYCLE_END = "cycle_end"
    DECISION = "decision"
    RISK_DECISION = "risk_decision"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_ACK = "order_ack"
    ORDER_REJECT = "order_reject"
    ORDER_CANCEL = "order_cancel"
    ORDER_UNKNOWN = "order_unknown"
    FILL = "fill"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    STOP_MOVED = "stop_moved"
    TAKE_PROFIT = "take_profit"
    FUNDING = "funding"
    EQUITY = "equity"
    RISK_HALT = "risk_halt"
    KILL_SWITCH = "kill_switch"
    CONFIG = "config"
    RECONCILE = "reconcile"
    BACKTEST = "backtest"
    ERROR = "error"


_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_base32(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_event_id(ts_ms: int | None = None) -> str:
    """ULID-compatible, lexicographically sortable id (26 chars)."""
    ts = int(ts_ms if ts_ms is not None else time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode_base32(ts, 10) + _encode_base32(randomness, 16)


@dataclass(frozen=True, slots=True)
class Event:
    type: EventType
    payload: Mapping[str, Any] = field(default_factory=dict)
    ts_ms: int = 0
    cycle_id: str | None = None
    symbol: str | None = None
    config_hash: str | None = None
    code_version: str | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", new_event_id(self.ts_ms or None))
        if not self.ts_ms:
            object.__setattr__(self, "ts_ms", int(time.time() * 1000))

    def to_row(self) -> tuple[Any, ...]:
        import json

        return (
            self.id,
            self.ts_ms,
            self.cycle_id,
            self.type.value,
            self.symbol,
            json.dumps(self.payload, sort_keys=True, default=str, separators=(",", ":")),
            self.config_hash,
            self.code_version,
        )

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Event:
        import json

        return cls(
            id=row["id"],
            ts_ms=int(row["ts_ms"]),
            cycle_id=row["cycle_id"],
            type=EventType(row["type"]),
            symbol=row["symbol"],
            payload=json.loads(row["payload"]),
            config_hash=row["config_hash"],
            code_version=row["code_version"],
        )
