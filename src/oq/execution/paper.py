"""Fill simulation for paper mode and tests.

Not a queue model.  A market order pays slippage and the taker fee; a limit
order fills only when the bar trades through the price (strict, not "touch"),
and a post-only order is rejected when it would cross.  Anything more
elaborate (queue position, latency, partial fills) is a P3 concern and
pretending otherwise would overstate backtest accuracy.
"""

from __future__ import annotations

from decimal import Decimal

from oq.data.models import Quote
from oq.domain.order import Fill, Order
from oq.execution.broker import BrokerContext, BrokerResult, slippage_price
from oq.types import ZERO, Instrument, Liquidity, OrderType, Side


class PaperBroker:
    def __init__(
        self, *, taker_slippage_bps: int = 2, maker_fee_override: Decimal | None = None
    ) -> None:
        self.taker_slippage_bps = taker_slippage_bps
        self.maker_fee_override = maker_fee_override

    def place(self, order: Order, instrument: Instrument, ctx: BrokerContext) -> BrokerResult:
        reference = ctx.price_for(order.symbol)
        if reference <= ZERO:
            return BrokerResult(rejected="no price")

        if order.order_type is OrderType.MARKET:
            price = slippage_price(
                reference, order.side is Side.BUY, max(self.taker_slippage_bps, 1)
            )
            price = instrument.round_px(price)
            fee = instrument.fee(order.qty * price, Liquidity.TAKER)
            return BrokerResult(
                fills=[
                    Fill(
                        order_client_id=order.client_id,
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        px=price,
                        fee=fee,
                        ts_ms=ctx.now_ms,
                        liquidity=Liquidity.TAKER,
                    )
                ]
            )

        # Limit / post-only.
        assert order.px is not None
        crossed = (order.side is Side.BUY and reference <= order.px) or (
            order.side is Side.SELL and reference >= order.px
        )
        if order.order_type is OrderType.POST_ONLY and crossed:
            return BrokerResult(rejected="post-only would cross")

        touched = self._touched(order, ctx)
        if not touched and not crossed:
            return BrokerResult()
        price = order.px
        fee = instrument.fee(order.qty * price, Liquidity.MAKER)
        return BrokerResult(
            fills=[
                Fill(
                    order_client_id=order.client_id,
                    symbol=order.symbol,
                    side=order.side,
                    qty=order.qty,
                    px=price,
                    fee=fee,
                    ts_ms=ctx.now_ms,
                    liquidity=Liquidity.MAKER,
                )
            ]
        )

    @staticmethod
    def _touched(order: Order, ctx: BrokerContext) -> bool:
        if ctx.low is None or ctx.high is None:
            return False
        assert order.px is not None
        if order.side is Side.BUY:
            return ctx.low < order.px
        return ctx.high > order.px

    def cancel(self, order: Order, instrument: Instrument) -> bool:
        return True


class InstantQuoteBroker(PaperBroker):
    """Paper broker that also considers the live bid/ask spread."""

    def place(self, order: Order, instrument: Instrument, ctx: BrokerContext) -> BrokerResult:
        quote: Quote | None = ctx.quote
        if quote is not None and order.order_type is OrderType.MARKET:
            price = quote.ask if order.side is Side.BUY else quote.bid
            if price > ZERO:
                fee = instrument.fee(order.qty * price, Liquidity.TAKER)
                return BrokerResult(
                    fills=[
                        Fill(
                            order_client_id=order.client_id,
                            symbol=order.symbol,
                            side=order.side,
                            qty=order.qty,
                            px=instrument.round_px(price),
                            fee=fee,
                            ts_ms=ctx.now_ms,
                            liquidity=Liquidity.TAKER,
                        )
                    ]
                )
        return super().place(order, instrument, ctx)


__all__ = ["InstantQuoteBroker", "PaperBroker"]
