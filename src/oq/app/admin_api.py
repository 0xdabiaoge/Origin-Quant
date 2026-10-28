"""Admin console and workspace endpoints.

Mounted under ``/api/v1`` by :mod:`oq.app.api`.  Read endpoints need any
authenticated principal; anything that changes state needs ``trader``, and
user/config management needs ``owner``.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from oq.app import admin_data
from oq.app.auth import (
    ROLES,
    Principal,
    create_user,
    delete_user,
    list_users,
    require_auth,
    require_owner,
    require_write,
    set_password,
    set_role,
)
from oq.config import redacted_config
from oq.version import app_version

router = APIRouter()


def _state(request: Request) -> Any:
    return request.app.state.oq


def _conn(request: Request) -> sqlite3.Connection:
    return _state(request).db.connect()


# -- public -----------------------------------------------------------------
@router.get("/public/brand")
async def brand(request: Request) -> dict[str, Any]:
    ctx = _state(request)
    return {
        "name": "Origin Quant",
        "tagline": "单操作员加密货币量化交易引擎",
        "mode": ctx.config.app.mode,
        "version": app_version(),
        "live_trading": bool(ctx.config.app.live_trading),
    }


@router.get("/public/metrics")
async def public_metrics(request: Request) -> dict[str, Any]:
    """The few numbers that are safe to show a logged-out visitor.

    Deliberately excludes positions, orders and config: this endpoint is the
    only unauthenticated data surface, so it must not leak the book.
    """
    ctx = _state(request)
    conn = _conn(request)
    try:
        stats = admin_data.trade_stats(conn)
        series = admin_data.equity_series(conn, limit=180)
    finally:
        conn.close()
    return {
        "mode": ctx.config.app.mode,
        "version": app_version(),
        "uptime_s": round(time.time() - ctx.started_at, 1),
        "last_cycle_ms": ctx.last_cycle_ms,
        "stats": stats,
        "equity": [
            {"ts_ms": r["ts_ms"], "equity": r["equity"], "drawdown": r["drawdown"]} for r in series
        ],
    }


@router.get("/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    """Report the current session, or ``authenticated: false``.

    Deliberately answers 200 for an anonymous caller: the console probes this on
    every page load, and a 401 here would print a console error for every
    logged-out visit while telling us nothing new.
    """
    from oq.app.auth import COOKIE_NAME, _configured_token, _is_loopback, read_session

    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        principal = read_session(cookie)
        if principal is not None:
            return {
                "authenticated": True,
                "username": principal.name,
                "role": principal.role,
                "via": principal.via,
            }
    token = _configured_token()
    if not token and _is_loopback(request):
        return {
            "authenticated": True,
            "username": "loopback",
            "role": "owner",
            "via": "loopback",
        }
    return {"authenticated": False, "username": None, "role": None, "via": None}


# -- workspace --------------------------------------------------------------
@router.get("/app/overview")
async def app_overview(
    request: Request, principal: Principal = Depends(require_auth)
) -> dict[str, Any]:
    ctx = _state(request)
    conn = _conn(request)
    try:
        stats = admin_data.trade_stats(conn)
        series = admin_data.equity_series(conn, limit=600)
        decisions = admin_data.recent_decisions(conn, limit=20)
        overview = admin_data.system_overview(
            conn, uptime_s=time.time() - ctx.started_at, version=app_version()
        )
    finally:
        conn.close()
    orch = ctx.orchestrator
    portfolio = orch.portfolio if orch is not None else None
    marks = getattr(ctx, "marks", {}) or {}
    positions: list[dict[str, Any]] = []
    equity = cash = None
    if portfolio is not None:
        equity = str(portfolio.equity(marks))
        cash = str(portfolio.cash)
        for pos in portfolio.positions_of():
            positions.append(
                {
                    "symbol": pos.symbol,
                    "book": pos.book.value,
                    "qty": str(pos.qty),
                    "avg_px": str(pos.avg_px),
                    "stop_px": None if pos.stop_px is None else str(pos.stop_px),
                    "strategy_id": pos.strategy_id,
                    "realized_pnl": str(pos.realized_pnl),
                    "funding_net": str(pos.funding_net),
                    "opened_ms": pos.opened_ms,
                }
            )
    return {
        "equity": equity,
        "cash": cash,
        "positions": positions,
        "stats": stats,
        "equity_series": series,
        "recent_decisions": decisions,
        "counts": overview["counts"],
        "last_cycle_ms": ctx.last_cycle_ms,
        "mode": ctx.config.app.mode,
        "version": app_version(),
    }


@router.get("/app/positions")
async def app_positions(
    request: Request, principal: Principal = Depends(require_auth)
) -> dict[str, Any]:
    ctx = _state(request)
    orch = ctx.orchestrator
    if orch is None:
        return {"positions": [], "equity": None, "cash": None}
    marks = getattr(ctx, "marks", {}) or {}
    out = []
    for pos in orch.portfolio.positions_of():
        mark = marks.get(pos.symbol)
        out.append(
            {
                "symbol": pos.symbol,
                "book": pos.book.value,
                "qty": str(pos.qty),
                "avg_px": str(pos.avg_px),
                "mark_px": None if mark is None else str(mark),
                "stop_px": None if pos.stop_px is None else str(pos.stop_px),
                "initial_stop_px": None
                if pos.initial_stop_px is None
                else str(pos.initial_stop_px),
                "strategy_id": pos.strategy_id,
                "realized_pnl": str(pos.realized_pnl),
                "funding_net": str(pos.funding_net),
                "fees_paid": str(pos.fees_paid),
                "opened_ms": pos.opened_ms,
                "tags": dict(pos.tags),
            }
        )
    return {
        "positions": out,
        "equity": str(orch.portfolio.equity(marks)),
        "cash": str(orch.portfolio.cash),
        "heat": str(orch.portfolio.heat(marks)),
        "open_risk": str(orch.portfolio.open_risk(marks)),
    }


@router.get("/app/orders")
async def app_orders(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_auth),
) -> dict[str, Any]:
    conn = _conn(request)
    try:
        return {
            "open": admin_data.open_orders(conn, limit),
            "fills": admin_data.recent_fills(conn, limit),
        }
    finally:
        conn.close()


@router.get("/app/decisions")
async def app_decisions(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_auth),
) -> dict[str, Any]:
    conn = _conn(request)
    try:
        return {
            "items": admin_data.recent_decisions(conn, limit),
            "rejections": admin_data.decision_reason_histogram(conn),
        }
    finally:
        conn.close()


@router.get("/app/events")
async def app_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    since_id: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_auth),
) -> dict[str, Any]:
    conn = _conn(request)
    try:
        return {"items": admin_data.event_feed(conn, limit, since_id)}
    finally:
        conn.close()


@router.get("/app/equity")
async def app_equity(
    request: Request,
    limit: int = Query(default=600, ge=1, le=5000),
    principal: Principal = Depends(require_auth),
) -> dict[str, Any]:
    conn = _conn(request)
    try:
        return {"items": admin_data.equity_series(conn, limit)}
    finally:
        conn.close()


@router.get("/app/data")
async def app_data(
    request: Request, principal: Principal = Depends(require_auth)
) -> dict[str, Any]:
    """Freshness per symbol: what the engine would actually be able to trade."""
    from oq.clock import tf_to_ms
    from oq.data.store import ParquetStore

    ctx = _state(request)
    timeframe = ctx.config.data.timeframe
    step = tf_to_ms(timeframe)
    now = int(time.time() * 1000)
    store = ParquetStore(ctx.config.data.parquet_dir)
    rows = []
    for symbol in ctx.config.instrument_map():
        try:
            last = store.last_bar_ms(symbol, timeframe)
        except Exception:
            last = None
        if last is None:
            rows.append({"symbol": symbol, "last_bar_ms": None, "age_min": None, "stale": True})
            continue
        age_min = round((now - last) / 60000, 1)
        rows.append(
            {
                "symbol": symbol,
                "last_bar_ms": last,
                "age_min": age_min,
                "stale": last < (now // step) * step - step,
            }
        )
    rows.sort(key=lambda r: (r["last_bar_ms"] is None, -(r["age_min"] or 0)))
    return {"timeframe": timeframe, "now_ms": now, "instruments": rows}


# -- admin ------------------------------------------------------------------
@router.get("/admin/overview")
async def admin_overview(
    request: Request, principal: Principal = Depends(require_auth)
) -> dict[str, Any]:
    ctx = _state(request)
    conn = _conn(request)
    try:
        data = admin_data.system_overview(
            conn, uptime_s=time.time() - ctx.started_at, version=app_version()
        )
        data["stats"] = admin_data.trade_stats(conn)
    finally:
        conn.close()
    data["mode"] = ctx.config.app.mode
    data["scheduler"] = ctx.scheduler is not None
    data["config_hash"] = ctx.config.hash
    return data


class UserBody(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="viewer")


@router.get("/admin/users")
async def admin_users(
    request: Request, principal: Principal = Depends(require_owner)
) -> dict[str, Any]:
    conn = _conn(request)
    try:
        return {"items": list_users(conn), "roles": list(ROLES)}
    finally:
        conn.close()


@router.post("/admin/users")
async def admin_create_user(
    request: Request,
    body: UserBody,
    principal: Principal = Depends(require_owner),
) -> dict[str, Any]:
    if body.role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"role must be one of {ROLES}")
    ctx = _state(request)
    with ctx.db.tx() as conn:
        if any(u["username"] == body.username for u in list_users(conn)):
            raise HTTPException(status.HTTP_409_CONFLICT, "username already exists")
        create_user(conn, body.username, body.password, body.role)
        admin_data.record_audit(conn, principal.name, "user.create", {"username": body.username})
    return {"ok": True}


@router.put("/admin/users/{username}/password")
async def admin_set_password(
    request: Request,
    username: str,
    body: dict[str, str],
    principal: Principal = Depends(require_owner),
) -> dict[str, Any]:
    password = body.get("password", "")
    if len(password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "password must be at least 8 characters")
    ctx = _state(request)
    with ctx.db.tx() as conn:
        set_password(conn, username, password)
        admin_data.record_audit(conn, principal.name, "user.password", {"username": username})
    return {"ok": True}


@router.put("/admin/users/{username}/role")
async def admin_set_role(
    request: Request,
    username: str,
    body: dict[str, str],
    principal: Principal = Depends(require_owner),
) -> dict[str, Any]:
    role = body.get("role", "")
    if role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"role must be one of {ROLES}")
    ctx = _state(request)
    with ctx.db.tx() as conn:
        set_role(conn, username, role)
        admin_data.record_audit(
            conn, principal.name, "user.role", {"username": username, "role": role}
        )
    return {"ok": True}


@router.delete("/admin/users/{username}")
async def admin_delete_user(
    request: Request,
    username: str,
    principal: Principal = Depends(require_owner),
) -> dict[str, Any]:
    ctx = _state(request)
    with ctx.db.tx() as conn:
        if username == principal.name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "refusing to delete yourself")
        # No "last user" guard: the environment password always remains a way in,
        # so deleting every row cannot lock anyone out. Blocking here made the
        # console unable to remove a stray account.
        delete_user(conn, username)
        admin_data.record_audit(conn, principal.name, "user.delete", {"username": username})
    return {"ok": True}


@router.get("/admin/audit")
async def admin_audit(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: str | None = None,
    action: str | None = None,
    principal: Principal = Depends(require_owner),
) -> dict[str, Any]:
    conn = _conn(request)
    try:
        return admin_data.list_audit(conn, limit=limit, offset=offset, actor=actor, action=action)
    finally:
        conn.close()


@router.get("/admin/config")
async def admin_config(
    request: Request, principal: Principal = Depends(require_owner)
) -> dict[str, Any]:
    ctx = _state(request)
    return {
        "config": redacted_config(ctx.config),
        "hash": ctx.config.hash,
        "paths": {
            "db": str(ctx.config.app.db_path),
            "parquet": str(ctx.config.data.parquet_dir),
            "config_dir": str(ctx.config.root),
        },
    }


@router.get("/admin/logs")
async def admin_logs(
    request: Request,
    limit: int = Query(default=200, ge=1, le=2000),
    principal: Principal = Depends(require_owner),
) -> dict[str, Any]:
    """Tail the log file if one is configured, otherwise report where it would be."""
    import os

    ctx = _state(request)
    log_dir = Path(os.getenv("OQ_LOG_DIR") or (Path(ctx.config.root) / "logs"))
    files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"path": None, "lines": [], "available": False}
    target = files[0]
    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-limit:]
    except OSError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    return {
        "path": str(target),
        "available": True,
        "lines": [ln.rstrip("\n") for ln in tail],
    }


@router.post("/admin/backup")
async def admin_backup(
    request: Request, principal: Principal = Depends(require_owner)
) -> dict[str, Any]:
    """Copy the database to a timestamped file and report its size.

    Uses SQLite's own backup API rather than a file copy: the engine keeps
    writing, and a raw copy of a live WAL database is not guaranteed to be
    consistent.
    """
    ctx = _state(request)
    backups = Path(ctx.config.root) / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = backups / f"origin-{stamp}.db"
    src = ctx.db.connect()
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    size = target.stat().st_size
    with ctx.db.tx() as conn:
        admin_data.record_audit(
            conn, principal.name, "backup.create", {"file": target.name, "bytes": size}
        )
    return {"ok": True, "file": target.name, "bytes": size}


@router.get("/admin/backups")
async def admin_backups(
    request: Request, principal: Principal = Depends(require_owner)
) -> dict[str, Any]:
    ctx = _state(request)
    backups = Path(ctx.config.root) / "backups"
    if not backups.exists():
        return {"items": []}
    items = [
        {
            "file": p.name,
            "bytes": p.stat().st_size,
            "created_ms": int(p.stat().st_mtime * 1000),
        }
        for p in sorted(backups.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    ]
    return {"items": items, "disk_free": shutil.disk_usage(backups).free}


class DangerBody(BaseModel):
    confirm: str = Field(description="Type the action name to confirm")


@router.post("/admin/danger/{action}")
async def admin_danger(
    request: Request,
    action: str,
    body: DangerBody,
    principal: Principal = Depends(require_write),
) -> dict[str, Any]:
    """Irreversible operations, gated on typing the action name.

    Everything here is also written to the audit log, because "who flattened
    the book at 03:00" is the question this console exists to answer.
    """
    if body.confirm.strip().upper() != action.upper():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"type {action.upper()} to confirm this action"
        )
    ctx = _state(request)
    orch = ctx.orchestrator
    if orch is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no orchestrator")
    if action == "kill":
        orch.engage_kill_switch(f"admin console by {principal.name}")
    elif action == "pause":
        orch.halt(f"admin console by {principal.name}")
    elif action == "resume":
        orch.resume()
    elif action == "flatten":
        marks = getattr(ctx, "marks", {}) or {}
        orch.flatten(marks, f"admin console by {principal.name}")
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown action {action}")
    with ctx.db.tx() as conn:
        admin_data.record_audit(conn, principal.name, f"danger.{action}", None)
    return {"ok": True, "action": action}


__all__ = ["router"]
