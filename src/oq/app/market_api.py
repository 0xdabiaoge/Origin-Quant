"""Authenticated, read-only charts from the engine's own confirmed market data."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from oq.app.auth import Principal, require_auth
from oq.clock import tf_to_ms
from oq.data.store import BAR_COLUMNS, ParquetStore

router = APIRouter()
PERIODS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def confirmed_bars(frame: pd.DataFrame, step: int, now: int) -> pd.DataFrame:
    """Reject invalid OHLCV, future/open candles, and off-grid timestamps."""
    if frame.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    frame = frame[BAR_COLUMNS].copy()
    numeric = ["ts_ms", "open", "high", "low", "close", "volume"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    valid = (
        np.isfinite(frame[numeric]).all(axis=1)
        & frame["confirmed"].eq(True)
        & (frame.ts_ms % step == 0)
        & (frame.ts_ms <= now // step * step - step)
        & (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
        & (frame.volume >= 0)
        & (frame.high >= frame[["open", "close", "low"]].max(axis=1))
        & (frame.low <= frame[["open", "close"]].min(axis=1))
    )
    return frame.loc[valid].drop_duplicates("ts_ms", keep="last").sort_values("ts_ms")


def aggregate_bars(frame: pd.DataFrame, base_step: int, step: int, now: int) -> list[dict]:
    if step < base_step or step % base_step:
        raise ValueError("timeframe must be a multiple of the stored timeframe")
    clean = confirmed_bars(frame, base_step, now)
    if clean.empty:
        return []
    clean["bucket"] = clean.ts_ms.astype("int64") // step * step
    grouped = clean.groupby("bucket", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        count=("ts_ms", "count"),
    )
    grouped = grouped[(grouped["count"] == step // base_step) & (grouped.index + step <= now)]
    return [
        {
            "ts_ms": int(ts),
            **{key: float(row[key]) for key in ["open", "high", "low", "close", "volume"]},
        }
        for ts, row in grouped.iterrows()
    ]


def _read(ctx: Any, symbol: str) -> pd.DataFrame:
    try:
        frame = ParquetStore(ctx.config.data.parquet_dir).read_bars(
            symbol, ctx.config.data.timeframe
        )
        if not set(BAR_COLUMNS).issubset(frame.columns):
            raise ValueError("incomplete OHLCV schema")
        return frame
    except Exception as exc:
        raise HTTPException(503, "market data temporarily unavailable") from exc


@router.get("/app/market/summary")
def market_summary(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    ctx = request.app.state.oq
    now = int(time.time() * 1000)
    step = tf_to_ms(ctx.config.data.timeframe)
    items = []
    for symbol in ctx.config.instrument_map():
        bars = confirmed_bars(_read(ctx, symbol), step, now)
        last = bars.iloc[-1] if not bars.empty else None
        ts = int(last.ts_ms) if last is not None else None
        window = bars[bars.ts_ms > ts - 86_400_000] if ts is not None else bars
        previous = bars[bars.ts_ms == ts - 86_400_000] if ts is not None else bars
        complete_day = len(window) == 86_400_000 // step and not previous.empty
        items.append(
            {
                "symbol": symbol,
                "last_bar_ms": ts,
                "close": float(last.close) if last is not None else None,
                "change_24h_pct": (float(last.close / previous.iloc[-1].close) - 1) * 100
                if complete_day and last is not None
                else None,
                "high_24h": float(window.high.max()) if complete_day else None,
                "low_24h": float(window.low.min()) if complete_day else None,
                "sparkline": [float(v) for v in bars.close.tail(32)],
                "stale": ts is None or ts < now // step * step - step,
            }
        )
    return {"items": items, "now_ms": now, "base_timeframe": ctx.config.data.timeframe}


@router.get("/app/market/candles")
def market_candles(
    request: Request,
    symbol: str,
    timeframe: str = "15m",
    limit: int = Query(400, ge=2, le=1000),
    principal: Principal = Depends(require_auth),
) -> dict:
    ctx = request.app.state.oq
    if symbol not in ctx.config.instrument_map():
        raise HTTPException(404, "unknown instrument")
    base_step = tf_to_ms(ctx.config.data.timeframe)
    step = PERIODS.get(timeframe, 0)
    if not step or step < base_step or step % base_step:
        raise HTTPException(422, "unsupported chart timeframe")
    now = int(time.time() * 1000)
    items = aggregate_bars(_read(ctx, symbol), base_step, step, now)[-limit:]
    last = items[-1]["ts_ms"] if items else None
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "base_timeframe": ctx.config.data.timeframe,
        "items": items,
        "now_ms": now,
        "last_bar_ms": last,
        "stale": last is None or last < now // step * step - step,
        "source": "local-parquet",
        "confirmed_only": True,
    }
