"""Position sizing.

One formula for directional risk::

    qty = equity * risk_pct / |entry - stop|

and one for carry::

    qty = equity * notional_pct / price

Both are then rounded **down** and capped.  A cap may only reduce size; no
rule in this module can increase it.  Every cap that binds is recorded as an
adjustment string so the decision log explains the final number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from oq.config import Config
from oq.domain.intent import Intent, LegPlan
from oq.risk.context import RiskContext
from oq.types import ONE, ZERO, Action, D, Instrument, InstType, Side, safe_div


@dataclass(slots=True)
class SizingResult:
    legs: list[LegPlan] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)
    failure: str | None = None
    instruments: dict[str, Instrument] = field(default_factory=dict)


def risk_scale(ctx: RiskContext, config: Config) -> Decimal:
    """Weekly drawdown halves risk.  Losing streaks reduce exposure, not increase it."""
    if ctx.portfolio.week_pnl(ctx.marks) <= -config.risk.weekly_loss_halve_pct:
        return D("0.5")
    return ONE


def build_legs(
    intent: Intent,
    ctx: RiskContext,
    config: Config,
    instruments: dict[str, Instrument],
) -> SizingResult:
    result = SizingResult(instruments=instruments)
    if intent.action is Action.MOVE_STOP:
        return result

    equity = ctx.equity
    if equity <= ZERO:
        result.failure = "equity_non_positive"
        return result

    if intent.action in {Action.CLOSE, Action.REDUCE}:
        return _exit_legs(intent, ctx, config, instruments, result)

    if intent.sizing == "notional":
        return _notional_legs(intent, ctx, config, instruments, result)
    return _risk_legs(intent, ctx, config, instruments, result)


def _exit_legs(
    intent: Intent,
    ctx: RiskContext,
    config: Config,
    instruments: dict[str, Instrument],
    result: SizingResult,
) -> SizingResult:
    for leg in intent.legs:
        position = ctx.portfolio.get(leg.symbol, intent.book)
        if position is None:
            result.failure = f"no_position:{leg.symbol}"
            return result
        instrument = instruments.get(leg.symbol)
        if instrument is None:
            result.failure = f"unknown_instrument:{leg.symbol}"
            return result
        # An exit may only shrink a position.  If the requested side would add
        # to it, the intent is stale or wrong — refuse rather than flip.
        if (position.is_long and leg.side is Side.BUY) or (
            not position.is_long and leg.side is Side.SELL
        ):
            result.failure = f"wrong_close_side:{leg.symbol}"
            return result
        fraction = min(ONE, leg.weight)
        if intent.action is Action.REDUCE:
            target = position.initial_qty * fraction
            qty = min(position.abs_qty, target)
        else:
            qty = position.abs_qty * fraction
        qty = instrument.round_qty(qty)
        if qty < instrument.min_size:
            # A partial exit smaller than the minimum is not executable; close
            # the whole remainder instead of leaving an unclosable stub.
            qty = instrument.round_qty(position.abs_qty)
        if qty <= ZERO:
            # Legacy dust below one lot: allow the accounting close.  The venue
            # adapter refuses to send it, and reconciliation flags it.
            qty = position.abs_qty
            result.adjustments.append(f"sub_lot_close:{leg.symbol}")
        price = ctx.price(leg.symbol) or position.avg_px
        result.legs.append(
            LegPlan(
                symbol=leg.symbol,
                side=leg.side,
                qty=qty,
                ref_px=instrument.round_px(price),
                notional=qty * price,
                stop_px=None,
            )
        )
    return result


def _risk_legs(
    intent: Intent,
    ctx: RiskContext,
    config: Config,
    instruments: dict[str, Instrument],
    result: SizingResult,
) -> SizingResult:
    if intent.stop_px is None:
        result.failure = "missing_stop"
        return result
    primary = intent.legs[0]
    instrument = instruments.get(primary.symbol)
    if instrument is None:
        result.failure = f"unknown_instrument:{primary.symbol}"
        return result
    price = ctx.price(primary.symbol)
    if price <= ZERO:
        result.failure = f"no_price:{primary.symbol}"
        return result
    stop_distance = abs(price - intent.stop_px)
    if stop_distance <= ZERO:
        result.failure = "zero_stop_distance"
        return result

    risk_pct = intent.risk_pct if intent.risk_pct is not None else config.risk.risk_per_trade_pct
    risk_amount = ctx.equity * risk_pct * risk_scale(ctx, config)
    if risk_amount <= ZERO:
        result.failure = "risk_budget_zero"
        return result

    weights = intent.normalized_weights()
    for leg, weight in zip(intent.legs, weights, strict=True):
        leg_instrument = instruments.get(leg.symbol)
        leg_price = ctx.price(leg.symbol)
        if leg_instrument is None or leg_price <= ZERO:
            result.failure = f"bad_leg:{leg.symbol}"
            return result
        qty = risk_amount / stop_distance * weight
        qty = _apply_caps(
            qty, leg_instrument, leg_price, intent, ctx, config, result, is_exit=False
        )
        if qty <= ZERO:
            result.failure = f"capped_to_zero:{leg.symbol}"
            return result
        result.legs.append(
            LegPlan(
                symbol=leg.symbol,
                side=leg.side,
                qty=qty,
                ref_px=leg_price,
                notional=qty * leg_price,
                stop_px=intent.stop_px,
            )
        )
    return _apply_portfolio_caps(result, ctx, config)


def _notional_legs(
    intent: Intent,
    ctx: RiskContext,
    config: Config,
    instruments: dict[str, Instrument],
    result: SizingResult,
) -> SizingResult:
    if intent.notional_pct is None:
        result.failure = "missing_notional_pct"
        return result
    notional_budget = ctx.equity * intent.notional_pct
    weights = intent.normalized_weights()
    for leg, weight in zip(intent.legs, weights, strict=True):
        instrument = instruments.get(leg.symbol)
        price = ctx.price(leg.symbol)
        if instrument is None or price <= ZERO:
            result.failure = f"bad_leg:{leg.symbol}"
            return result
        qty = notional_budget * weight / price
        qty = _apply_caps(qty, instrument, price, intent, ctx, config, result, is_exit=False)
        if qty <= ZERO:
            result.failure = f"capped_to_zero:{leg.symbol}"
            return result
        result.legs.append(
            LegPlan(
                symbol=leg.symbol,
                side=leg.side,
                qty=qty,
                ref_px=price,
                notional=qty * price,
                stop_px=None,
            )
        )
    return _apply_portfolio_caps(result, ctx, config)


def _apply_caps(
    qty: Decimal,
    instrument: Instrument,
    price: Decimal,
    intent: Intent,
    ctx: RiskContext,
    config: Config,
    result: SizingResult,
    *,
    is_exit: bool,
) -> Decimal:
    if is_exit:
        return instrument.round_qty(qty)

    # Per-order notional ceiling.
    max_order_notional = ctx.equity * config.risk.max_order_notional_pct
    notional = qty * price
    if notional > max_order_notional > ZERO:
        qty = max_order_notional / price
        result.adjustments.append(f"order_notional_cap:{intent.legs[0].symbol}")

    # Per-symbol exposure ceiling, net of what is already held.
    max_symbol = ctx.equity * config.risk.max_symbol_exposure_pct
    existing = ctx.portfolio.exposure_by_symbol(ctx.marks).get(intent.legs[0].symbol, ZERO)
    if existing + qty * price > max_symbol:
        room = max(ZERO, max_symbol - existing)
        qty = min(qty, room / price)
        result.adjustments.append(f"symbol_exposure_cap:{intent.legs[0].symbol}")

    qty = instrument.round_qty(qty)
    if qty < instrument.min_size:
        qty = instrument.round_qty(instrument.min_size)
        if qty < instrument.min_size:
            return ZERO
    return qty


def _apply_portfolio_caps(result: SizingResult, ctx: RiskContext, config: Config) -> SizingResult:
    if not result.legs:
        return result
    equity = ctx.equity
    instruments = result.instruments

    def rebuild(plans: list[LegPlan]) -> list[LegPlan]:
        """Re-align to lot size after any proportional scaling.

        Skipping this step is how a position becomes unclosable dust: the
        venue rejects sub-lot orders and the residual can never be flattened.
        """
        out: list[LegPlan] = []
        for plan in plans:
            instrument = instruments.get(plan.symbol)
            if instrument is None:
                continue
            qty = instrument.round_qty(plan.qty)
            if qty < instrument.min_size or qty <= ZERO:
                continue
            out.append(
                LegPlan(
                    symbol=plan.symbol,
                    side=plan.side,
                    qty=qty,
                    ref_px=plan.ref_px,
                    notional=qty * plan.ref_px,
                    stop_px=plan.stop_px,
                )
            )
        return out

    # Cluster ceiling.
    cluster_totals: dict[str, Decimal] = {}
    for position in ctx.portfolio.positions.values():
        cluster = ctx.clusters.get(position.symbol)
        if cluster:
            cluster_totals[cluster] = cluster_totals.get(cluster, ZERO) + position.notional(
                ctx.price(position.symbol) or position.avg_px
            )
    scaled: list[LegPlan] = []
    for plan in result.legs:
        cluster = ctx.clusters.get(plan.symbol)
        if cluster:
            cap = equity * config.risk.max_cluster_exposure_pct
            used = cluster_totals.get(cluster, ZERO)
            if used + plan.notional > cap:
                room = max(ZERO, cap - used)
                scale = safe_div(room, plan.notional, ZERO)
                if scale < ONE:
                    result.adjustments.append(f"cluster_cap:{cluster}")
                    cluster_totals[cluster] = used + plan.notional * scale
                    scaled.append(
                        LegPlan(
                            symbol=plan.symbol,
                            side=plan.side,
                            qty=plan.qty * scale,
                            ref_px=plan.ref_px,
                            notional=plan.notional * scale,
                            stop_px=plan.stop_px,
                        )
                    )
                    continue
        cluster_totals[cluster or ""] = cluster_totals.get(cluster or "", ZERO) + plan.notional
        scaled.append(plan)

    # Gross leverage ceiling.
    gross_now = ctx.portfolio.gross_notional(ctx.marks)
    gross_new = sum((plan.notional for plan in scaled), ZERO)
    cap = equity * config.risk.max_gross_leverage
    if cap > ZERO and gross_now + gross_new > cap:
        room = max(ZERO, cap - gross_now)
        scale = safe_div(room, gross_new, ONE)
        if scale < ONE:
            result.adjustments.append("gross_leverage_cap")
            scaled = [
                LegPlan(
                    symbol=plan.symbol,
                    side=plan.side,
                    qty=plan.qty * scale,
                    ref_px=plan.ref_px,
                    notional=plan.notional * scale,
                    stop_px=plan.stop_px,
                )
                for plan in scaled
            ]

    aligned = rebuild(scaled)
    if len(aligned) != len(result.legs):
        # Dropping one leg of a hedge would leave naked directional exposure.
        result.failure = "sized_below_minimum"
        return result

    # Margin availability: spot legs consume cash, swaps consume notional/leverage.
    margin_new = ZERO
    for plan in aligned:
        if plan.symbol.endswith("-SWAP"):
            margin_new += plan.notional / D(3)
        else:
            margin_new += plan.notional
    if ctx.portfolio.margin_used(ctx.marks) + margin_new > equity:
        result.adjustments.append("margin_cap")
        result.failure = "insufficient_margin"
        return result

    result.legs = aligned
    return result


__all__ = ["InstType", "SizingResult", "build_legs", "risk_scale"]
