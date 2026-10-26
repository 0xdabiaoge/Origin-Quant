"""Ordered risk gates.

The pipeline is deliberately boring: a list of named predicates, evaluated in
order, short-circuiting on the first failure, every result recorded.  That is
what makes a rejected trade explainable six weeks later.

Two asymmetries matter:

* **Exits bypass entry gates.**  A closed position is always risk-reducing;
  blocking it because the market data is stale or the daily loss limit was
  hit is how a small loss becomes an uncontrolled one.
* **Gates never enlarge.**  Sizing caps can only shrink a plan, and the
  adjustment is named in the decision.
"""

from __future__ import annotations

from oq.config import Config
from oq.domain.intent import GateResult, Intent, LegPlan, RiskDecision
from oq.risk.context import RiskContext
from oq.risk.sizing import build_legs
from oq.types import ONE, ZERO, Action, Book, D, Instrument, safe_div


class RiskEngine:
    def __init__(self, config: Config, instruments: dict[str, Instrument]) -> None:
        self.config = config
        self.instruments = instruments
        self.risk = config.risk

    # -- public --------------------------------------------------------
    def evaluate(self, intent: Intent, ctx: RiskContext) -> RiskDecision:
        if intent.action is Action.MOVE_STOP:
            return self._evaluate_stop_move(intent, ctx)

        sizing = build_legs(intent, ctx, self.config, self.instruments)
        if sizing.failure:
            gate = GateResult("sizing", False, sizing.failure)
            return RiskDecision(
                intent_id=intent.id,
                approved=False,
                legs=tuple(sizing.legs),
                gates=(gate,),
                adjustments=tuple(sizing.adjustments),
                action=intent.action,
                reasons=(sizing.failure,),
            )

        if intent.action in {Action.CLOSE, Action.REDUCE}:
            gates = self._structural_gates(intent, sizing.legs, ctx)
        else:
            gates = self._entry_gates(intent, sizing.legs, ctx)

        approved = all(gate.passed for gate in gates)
        reasons = tuple(gate.reason for gate in gates if not gate.passed)
        return RiskDecision(
            intent_id=intent.id,
            approved=approved,
            legs=tuple(sizing.legs),
            gates=tuple(gates),
            adjustments=tuple(sizing.adjustments),
            action=intent.action,
            reasons=reasons,
        )

    # -- gate sets -----------------------------------------------------
    def _structural_gates(
        self, intent: Intent, legs: list[LegPlan], ctx: RiskContext
    ) -> list[GateResult]:
        results = [GateResult("sizing", True)]
        for plan in legs:
            if plan.symbol not in self.instruments:
                results.append(GateResult("instrument", False, plan.symbol))
                return results
        results.append(GateResult("instrument", True))
        return results

    def _entry_gates(
        self, intent: Intent, legs: list[LegPlan], ctx: RiskContext
    ) -> list[GateResult]:
        checks = [
            self._gate_kill_switch,
            self._gate_halt,
            self._gate_data,
            self._gate_universe,
            self._gate_spread,
            self._gate_cooldown,
            self._gate_stop_sanity,
            self._gate_rr,
            self._gate_cost_edge,
            self._gate_positions,
            self._gate_heat,
            self._gate_drawdown,
            self._gate_leverage,
        ]
        results: list[GateResult] = []
        for check in checks:
            result = check(intent, legs, ctx)
            results.append(result)
            if not result.passed:
                break
        return results

    # -- gates ---------------------------------------------------------
    def _gate_kill_switch(
        self, intent: Intent, legs: list[LegPlan], ctx: RiskContext
    ) -> GateResult:
        if ctx.kill_switch:
            return GateResult("kill_switch", False, "kill switch engaged")
        return GateResult("kill_switch", True)

    def _gate_halt(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        if ctx.trading_halted:
            return GateResult("trading_halt", False, ctx.halt_reason or "trading halted")
        return GateResult("trading_halt", True)

    def _gate_data(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        for plan in legs:
            if not ctx.is_fresh(plan.symbol):
                return GateResult("data_fresh", False, f"stale data: {plan.symbol}")
            if ctx.price(plan.symbol) <= ZERO:
                return GateResult("data_fresh", False, f"no price: {plan.symbol}")
        return GateResult("data_fresh", True)

    def _gate_universe(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        universe = self.config.universe_map()
        for plan in legs:
            item = universe.get(plan.symbol)
            if item is None:
                return GateResult("universe", False, f"not in universe: {plan.symbol}")
            if not item.allows(intent.book):
                return GateResult(
                    "universe",
                    False,
                    f"book mismatch: {plan.symbol} allows {[b.value for b in item.books]}",
                )
        return GateResult("universe", True)

    def _gate_spread(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        limit = D(self.config.data.max_spread_bps)
        for plan in legs:
            quote = ctx.quotes.get(plan.symbol)
            if quote is None:
                continue
            if quote.spread_bps > limit:
                return GateResult(
                    "spread", False, f"{plan.symbol} spread {quote.spread_bps:.2f}bps > {limit}bps"
                )
        return GateResult("spread", True)

    def _gate_cooldown(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        for plan in legs:
            if ctx.in_cooldown(plan.symbol, intent.book):
                return GateResult("cooldown", False, f"{plan.symbol} in stop cooldown")
        return GateResult("cooldown", True)

    def _gate_stop_sanity(
        self, intent: Intent, legs: list[LegPlan], ctx: RiskContext
    ) -> GateResult:
        if intent.book is Book.CARRY or intent.stop_px is None:
            return GateResult("stop_sanity", True)
        if not legs:
            return GateResult("stop_sanity", False, "no sized legs")
        plan = legs[0]
        price = plan.ref_px
        stop = intent.stop_px
        long_side = plan.side.value == "buy"
        if long_side and stop >= price:
            return GateResult("stop_sanity", False, "long stop is not below entry")
        if not long_side and stop <= price:
            return GateResult("stop_sanity", False, "short stop is not above entry")

        distance = abs(price - stop)
        distance_pct = safe_div(distance, price)
        if distance_pct < D("0.001") or distance_pct > D("0.15"):
            return GateResult(
                "stop_sanity", False, f"stop distance {distance_pct:.4%} outside [0.1%, 15%]"
            )

        atr_value = ctx.atr_of(plan.symbol)
        if atr_value <= ZERO:
            return GateResult("stop_sanity", False, f"no ATR for {plan.symbol}")
        low_bound = self.risk.min_stop_atr_mult * atr_value
        high_bound = self.risk.max_stop_atr_mult * atr_value
        if distance < low_bound or distance > high_bound:
            return GateResult(
                "stop_sanity",
                False,
                f"stop {distance:.6g} outside [{low_bound:.6g}, {high_bound:.6g}] ATR",
            )
        return GateResult("stop_sanity", True)

    def _gate_rr(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        if intent.book is Book.CARRY or not intent.tp_ladder:
            return GateResult("risk_reward", True)
        first_r = intent.tp_ladder[0][0]
        if first_r < self.risk.min_rr:
            return GateResult(
                "risk_reward", False, f"first target {first_r}R < {self.risk.min_rr}R"
            )
        return GateResult("risk_reward", True)

    def _gate_cost_edge(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        cost = ZERO
        notional = ZERO
        for plan in legs:
            instrument = self.instruments[plan.symbol]
            notional += plan.notional
            cost += plan.notional * (instrument.taker_fee * D(2))
            cost += plan.notional * D(intent.max_slippage_bps) / D(10_000)
        if notional <= ZERO:
            return GateResult("cost_edge", False, "zero notional")

        if intent.book is Book.CARRY:
            horizon_days = D(intent.tags.get("horizon_days", "14"))
            rate = ZERO
            for plan in legs:
                if plan.side.value == "sell":
                    rate = max(rate, abs(ctx.funding.get(plan.symbol, ZERO)))
            edge = notional * rate * D(3) * horizon_days
        else:
            risk_usd = sum((plan.risk_usd for plan in legs), ZERO)
            first_r = intent.tp_ladder[0][0] if intent.tp_ladder else D("1.0")
            edge = risk_usd * first_r

        required = cost * self.risk.min_cost_edge_multiple
        if edge < required:
            return GateResult(
                "cost_edge",
                False,
                f"edge {edge:.4f} < {self.risk.min_cost_edge_multiple}x cost {cost:.4f}",
            )
        return GateResult("cost_edge", True)

    def _gate_positions(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        total = len(ctx.portfolio.positions)
        if total >= self.risk.max_open_positions:
            return GateResult("position_count", False, f"open positions {total}")
        book_count = len(ctx.portfolio.positions_of(intent.book))
        if book_count >= self.risk.max_per_book:
            return GateResult(
                "position_count", False, f"{intent.book.value} positions {book_count}"
            )
        return GateResult("position_count", True)

    def _gate_heat(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        equity = ctx.equity
        if equity <= ZERO:
            return GateResult("heat", False, "non-positive equity")
        new_risk = sum((plan.risk_usd for plan in legs), ZERO)
        if intent.book is Book.CARRY:
            new_risk = ZERO  # carry has no stop-defined risk; it is capped by notional
        book_heat = safe_div(ctx.portfolio.open_risk(intent.book) + new_risk, equity)
        book_cap = self.risk.max_book_heat_pct.get(intent.book, self.risk.max_portfolio_heat_pct)
        if book_heat > book_cap:
            return GateResult(
                "heat", False, f"{intent.book.value} heat {book_heat:.4%} > {book_cap:.4%}"
            )
        total_heat = safe_div(ctx.portfolio.open_risk() + new_risk, equity)
        if total_heat > self.risk.max_portfolio_heat_pct:
            return GateResult(
                "heat",
                False,
                f"portfolio heat {total_heat:.4%} > {self.risk.max_portfolio_heat_pct:.4%}",
            )
        return GateResult("heat", True)

    def _gate_drawdown(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        day = ctx.portfolio.day_pnl(ctx.marks)
        if day <= -self.risk.daily_loss_halt_pct:
            return GateResult("drawdown", False, f"daily pnl {day:.4%} below halt")
        drawdown = ctx.portfolio.drawdown(ctx.marks)
        if drawdown >= self.risk.max_drawdown_pct:
            return GateResult("drawdown", False, f"drawdown {drawdown:.4%} above limit")
        return GateResult("drawdown", True)

    def _gate_leverage(self, intent: Intent, legs: list[LegPlan], ctx: RiskContext) -> GateResult:
        equity = ctx.equity
        if equity <= ZERO:
            return GateResult("leverage", False, "non-positive equity")
        gross = ctx.portfolio.gross_notional(ctx.marks) + sum((p.notional for p in legs), ZERO)
        leverage = gross / equity
        if leverage > self.risk.max_gross_leverage:
            return GateResult("leverage", False, f"gross leverage {leverage:.2f}x")
        return GateResult("leverage", True)

    # -- stop moves ----------------------------------------------------
    def _evaluate_stop_move(self, intent: Intent, ctx: RiskContext) -> RiskDecision:
        symbol = intent.legs[0].symbol
        position = ctx.portfolio.get(symbol, intent.book)
        if position is None:
            return RiskDecision(
                intent_id=intent.id,
                approved=False,
                legs=(),
                gates=(GateResult("stop_move", False, "no position"),),
                action=intent.action,
            )
        if intent.stop_px is None:
            return RiskDecision(
                intent_id=intent.id,
                approved=False,
                legs=(),
                gates=(GateResult("stop_move", False, "missing stop"),),
                action=intent.action,
            )
        price = ctx.price(symbol) or position.avg_px
        new_stop = intent.stop_px
        if position.is_long and new_stop >= price:
            return RiskDecision(
                intent_id=intent.id,
                approved=False,
                legs=(),
                gates=(GateResult("stop_move", False, "stop at or above price"),),
                action=intent.action,
            )
        if not position.is_long and new_stop <= price:
            return RiskDecision(
                intent_id=intent.id,
                approved=False,
                legs=(),
                gates=(GateResult("stop_move", False, "stop at or below price"),),
                action=intent.action,
            )
        tighter = position.stop_px is None or (
            new_stop > position.stop_px if position.is_long else new_stop < position.stop_px
        )
        if not tighter:
            return RiskDecision(
                intent_id=intent.id,
                approved=False,
                legs=(),
                gates=(GateResult("stop_move", False, "stop would loosen"),),
                action=intent.action,
            )
        return RiskDecision(
            intent_id=intent.id,
            approved=True,
            legs=(),
            gates=(GateResult("stop_move", True),),
            action=intent.action,
        )


__all__ = ["ONE", "RiskEngine"]
