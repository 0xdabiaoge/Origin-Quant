"""Owner-controlled, opt-in Telegram alerts from the local event journal.

The notification worker is independent of the execution/scheduler thread.
Only event type, timestamp, symbol and event sequence leave the application;
never send raw payloads, account data, credentials or strategy prompts.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from oq.app.auth import Principal, require_owner
from oq.notifications.telegram import credentials_available, send_telegram

router = APIRouter()
_CONFIG = "notifications.telegram.config.v1"
_CURSOR = "notifications.telegram.cursor.v1"
_STATUS = "notifications.telegram.status.v1"
_LOCK = threading.Lock()
_RISK_EVENTS = ("kill_switch", "risk_halt", "order_unknown", "error")
log = logging.getLogger(__name__)


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    poll_interval_s: int = Field(default=30, ge=5, le=300)


def _settings(ctx: Any) -> NotificationSettings:
    return NotificationSettings.model_validate(ctx.store.get_kv(_CONFIG, {}))


def _status(ctx: Any) -> dict:
    value = ctx.store.get_kv(_STATUS, {})
    return value if isinstance(value, dict) else {}


def _public(ctx: Any) -> dict:
    return {
        **_settings(ctx).model_dump(),
        "credentials_available": credentials_available(),
        "event_types": list(_RISK_EVENTS),
        "status": _status(ctx),
    }


@router.get("/admin/notifications/config")
def get_config(request: Request, principal: Principal = Depends(require_owner)) -> dict:
    return _public(request.app.state.oq)


@router.put("/admin/notifications/config")
def put_config(
    request: Request,
    body: NotificationSettings,
    principal: Principal = Depends(require_owner),
) -> dict:
    if body.enabled and not credentials_available():
        raise HTTPException(409, "notification credentials are missing or invalid")
    ctx = request.app.state.oq
    with _LOCK:
        previous = _settings(ctx)
        with ctx.db.tx() as conn:
            # Start at the current end on explicit enabling. Old risk events
            # are not silently replayed to a newly configured recipient.
            if body.enabled and not previous.enabled:
                current = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
                ctx.store.set_kv(conn, _CURSOR, int(current))
                ctx.store.set_kv(conn, _STATUS, {})
            ctx.store.set_kv(conn, _CONFIG, body.model_dump())
            ctx.store.audit(conn, principal.name, "notification.config", {"enabled": body.enabled})
    return _public(ctx)


@router.post("/admin/notifications/test")
def test_notification(request: Request, principal: Principal = Depends(require_owner)) -> dict:
    ctx = request.app.state.oq
    if not _settings(ctx).enabled:
        raise HTTPException(409, "notifications are disabled")
    try:
        send_telegram("Origin Quant: notification connection test.")
    except Exception:
        ctx.audit(principal.name, "notification.test", {"result": "failed"})
        raise HTTPException(502, "notification delivery failed") from None
    ctx.audit(principal.name, "notification.test", {"result": "acknowledged"})
    return {"ok": True, "status": "acknowledged"}


class NotificationWorker:
    """Poll journal risk events, retry delivery without blocking trade cycles.

    Acknowledged events advance a durable cursor. Failed sends keep the cursor
    and retry with a capped backoff. No exactly-once delivery is promised.
    This design assumes the application's documented single process/worker.
    """

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_attempt = float("-inf")
        self._failures = 0

    def run_once(self) -> None:
        if self._stop.is_set():
            return
        with _LOCK:
            settings = _settings(self.ctx)
            if not settings.enabled or self._stop.is_set():
                self._failures = 0
                return
            now = time.monotonic()
            delay = min(300, settings.poll_interval_s * 2 ** min(self._failures, 4))
            if now - self._last_attempt < delay:
                return
            self._last_attempt = now
            cursor = int(self.ctx.store.get_kv(_CURSOR, 0) or 0)
            # Read an upper bound first. Events inserted after it remain for the
            # next pass; do not skip events between reads of two queries.
            with self.ctx.db.read() as conn:
                upper = int(conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0])
                rows = conn.execute(
                    "SELECT seq, ts_ms, type, symbol FROM events "
                    "WHERE seq > ? AND seq <= ? AND type IN (?,?,?,?) "
                    "ORDER BY seq LIMIT 1",
                    (cursor, upper, *_RISK_EVENTS),
                ).fetchall()
            if not rows:
                if upper > cursor and not self._stop.is_set():
                    with self.ctx.db.tx() as conn:
                        self.ctx.store.set_kv(conn, _CURSOR, upper)
                return
            for row in rows:
                if self._stop.is_set():
                    return
                symbol = str(row["symbol"] or "-")[:40]
                # Strictly sanitize symbol in case historical journal values
                # were modified outside validated market-data paths.
                symbol = "".join(c for c in symbol if c.isascii() and (c.isalnum() or c == "-"))
                message = (
                    f"Origin Quant risk event\nType: {row['type']}\nSymbol: {symbol or '-'}"
                    f"\nEvent ID: {row['seq']}\nTime (UTC ms): {row['ts_ms']}"
                )
                try:
                    send_telegram(message)
                except Exception:
                    self._failures += 1
                    if not self._stop.is_set():
                        with self.ctx.db.tx() as conn:
                            self.ctx.store.set_kv(
                                conn,
                                _STATUS,
                                {
                                    "last_attempt_ms": int(time.time() * 1000),
                                    "last_result": "failed",
                                    "pending_event_seq": row["seq"],
                                },
                            )
                    log.warning("risk notification delivery failed; will retry")
                    return
                if self._stop.is_set():
                    return
                self._failures = 0
                with self.ctx.db.tx() as conn:
                    self.ctx.store.set_kv(conn, _CURSOR, row["seq"])
                    self.ctx.store.set_kv(
                        conn,
                        _STATUS,
                        {
                            "last_attempt_ms": int(time.time() * 1000),
                            "last_result": "acknowledged",
                            "last_event_seq": row["seq"],
                        },
                    )

    def _loop(self) -> None:
        while not self._stop.wait(5):
            try:
                self.run_once()
            except Exception:
                # Never log exceptions containing a transport URL or token.
                log.warning("risk notification worker unavailable")

    def start(self) -> None:
        if self._thread is None and not self._stop.is_set():
            self._thread = threading.Thread(target=self._loop, name="oq-notifications", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
