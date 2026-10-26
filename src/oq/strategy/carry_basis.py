"""Funding/basis carry: long spot against short perpetual.

The trade earns the funding rate while staying delta-neutral.  It is not
free money:

* basis can widen before it converges (mark-to-market risk),
* both legs pay fees, so the hold must span enough settlements,
* the short leg carries liquidation and margin risk,
* exchange or stablecoin failure is a real tail risk that no amount of
  hedging inside one venue removes.

The strategy therefore only enters when the funding rate pays for the round
trip within the intended holding window, and exits when the carry decays.
"""

from __future__ import annotations

from oq.domain.intent import Intent
from oq.strategy.base import StrategyContext, dec, num
from oq.types import ZERO, Book, D


class CarryBasis:
    id = "carry_basis"
    book = Book.CARRY

    def __init__(self, params: dict, timeframe: str = "15m") -> None:
        self.params = params
        self.timeframe = timeframe
        self.entry_funding_bps = dec(params, "entry_funding_bps", D("2.0"))
        self.exit_funding_bps = dec(params, "exit_funding_bps", D("0.15"))
        self.max_basis_bps = dec(params, "max_basis_bps", D("20"))
        self.min_basis_bps = dec(params, "min_basis_bps", D("-8"))
        self.notional_per_leg_pct = dec(params, "notional_per_leg_pct", D("0.12"))
        self.max_pairs = int(num(params, "max_pairs", 2))
        self.horizon_days = int(num(params, "horizon_days", 14))
        self.pairs: list[tuple[str, str]] = [
            (str(pair["spot"]), str(pair["perp"]))
            for pair in params.get("pairs", [])
            if isinstance(pair, dict) and "spot" in pair and "perp" in pair
        ]

    def generate(self, ctx: StrategyContext) -> list[Intent]:
        intents: list[Intent] = []
        open_pairs = 0
        for spot_symbol, perp_symbol in self.pairs:
            spot = ctx.view(spot_symbol)
            perp = ctx.view(perp_symbol)
            if spot is None or perp is None or spot.close.size == 0 or perp.close.size == 0:
                continue
            spot_px = spot.price
            perp_px = perp.price
            if spot_px <= ZERO or perp_px <= ZERO:
                continue

            basis_bps = (perp_px / spot_px - D(1)) * D(10_000)
            funding_bps = perp.last_funding * D(10_000)

            spot_pos = ctx.position(spot_symbol, self.book)
            perp_pos = ctx.position(perp_symbol, self.book)
            in_pair = spot_pos is not None or perp_pos is not None
            if in_pair:
                open_pairs += 1

            if not in_pair:
                if funding_bps < self.entry_funding_bps:
                    continue
                if basis_bps > self.max_basis_bps or basis_bps < self.min_basis_bps:
                    continue
                if open_pairs >= self.max_pairs:
                    continue
                intents.append(
                    Intent.open_pair(
                        strategy_id=self.id,
                        ts_ms=ctx.now_ms,
                        spot_symbol=spot_symbol,
                        perp_symbol=perp_symbol,
                        notional_pct=self.notional_per_leg_pct,
                        reason=f"funding={funding_bps:.2f}bps basis={basis_bps:.2f}bps",
                    )
                )
                intents[-1] = _with_horizon(intents[-1], self.horizon_days)
                open_pairs += 1
            else:
                decayed = funding_bps <= self.exit_funding_bps
                basis_stretched = basis_bps >= self.max_basis_bps
                if decayed or basis_stretched:
                    intents.append(
                        Intent.close_pair(
                            strategy_id=self.id,
                            ts_ms=ctx.now_ms,
                            spot_symbol=spot_symbol,
                            perp_symbol=perp_symbol,
                            reason=(f"funding={funding_bps:.2f}bps basis={basis_bps:.2f}bps"),
                        )
                    )
        return intents


__all__ = ["CarryBasis"]


def _with_horizon(intent: Intent, horizon_days: int) -> Intent:
    tags = dict(intent.tags)
    tags["horizon_days"] = str(horizon_days)
    return type(intent)(
        strategy_id=intent.strategy_id,
        book=intent.book,
        action=intent.action,
        legs=intent.legs,
        ts_ms=intent.ts_ms,
        stop_px=intent.stop_px,
        tp_ladder=intent.tp_ladder,
        time_stop_bars=intent.time_stop_bars,
        max_slippage_bps=intent.max_slippage_bps,
        risk_pct=intent.risk_pct,
        sizing=intent.sizing,
        notional_pct=intent.notional_pct,
        ref_px=intent.ref_px,
        tags=tags,
    )
