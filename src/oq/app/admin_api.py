"""Admin console and workspace endpoints.

Mounted under ``/api/v1`` by :mod:`oq.app.api`.  Read endpoints need any
authenticated principal; anything that changes state needs ``trader``, and
user/config management needs ``owner``.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

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


def _marks_complete(ctx: Any, portfolio: Any, marks: dict) -> bool:
    """Do not present entry-price fallbacks or old marks as current equity."""
    from oq.clock import tf_to_ms

    positions = portfolio.positions_of()
    if not positions:
        return True
    last_cycle = int(getattr(ctx, "last_cycle_ms", 0) or 0)
    now = int(time.time() * 1000)
    step = tf_to_ms(ctx.config.data.timeframe)
    if not last_cycle or last_cycle > now or last_cycle // step != now // step:
        return False
    return all(pos.symbol in marks and marks[pos.symbol] > 0 for pos in positions)


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
    return {
        "mode": ctx.config.app.mode,
        "version": app_version(),
        "uptime_s": round(time.time() - ctx.started_at, 1),
        "last_cycle_ms": ctx.last_cycle_ms,
        "stats": None,
        "equity": [],
    }


@router.get("/auth/me")
async def auth_me(request: Request) -> dict[str, Any]:
    """Report the current session, or ``authenticated: false``.

    Deliberately answers 200 for an anonymous caller: the console probes this on
    every page load, and a 401 here would print a console error for every
    logged-out visit while telling us nothing new.
    """
    import os

    from oq.app.auth import COOKIE_NAME, _configured_token, _is_loopback, validated_session

    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        principal = validated_session(request, cookie)
        if principal is not None:
            return {
                "authenticated": True,
                "username": principal.name,
                "role": principal.role,
                "via": principal.via,
            }
    token = _configured_token()
    if not token and os.getenv("OQ_ALLOW_LOOPBACK_AUTH") == "1" and _is_loopback(request):
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
        if _marks_complete(ctx, portfolio, marks):
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
    complete = _marks_complete(ctx, orch.portfolio, marks)
    out = []
    for pos in orch.portfolio.positions_of():
        mark = marks.get(pos.symbol) if complete else None
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
        "equity": str(orch.portfolio.equity(marks)) if complete else None,
        "cash": str(orch.portfolio.cash),
        "heat": str(orch.portfolio.heat(marks)) if complete else None,
        "open_risk": str(orch.portfolio.open_risk(marks)) if complete else None,
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
            frame = store.read_bars(symbol, timeframe)
            if "confirmed" not in frame.columns or "ts_ms" not in frame.columns:
                raise ValueError("confirmed candles unavailable")
            frame = frame.loc[
                frame["confirmed"].eq(True) & (frame["ts_ms"] <= (now // step) * step - step)
            ]
            last = int(frame["ts_ms"].iloc[-1]) if not frame.empty else None
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
            "config_dir": str(ctx.config.config_dir or (Path(ctx.config.root) / "config")),
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


@router.post("/admin/backups/full")
def admin_full_backup(
    request: Request, principal: Principal = Depends(require_owner)
) -> dict[str, Any]:
    """Archive a SQLite snapshot, market data and YAML; excludes credentials."""
    from oq.storage.backup import create_archive

    ctx = _state(request)
    result = create_archive(ctx.db, ctx.config)
    ctx.audit(principal.name, "backup.full", {"file": result["file"]})
    return {"ok": True, **result}


@router.get("/admin/backups/full")
def admin_full_backups(
    request: Request, principal: Principal = Depends(require_owner)
) -> dict[str, Any]:
    backups = Path(_state(request).config.root) / "backups"
    if not backups.exists():
        return {"items": []}
    return {
        "items": [
            {"file": p.name, "bytes": p.stat().st_size, "created_ms": int(p.stat().st_mtime * 1000)}
            for p in sorted(
                backups.glob("origin-full-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            if p.is_file() and not p.is_symlink()
        ]
    }


@router.post("/admin/backups/full/{filename}/verify")
def admin_verify_full_backup(
    request: Request, filename: str, principal: Principal = Depends(require_owner)
) -> dict[str, Any]:
    from oq.storage.backup import verify_archive

    if not filename.startswith("origin-full-") or not filename.endswith(".zip"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid backup name")
    root = Path(_state(request).config.root) / "backups"
    path = root / filename
    if path.name != filename or path.is_symlink() or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found")
    try:
        result = verify_archive(path)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "backup verification failed"
        ) from exc
    _state(request).audit(principal.name, "backup.verify", {"file": filename})
    return result


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
    details: dict[str, Any] = {}
    if action == "kill":
        orch.engage_kill_switch(f"admin console by {principal.name}")
    elif action == "pause":
        orch.halt(f"admin console by {principal.name}")
    elif action == "resume":
        orch.resume()
    elif action == "reset_kill":
        if not principal.at_least("owner"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner role required")
        orch.reset_kill_switch()
    elif action == "flatten":
        marks = getattr(ctx, "marks", {}) or {}
        details = await asyncio.to_thread(orch.flatten, marks, f"admin console by {principal.name}")
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown action {action}")
    with ctx.db.tx() as conn:
        admin_data.record_audit(conn, principal.name, f"danger.{action}", None)
    if details.get("status") == "cycle_in_progress":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "交易周期正在运行；已启用熔断但尚未平仓，在途订单仍可能成交，周期结束后请重新操作",
        )
    return {"ok": True, "action": action, **details}


@router.post("/admin/venue/readback")
def admin_venue_readback(
    request: Request, principal: Principal = Depends(require_owner)
) -> dict[str, Any]:
    """Explicit read-only OKX observations; no order, position or ledger mutation."""
    from oq.execution.okx_adapter import OkxAdapter
    from oq.execution.venue_readback import readback

    ctx = _state(request)
    orchestrator = ctx.orchestrator
    if ctx.config.app.mode != "live" or orchestrator is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "venue readback requires a live-mode adapter")
    broker = orchestrator.broker
    if not isinstance(broker, OkxAdapter) or not broker.rest.has_credentials:
        raise HTTPException(status.HTTP_409_CONFLICT, "venue credentials are not configured")
    result = readback(ctx.store, broker.rest, ctx.config.instrument_map())
    result["venue_environment"] = "demo" if broker.rest.demo else "live"
    ctx.audit(principal.name, "venue.readback", {"orders": len(result["orders"])})
    return result


class VenueOrderReviewBody(BaseModel):
    client_id: str = Field(min_length=1, max_length=32, pattern="^[A-Za-z0-9]+$")
    review_id: UUID


@router.post("/admin/venue/order-review")
def admin_venue_order_review(
    request: Request,
    body: VenueOrderReviewBody,
    principal: Principal = Depends(require_owner),
) -> dict[str, Any]:
    """Record an idempotent venue observation; never resolve or unlock orders."""
    from oq.execution.okx_adapter import OkxAdapter
    from oq.execution.venue_readback import (
        ReviewConflict,
        ReviewOrderNotFound,
        record_order_review,
    )

    ctx = _state(request)
    orchestrator = ctx.orchestrator
    if ctx.config.app.mode != "live" or orchestrator is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "venue review requires a live-mode adapter")
    broker = orchestrator.broker
    if not isinstance(broker, OkxAdapter) or not broker.rest.has_credentials:
        raise HTTPException(status.HTTP_409_CONFLICT, "venue credentials are not configured")
    try:
        return record_order_review(
            ctx.store,
            broker.rest,
            ctx.config.instrument_map(),
            actor=principal.name,
            client_id=body.client_id,
            review_id=str(body.review_id),
        )
    except ReviewOrderNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unresolved order not found") from exc
    except ReviewConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


__all__ = ["router"]
