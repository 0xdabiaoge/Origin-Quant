"""Strategy output: an *intent*, never an order.

A strategy states what it wants in risk terms — a stop that invalidates the
idea, an R-multiple ladder, a holding horizon — and the risk engine decides
whether the idea is fundable and at what size.  This separation is the main
structural difference from both reference projects, where the LLM emitted
prices, margin and leverage and the trader executed them almost verbatim.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from oq.types import ONE, ZERO, Action, Book, D, Side, Symbol


@dataclass(frozen=True, slots=True)
class Leg:
    symbol: Symbol
    side: Side
    weight: Decimal = ONE

    def __post_init__(self) -> None:
        if self.weight <= ZERO:
            raise ValueError("leg weight must be positive")


@dataclass(frozen=True, slots=True)
class Intent:
    strategy_id: str
    book: Book
    action: Action
    legs: tuple[Leg, ...]
    ts_ms: int
    stop_px: Decimal | None = None
    tp_ladder: tuple[tuple[Decimal, Decimal], ...] = ()
    time_stop_bars: int | None = None
    max_slippage_bps: int = 10
    risk_pct: Decimal | None = None
    sizing: str = "risk"  # "risk" (stop-distance based) | "notional" (carry legs)
    notional_pct: Decimal | None = None
    ref_px: Mapping[str, Decimal] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("intent needs at least one leg")
        if self.action is Action.OPEN and self.stop_px is None and self.book is Book.DIRECTIONAL:
            raise ValueError("directional entries must carry a stop price")
        if self.stop_px is not None and self.stop_px <= ZERO:
            raise ValueError("stop price must be positive")
        if self.sizing not in {"risk", "notional"}:
            raise ValueError(f"unknown sizing mode {self.sizing!r}")
        if self.sizing == "notional" and (self.notional_pct is None or self.notional_pct <= ZERO):
            raise ValueError("notional sizing needs a positive notional_pct")
        total = sum((leg.weight for leg in self.legs), ZERO)
        if total <= ZERO:
            raise ValueError("leg weights must sum to a positive value")
        for r_multiple, fraction in self.tp_ladder:
            if r_multiple <= ZERO or not (ZERO < fraction <= ONE):
                raise ValueError("invalid take-profit ladder entry")
        if self.time_stop_bars is not None and self.time_stop_bars <= 0:
            raise ValueError("time stop must be positive")

    @property
    def id(self) -> str:
        """Deterministic identity: identical inputs produce identical ids."""
        raw = "|".join(
            [
                self.strategy_id,
                self.book.value,
                self.action.value,
                str(self.ts_ms),
                ",".join(f"{leg.symbol}:{leg.side.value}:{leg.weight}" for leg in self.legs),
                str(self.stop_px or ""),
            ]
        )
        return "int_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "book": self.book.value,
            "action": self.action.value,
            "ts_ms": self.ts_ms,
            "legs": [
                {"symbol": leg.symbol, "side": leg.side.value, "weight": str(leg.weight)}
                for leg in self.legs
            ],
            "stop_px": None if self.stop_px is None else str(self.stop_px),
            "tp_ladder": [[str(r), str(f)] for r, f in self.tp_ladder],
            "time_stop_bars": self.time_stop_bars,
            "sizing": self.sizing,
            "notional_pct": None if self.notional_pct is None else str(self.notional_pct),
            "tags": dict(self.tags),
        }

    def normalized_weights(self) -> tuple[Decimal, ...]:
        total = sum((leg.weight for leg in self.legs), ZERO)
        return tuple(leg.weight / total for leg in self.legs)

    @classmethod
    def open_long(
        cls,
        *,
        strategy_id: str,
        symbol: str,
        ts_ms: int,
        stop_px: Decimal,
        ref_px: Decimal,
        book: Book = Book.DIRECTIONAL,
        tp_ladder: tuple[tuple[Decimal, Decimal], ...] = (),
        time_stop_bars: int | None = None,
        max_slippage_bps: int = 10,
        tags: Mapping[str, str] | None = None,
    ) -> Intent:
        return cls(
            strategy_id=strategy_id,
            book=book,
            action=Action.OPEN,
            legs=(Leg(Symbol(symbol), Side.BUY),),
            ts_ms=ts_ms,
            stop_px=stop_px,
            tp_ladder=tp_ladder,
            time_stop_bars=time_stop_bars,
            max_slippage_bps=max_slippage_bps,
            ref_px={symbol: ref_px},
            tags=dict(tags or {}),
        )

    @classmethod
    def open_short(
        cls,
        *,
        strategy_id: str,
        symbol: str,
        ts_ms: int,
        stop_px: Decimal,
        ref_px: Decimal,
        book: Book = Book.DIRECTIONAL,
        tp_ladder: tuple[tuple[Decimal, Decimal], ...] = (),
        time_stop_bars: int | None = None,
        max_slippage_bps: int = 10,
        tags: Mapping[str, str] | None = None,
    ) -> Intent:
        return cls(
            strategy_id=strategy_id,
            book=book,
            action=Action.OPEN,
            legs=(Leg(Symbol(symbol), Side.SELL),),
            ts_ms=ts_ms,
            stop_px=stop_px,
            tp_ladder=tp_ladder,
            time_stop_bars=time_stop_bars,
            max_slippage_bps=max_slippage_bps,
            ref_px={symbol: ref_px},
            tags=dict(tags or {}),
        )

    @classmethod
    def close_position(
        cls,
        *,
        strategy_id: str,
        symbol: str,
        ts_ms: int,
        book: Book,
        side: Side,
        fraction: Decimal = ONE,
        reason: str = "",
    ) -> Intent:
        """Close (or reduce) an existing position.

        ``side`` is the side of the *closing* trade: sell to close a long.
        Quantity is resolved by the risk engine from the live position, never
        from the strategy — a strategy cannot invent size on an exit.
        """
        return cls(
            strategy_id=strategy_id,
            book=book,
            action=Action.REDUCE if fraction < ONE else Action.CLOSE,
            legs=(Leg(Symbol(symbol), side, fraction),),
            ts_ms=ts_ms,
            tags={"reason": reason, "fraction": str(fraction)},
        )

    @classmethod
    def open_pair(
        cls,
        *,
        strategy_id: str,
        ts_ms: int,
        spot_symbol: str,
        perp_symbol: str,
        notional_pct: Decimal,
        reason: str = "",
    ) -> Intent:
        """Delta-neutral carry entry: buy spot, sell the perpetual."""
        return cls(
            strategy_id=strategy_id,
            book=Book.CARRY,
            action=Action.OPEN,
            legs=(
                Leg(Symbol(spot_symbol), Side.BUY),
                Leg(Symbol(perp_symbol), Side.SELL),
            ),
            ts_ms=ts_ms,
            sizing="notional",
            notional_pct=notional_pct,
            tags={"pair": f"{spot_symbol}|{perp_symbol}", "reason": reason},
        )

    @classmethod
    def close_pair(
        cls,
        *,
        strategy_id: str,
        ts_ms: int,
        spot_symbol: str,
        perp_symbol: str,
        reason: str = "",
    ) -> Intent:
        return cls(
            strategy_id=strategy_id,
            book=Book.CARRY,
            action=Action.CLOSE,
            legs=(
                Leg(Symbol(spot_symbol), Side.SELL),
                Leg(Symbol(perp_symbol), Side.BUY),
            ),
            ts_ms=ts_ms,
            tags={"pair": f"{spot_symbol}|{perp_symbol}", "reason": reason},
        )

    @classmethod
    def move_stop(
        cls,
        *,
        strategy_id: str,
        symbol: str,
        ts_ms: int,
        book: Book,
        new_stop: Decimal,
        reason: str = "",
    ) -> Intent:
        return cls(
            strategy_id=strategy_id,
            book=book,
            action=Action.MOVE_STOP,
            legs=(Leg(Symbol(symbol), Side.BUY),),
            ts_ms=ts_ms,
            stop_px=new_stop,
            tags={"reason": reason},
        )


@dataclass(frozen=True, slots=True)
class LegPlan:
    """A sized, priced leg.  Produced by the risk engine, consumed by execution."""

    symbol: str
    side: Side
    qty: Decimal
    ref_px: Decimal
    notional: Decimal
    stop_px: Decimal | None = None

    def __post_init__(self) -> None:
        if self.qty <= ZERO:
            raise ValueError("leg plan quantity must be positive")
        if self.ref_px <= ZERO:
            raise ValueError("leg plan reference price must be positive")

    @property
    def risk_usd(self) -> Decimal:
        if self.stop_px is None:
            return ZERO
        return abs(self.ref_px - self.stop_px) * self.qty


@dataclass(frozen=True, slots=True)
class GateResult:
    code: str
    passed: bool
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "passed": self.passed, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class RiskDecision:
    intent_id: str
    approved: bool
    legs: tuple[LegPlan, ...]
    gates: tuple[GateResult, ...]
    adjustments: tuple[str, ...] = ()
    action: Action = Action.HOLD
    reasons: tuple[str, ...] = ()

    @property
    def risk_usd(self) -> Decimal:
        return sum((leg.risk_usd for leg in self.legs), ZERO)

    @property
    def notional(self) -> Decimal:
        return sum((leg.notional for leg in self.legs), ZERO)

    def failed_gate(self) -> str | None:
        for gate in self.gates:
            if not gate.passed:
                return gate.code
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "approved": self.approved,
            "action": self.action.value,
            "legs": [
                {
                    "symbol": leg.symbol,
                    "side": leg.side.value,
                    "qty": str(leg.qty),
                    "ref_px": str(leg.ref_px),
                    "notional": str(leg.notional),
                    "stop_px": None if leg.stop_px is None else str(leg.stop_px),
                }
                for leg in self.legs
            ],
            "gates": [gate.as_dict() for gate in self.gates],
            "adjustments": list(self.adjustments),
            "reasons": list(self.reasons),
        }


def r_multiple(entry: Decimal, stop: Decimal, target: Decimal) -> Decimal:
    risk = abs(entry - stop)
    if risk == ZERO:
        return ZERO
    return abs(target - entry) / risk


def intent_from_dict(payload: Mapping[str, object]) -> Intent:  # pragma: no cover - compatibility
    raise NotImplementedError("intents are reconstructed from storage projections, not dicts")


__all__ = [
    "ZERO",
    "D",
    "GateResult",
    "Intent",
    "Leg",
    "LegPlan",
    "RiskDecision",
]
