"""OKX order adapter.

Live placement is gated three times:

1. ``config.app.live_trading`` must be true,
2. ``OQ_LIVE=1`` must be set in the environment,
3. the process must be started in ``live`` mode.

Any one missing and :meth:`place` raises instead of sending.  Demo trading
uses the same code path with the simulated-trading header, which is how the
canary phase is meant to run.
"""

from __future__ import annotations

import logging
import os
import time
from decimal import Decimal

from oq.config import Config
from oq.data.okx_rest import OkxError, OkxRest
from oq.domain.order import Fill, Order
from oq.execution.broker import BrokerContext, BrokerResult
from oq.types import ZERO, D, InstType, Liquidity, OrderType, fmt_dec

log = logging.getLogger(__name__)


class LiveTradingDisabled(RuntimeError):
    pass


class OkxAdapter:
    def __init__(
        self,
        config: Config,
        rest: OkxRest,
        *,
        allow_live: bool | None = None,
        poll_attempts: int = 8,
        poll_interval_s: float = 0.25,
    ) -> None:
        self.config = config
        self.rest = rest
        self.allow_live = _live_allowed(config) if allow_live is None else allow_live
        self.poll_attempts = poll_attempts
        self.poll_interval_s = poll_interval_s

    # -- Broker protocol ----------------------------------------------
    def place(self, order: Order, instrument, ctx: BrokerContext) -> BrokerResult:
        if not self.allow_live:
            raise LiveTradingDisabled(
                "live trading needs config.live_trading=true, OQ_LIVE=1 and mode=live"
            )
        body = self._order_body(order, instrument, ctx)
        try:
            ack = self.rest.place_order(body)
        except OkxError as exc:
            return BrokerResult(rejected=f"{exc.code}: {exc.message}")

        venue_id = str(ack.get("ordId") or "")
        fills = self._poll(order, instrument, venue_id, ctx)
        return BrokerResult(fills=fills, venue_order_id=venue_id)

    def cancel(self, order: Order, instrument) -> bool:
        try:
            self.rest.cancel_order(
                order.symbol, client_id=order.client_id, venue_id=order.venue_order_id
            )
            return True
        except OkxError as exc:
            log.warning("cancel failed %s: %s", order.client_id, exc)
            return False

    # -- internals -----------------------------------------------------
    def _order_body(self, order: Order, instrument, ctx: BrokerContext) -> dict:
        if instrument.inst_type is InstType.SWAP:
            size = instrument.round_contracts(order.qty)
        else:
            size = instrument.round_qty(order.qty)
        body: dict = {
            "instId": order.symbol,
            "tdMode": "cross" if instrument.inst_type is InstType.SWAP else "cash",
            "side": order.side.value,
            "ordType": "market" if order.order_type is OrderType.MARKET else "limit",
            "sz": fmt_dec(Decimal(size), 8),
            "clOrdId": order.client_id,
        }
        if order.order_type is not OrderType.MARKET:
            body["px"] = fmt_dec(order.px or ctx.price, 10)
        if order.reduce_only:
            body["reduceOnly"] = "true"
        if order.tags.get("stop_px"):
            body["attachAlgoOrds"] = [{"slTriggerPx": order.tags["stop_px"], "slOrdPx": "-1"}]
        return body

    def _poll(self, order: Order, instrument, venue_id: str, ctx: BrokerContext) -> list[Fill]:
        last: dict = {}
        for attempt in range(self.poll_attempts):
            try:
                last = self.rest.order(order.symbol, client_id=order.client_id, venue_id=venue_id)
            except OkxError as exc:
                log.warning("order poll failed: %s", exc)
            state = str(last.get("state", ""))
            if state in {"filled", "partially_filled", "canceled"}:
                break
            time.sleep(self.poll_interval_s * (attempt + 1))

        filled = D(last.get("accFillSz") or 0)
        if filled <= ZERO:
            if str(last.get("state")) == "canceled":
                return []
            return []
        # accFillSz is in contracts for swaps.
        qty_base = instrument.base_from_contracts(filled) if instrument.is_swap else filled
        avg_px = D(last.get("avgPx") or 0) or ctx.price
        fee = D(last.get("fee") or 0)
        if fee == ZERO:
            fee = instrument.fee(qty_base * avg_px, Liquidity.TAKER)
        return [
            Fill(
                order_client_id=order.client_id,
                symbol=order.symbol,
                side=order.side,
                qty=qty_base,
                px=avg_px,
                fee=abs(fee),
                ts_ms=int(last.get("uTime") or ctx.now_ms),
                liquidity=Liquidity.TAKER,
                venue_fill_id=str(last.get("ordId") or venue_id),
            )
        ]

    # -- account views used by reconciliation --------------------------
    def balance_usdt(self) -> Decimal:
        payload = self.rest.balance()
        for detail in payload.get("details", []):
            if str(detail.get("ccy")) == "USDT":
                return D(detail.get("eq") or detail.get("cashBal") or 0)
        return ZERO

    def venue_positions(self) -> list[dict]:
        return self.rest.positions("SWAP") + self.rest.positions("SPOT")


def _live_allowed(config: Config) -> bool:
    return bool(
        config.app.live_trading and os.getenv("OQ_LIVE") == "1" and config.app.mode == "live"
    )


def build_rest(config: Config) -> OkxRest:
    """Build a REST client from environment credentials (never from YAML)."""
    demo = config.venues["okx"].demo
    if demo:
        key = os.getenv("OKX_DEMO_API_KEY", "")
        secret = os.getenv("OKX_DEMO_SECRET_KEY", "")
        passphrase = os.getenv("OKX_DEMO_PASSPHRASE", "")
    else:
        key = os.getenv("OKX_LIVE_API_KEY", "")
        secret = os.getenv("OKX_LIVE_SECRET_KEY", "")
        passphrase = os.getenv("OKX_LIVE_PASSPHRASE", "")
    return OkxRest(
        config.venues["okx"],
        api_key=key,
        secret=secret,
        passphrase=passphrase,
        demo=demo,
    )


__all__ = ["LiveTradingDisabled", "OkxAdapter", "build_rest"]
