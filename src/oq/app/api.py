"""HTTP control surface.

Read endpoints are cheap projections; write endpoints are the kill switch, the
flatten, the pause, and a backtest runner.  Every write is audited with the
principal that performed it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from oq.app.auth import (
    COOKIE_NAME,
    ROLE_OWNER,
    SESSION_TTL_S,
    Principal,
    bootstrap_password_hash,
    check_password,
    get_user,
    issue_session,
    require_auth,
    require_write,
    touch_login,
)
from oq.backtest import (
    BacktestEngine,
    BacktestParams,
    load_parquet,
    load_synthetic,
    trading_symbols,
)
from oq.backtest.report import format_report
from oq.config import Config
from oq.domain.events import EventType
from oq.strategy.registry import build_position_manager, build_strategies
from oq.types import D
from oq.version import app_version

log = logging.getLogger(__name__)

router = APIRouter()


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    username: str = Field(default="operator", max_length=64)


class BacktestBody(BaseModel):
    days: int = Field(default=120, ge=7, le=730)
    initial_cash: Decimal = Field(default=D("10000"), gt=0)
    source: str = Field(default="parquet", pattern="^(parquet|synthetic)$")
    seed: int = 7


def _state(request: Request) -> Any:
    return request.app.state.oq


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    ctx = _state(request)
    return {
        "status": "ok",
        "mode": ctx.config.app.mode,
        "version": app_version(),
        "uptime_s": round(time.time() - ctx.started_at, 1),
        "last_cycle_ms": ctx.last_cycle_ms,
    }


@router.get("/status")
async def trading_status(
    request: Request, principal: Principal = Depends(require_auth)
) -> dict[str, Any]:
    ctx = _state(request)
    store = ctx.store
    portfolio = ctx.orchestrator.portfolio if ctx.orchestrator else None
    marks = ctx.marks or {}
    equity = portfolio.equity(marks) if portfolio else D("0")
    return {
        "mode": ctx.config.app.mode,
        "live_trading": ctx.config.app.live_trading,
        "halted": bool(ctx.orchestrator and ctx.orchestrator.halted),
        "kill_switch": bool(ctx.orchestrator and ctx.orchestrator._kill),
        "equity": str(equity),
        "cash": str(portfolio.cash) if portfolio else "0",
        "positions": [p.as_dict(marks.get(p.symbol, p.avg_px)) for p in portfolio.positions_of()]
        if portfolio
        else [],
        "snapshot": portfolio.snapshot(marks) if portfolio else {},
        "config_hash": ctx.config.hash,
        "last_cycle_ms": store.last_cycle_ms(),
        "stats": store.stats(),
        "principal": principal.name,
    }


@router.get("/equity")
async def equity(
    request: Request, limit: int = 500, principal: Principal = Depends(require_auth)
) -> dict[str, Any]:
    return {"points": _state(request).store.equity_series(limit=min(limit, 5000))}


@router.get("/trades")
async def trades(
    request: Request, limit: int = 100, principal: Principal = Depends(require_auth)
) -> dict[str, Any]:
    return {"trades": _state(request).store.recent_trades(limit=min(limit, 1000))}


@router.get("/decisions")
async def decisions(
    request: Request, limit: int = 50, principal: Principal = Depends(require_auth)
) -> dict[str, Any]:
    return {"decisions": _state(request).store.recent_decisions(limit=min(limit, 500))}


@router.get("/orders")
async def orders(request: Request, principal: Principal = Depends(require_auth)) -> dict[str, Any]:
    return {"orders": _state(request).store.open_orders()}


@router.get("/config")
async def config(request: Request, principal: Principal = Depends(require_auth)) -> dict[str, Any]:
    from oq.config import redacted_config

    return redacted_config(_state(request).config)


@router.post("/auth/login", include_in_schema=True)
async def login(request: Request, body: LoginBody, response: Response) -> dict[str, Any]:
    """Log in with the bootstrap password, or with a database user.

    The bootstrap password lives in the environment so an operator can always
    get in even if they lock themselves out of the users table; once a user is
    created that matches the submitted username, the table wins.
    """
    ctx = _state(request)
    username = (body.username or "operator").strip()
    with ctx.db.tx() as conn:
        user = get_user(conn, username)
        if user is not None:
            if not check_password(str(user["password_hash"]), body.password):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
            touch_login(conn, username)
            role = str(user["role"])
        else:
            bootstrap = bootstrap_password_hash()
            if not bootstrap or not check_password(bootstrap, body.password):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
            role = ROLE_OWNER
    token = issue_session(username, role)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        max_age=SESSION_TTL_S,
        secure=request.url.scheme == "https",
    )
    return {"ok": True, "username": username, "role": role}


@router.post("/auth/logout")
async def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/auth/me")
async def me(principal: Principal = Depends(require_auth)) -> dict[str, Any]:
    return {"username": principal.name, "role": principal.role, "via": principal.via}


@router.post("/control/kill")
async def kill(request: Request, principal: Principal = Depends(require_write)) -> dict[str, Any]:
    ctx = _state(request)
    if ctx.orchestrator is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no orchestrator")
    ctx.orchestrator.engage_kill_switch(f"http:{principal.name}")
    ctx.audit(principal.name, "kill_switch")
    return {"ok": True, "kill_switch": True}


@router.post("/control/pause")
async def pause(request: Request, principal: Principal = Depends(require_write)) -> dict[str, Any]:
    ctx = _state(request)
    if ctx.orchestrator is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no orchestrator")
    ctx.orchestrator.halt(f"http:{principal.name}")
    ctx.audit(principal.name, "pause")
    return {"ok": True, "halted": True}


@router.post("/control/resume")
async def resume(request: Request, principal: Principal = Depends(require_write)) -> dict[str, Any]:
    ctx = _state(request)
    if ctx.orchestrator is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no orchestrator")
    ctx.orchestrator.resume()
    ctx.audit(principal.name, "resume")
    return {"ok": True, "halted": False}


@router.post("/control/flatten")
async def flatten(
    request: Request, principal: Principal = Depends(require_write)
) -> dict[str, Any]:
    ctx = _state(request)
    if ctx.orchestrator is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no orchestrator")
    ctx.orchestrator._flatten(ctx.marks or {}, int(time.time() * 1000), f"manual:{principal.name}")
    ctx.audit(principal.name, "flatten")
    return {"ok": True}


@router.post("/control/cycle")
async def run_cycle(
    request: Request, principal: Principal = Depends(require_write)
) -> dict[str, Any]:
    ctx = _state(request)
    if ctx.orchestrator is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no orchestrator")
    report = await asyncio.to_thread(ctx.orchestrator.run_once)
    ctx.audit(principal.name, "manual_cycle")
    return {
        "cycle_id": report.cycle_id,
        "equity": str(report.equity),
        "intents": report.intents,
        "approved": report.approved,
        "rejected": report.rejected,
        "fills": report.fills,
        "errors": report.errors,
        "skipped": report.skipped,
    }


@router.post("/backtest")
async def run_backtest(
    request: Request, body: BacktestBody, principal: Principal = Depends(require_write)
) -> dict[str, Any]:
    ctx = _state(request)
    result = await asyncio.to_thread(_run_backtest, ctx.config, body)
    ctx.audit(principal.name, "backtest", {"days": body.days, "source": body.source})
    return {
        "label": result.label,
        "metrics": result.metrics,
        "rejections": result.rejections,
        "decisions": result.decisions,
        "trades": len(result.trades),
        "report": format_report(result),
        "equity": [
            [p.ts_ms, str(p.equity)] for p in result.equity[:: max(1, len(result.equity) // 400)]
        ],
    }


def _run_backtest(config: Config, body: BacktestBody):
    symbols = trading_symbols(config)
    if body.source == "synthetic":
        series = load_synthetic(
            config, symbols=symbols, timeframe=config.data.timeframe, days=body.days, seed=body.seed
        )
    else:
        series = load_parquet(config, symbols=symbols, timeframe=config.data.timeframe)
    if not series:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "no parquet data; seed it or use source=synthetic"
        )
    engine = BacktestEngine(config, build_strategies(config), build_position_manager())
    return engine.run(
        series,
        BacktestParams(
            initial_cash=body.initial_cash,
            timeframe=config.data.timeframe,
            label=f"{body.source}-{body.days}d",
        ),
    )


__all__ = ["EventType", "router"]
