"""Validate model output and turn a bounded snapshot into an intent.

Conversion does not approve or execute anything.  All callers must still pass
the resulting intent through RiskEngine and OMS.  No model output sets size,
leverage, an account, or a broker.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from oq.domain.intent import Intent
from oq.types import D


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    action: Literal["wait", "long", "short"]
    symbol: str = Field(min_length=1, max_length=40)
    stop_px: Decimal | None = None
    rationale: str = Field(default="", max_length=1200)

    @model_validator(mode="after")
    def valid_stop(self) -> Proposal:
        if self.action != "wait" and (self.stop_px is None or self.stop_px <= 0):
            raise ValueError("entry proposals require a positive stop")
        return self


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def proposal_to_intent(
    proposal: Proposal, snapshot: dict, *, now_ms: int, max_age_ms: int = 180_000
) -> Intent | None:
    """Return an entry intent or None for wait, rejecting stale/mismatched data.

    ``snapshot`` must be assembled locally from a confirmed candle. The caller
    must verify the configured instrument universe and data source. A hash
    included in order tags binds the decision to the exact recorded snapshot.
    """
    created = int(snapshot["captured_ms"])
    if created > now_ms or now_ms - created > max_age_ms:
        raise ValueError("snapshot expired")
    if proposal.symbol != snapshot["symbol"]:
        raise ValueError("proposal symbol does not match snapshot")
    if proposal.action == "wait":
        return None
    price = D(str(snapshot["reference_price"]))
    stop = proposal.stop_px
    if not price.is_finite() or price <= 0 or stop is None or not stop.is_finite():
        raise ValueError("invalid reference price or stop")
    if (proposal.action == "long" and stop >= price) or (
        proposal.action == "short" and stop <= price
    ):
        raise ValueError("stop is on the wrong side of the reference price")
    factory = Intent.open_long if proposal.action == "long" else Intent.open_short
    return factory(
        strategy_id="ai_research",
        symbol=proposal.symbol,
        ts_ms=created,
        stop_px=stop,
        ref_px=price,
        # The model cannot invent profit targets or holding periods. These
        # match the existing directional position manager's defaults, and are
        # explicit so the risk gate and audit see the same parameters.
        tp_ladder=((D("1.5"), D("0.33")), (D("3.0"), D("0.33"))),
        time_stop_bars=32,
        tags={"snapshot_hash": snapshot_hash(snapshot), "origin": "ai"},
    )
