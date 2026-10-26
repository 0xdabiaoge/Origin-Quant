"""Core value types.

Two invariants live here and are enforced by tests:

1. Money and quantity are :class:`~decimal.Decimal`, never ``float``.
   Binary floats cannot represent 0.1 tick sizes and silently accumulate
   rounding error across thousands of fills.  Conversion is allowed only at
   the edges (exchange payloads, CSV, JSON for humans).

2. Time is an integer number of milliseconds since the Unix epoch, UTC.
   No naive datetimes, no local time, no ``datetime.now()`` inside the
   decision kernel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any, NewType

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)

Symbol = NewType("Symbol", str)
Ms = NewType("Ms", int)

_DECIMAL_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY

    @classmethod
    def parse(cls, raw: str) -> Side:
        value = str(raw).strip().lower()
        if value in {"buy", "bid", "long", "b"}:
            return cls.BUY
        if value in {"sell", "ask", "short", "s"}:
            return cls.SELL
        raise ValueError(f"unknown side: {raw!r}")


class PosSide(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is PosSide.LONG else -1

    @classmethod
    def from_side(cls, side: Side) -> PosSide:
        return cls.LONG if side is Side.BUY else cls.SHORT


class Book(str, Enum):
    """Capital book.  Carry and directional risk are never netted together."""

    CARRY = "carry"
    DIRECTIONAL = "directional"


class Action(str, Enum):
    OPEN = "open"
    CLOSE = "close"
    REDUCE = "reduce"
    MOVE_STOP = "move_stop"
    HOLD = "hold"


class InstType(str, Enum):
    SPOT = "SPOT"
    SWAP = "SWAP"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"
    POST_ONLY = "post_only"


class TimeInForce(str, Enum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    """Order lifecycle.

    ``UNKNOWN`` is a first-class state, not an error.  It means "we sent
    something and the venue did not confirm; the truth must be recovered by
    reconciliation".  Treating it as failed is how duplicates are born.
    """

    INTENT = "intent"
    RISK_REJECTED = "risk_rejected"
    SUBMITTING = "submitting"
    ACKED = "acked"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.RISK_REJECTED,
        }

    @property
    def live(self) -> bool:
        return self in {
            OrderStatus.SUBMITTING,
            OrderStatus.ACKED,
            OrderStatus.PARTIAL,
            OrderStatus.UNKNOWN,
        }


class Liquidity(str, Enum):
    MAKER = "maker"
    TAKER = "taker"


_ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.INTENT: {OrderStatus.RISK_REJECTED, OrderStatus.SUBMITTING, OrderStatus.REJECTED},
    OrderStatus.RISK_REJECTED: set(),
    OrderStatus.SUBMITTING: {
        OrderStatus.ACKED,
        OrderStatus.REJECTED,
        OrderStatus.UNKNOWN,
        OrderStatus.CANCELED,
    },
    OrderStatus.ACKED: {
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.PARTIAL: {
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.UNKNOWN: {
        OrderStatus.ACKED,
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.REJECTED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELED: set(),
    OrderStatus.REJECTED: set(),
}


def check_transition(current: OrderStatus, new: OrderStatus) -> None:
    if new is current:
        return
    if new not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"illegal order transition {current.value} -> {new.value}")


def D(value: Any) -> Decimal:
    """Convert to Decimal without ever passing through a binary float.

    ``Decimal(0.1)`` is 0.1000000000000000055511151231257827; ``D(0.1)``
    is 0.1.  Every external payload goes through this function.
    """
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return ZERO
    if isinstance(value, bool):
        raise TypeError("bool is not a number")
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, int):
        return Decimal(value)
    text = str(value).strip()
    text = text.replace(",", "").replace("_", "")
    if text in {"", "-", "nan", "NaN", "None", "null"}:
        return ZERO
    if not _DECIMAL_RE.match(text):
        try:
            return Decimal(text)
        except InvalidOperation as exc:  # pragma: no cover - defensive
            raise ValueError(f"not a decimal: {value!r}") from exc
    return Decimal(text)


def q_down(value: Decimal, step: Decimal) -> Decimal:
    """Round toward zero to the nearest multiple of ``step``."""
    if step <= ZERO:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def q_up(value: Decimal, step: Decimal) -> Decimal:
    if step <= ZERO:
        return value
    magnitude = abs(value)
    rounded = (magnitude / step).to_integral_value(rounding=ROUND_UP) * step
    return rounded if value >= ZERO else -rounded


def q_nearest(value: Decimal, step: Decimal) -> Decimal:
    if step <= ZERO:
        return value
    return (value / step).to_integral_value(rounding=ROUND_HALF_UP) * step


def bps(n: float | Decimal) -> Decimal:
    """N basis points as a fraction (8 bps -> 0.0008)."""
    return D(n) / D(10_000)


def pct(n: float | Decimal) -> Decimal:
    return D(n) / HUNDRED


def safe_div(num: Decimal, den: Decimal, default: Decimal = ZERO) -> Decimal:
    if den == ZERO:
        return default
    return num / den


@dataclass(frozen=True, slots=True)
class Instrument:
    """Tradable contract metadata.

    ``contract_value`` is the base-currency amount per contract for swaps and
    ``1`` for spot.  ``lot_size`` is the minimum size increment expressed in
    **base units** (not contracts), so sizing code never has to remember which
    unit a venue wants.
    """

    symbol: str
    inst_type: InstType
    base: str
    quote: str
    tick_size: Decimal
    lot_size: Decimal
    min_size: Decimal
    contract_value: Decimal = ONE
    maker_fee: Decimal = D("0.0002")
    taker_fee: Decimal = D("0.0005")
    max_leverage: Decimal = D("10")

    @property
    def is_swap(self) -> bool:
        return self.inst_type is InstType.SWAP

    def notional(self, qty_base: Decimal, price: Decimal) -> Decimal:
        return abs(qty_base) * price

    def contracts(self, qty_base: Decimal) -> Decimal:
        if not self.is_swap:
            return qty_base
        return qty_base / self.contract_value

    def base_from_contracts(self, contracts: Decimal) -> Decimal:
        if not self.is_swap:
            return contracts
        return contracts * self.contract_value

    def round_qty(self, qty_base: Decimal) -> Decimal:
        """Round **down** to a tradable size.  Never round risk up."""
        return q_down(abs(qty_base), self.lot_size)

    @property
    def contract_lot(self) -> Decimal:
        """Contract increment implied by ``lot_size``.

        OKX sizes swaps in contracts, not base units.  Keeping the base-unit
        lot as the single source of truth and deriving the contract lot here
        means sizing, rounding and the venue payload can never disagree.
        """
        if not self.is_swap:
            return ZERO
        return self.lot_size / self.contract_value

    def round_contracts(self, qty_base: Decimal) -> Decimal:
        if not self.is_swap:
            return abs(qty_base)
        lot = self.contract_lot
        if lot <= ZERO:
            return self.contracts(abs(qty_base))
        return q_down(self.contracts(abs(qty_base)), lot)

    def round_px(self, price: Decimal) -> Decimal:
        return q_nearest(price, self.tick_size)

    def fee(self, notional: Decimal, liquidity: Liquidity) -> Decimal:
        rate = self.maker_fee if liquidity is Liquidity.MAKER else self.taker_fee
        return abs(notional) * rate

    def __post_init__(self) -> None:
        if self.tick_size <= ZERO or self.lot_size <= ZERO or self.min_size <= ZERO:
            raise ValueError(f"instrument {self.symbol} has non-positive increments")
        if self.contract_value <= ZERO:
            raise ValueError(f"instrument {self.symbol} has non-positive contract value")


def utc_ms(ts: float) -> int:
    return int(ts * 1000)


def fmt_dec(value: Decimal, places: int = 8) -> str:
    """Stable string form for JSON and venue payloads (no exponent)."""
    quant = Decimal(1).scaleb(-places)
    text = format(value.quantize(quant), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))
