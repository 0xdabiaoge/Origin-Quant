"""FastAPI application factory and the in-process scheduler.

The scheduler is a single thread inside the same process as the API: there is
no cron, no second writer, and no lock file.  Missing a cycle is a non-event —
the next one re-reads the same state.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from oq.app.admin_api import router as v1_router
from oq.app.api import router
from oq.backtest import trading_symbols
from oq.clock import sleep_until, tf_to_ms
from oq.config import Config, load_config, redacted_config
from oq.data.ingest import Ingestor
from oq.data.okx_rest import OkxRest
from oq.data.store import ParquetStore
from oq.execution.okx_adapter import build_rest
from oq.execution.paper import InstantQuoteBroker
from oq.storage import Database, Store
from oq.version import app_version

log = logging.getLogger(__name__)
# Built SPA (Vite output, emitted into the package by `npm run build`).
# Overridable so a build can be mounted from outside without rebuilding the wheel.
WEB_DIR = Path(os.getenv("OQ_WEB_DIR", str(Path(__file__).parent / "web")))


@dataclass
class AppContext:
    config: Config
    db: Database
    store: Store
    orchestrator: Any = None
    marks: dict[str, Any] = field(default_factory=dict)
    last_cycle_ms: int = 0
    started_at: float = field(default_factory=time.time)
    version: str = field(default_factory=app_version)
    scheduler: Scheduler | None = None
    news_worker: Any = None
    notification_worker: Any = None

    def audit(self, actor: str, action: str, detail: Any = None) -> None:
        try:
            with self.db.tx() as conn:
                self.store.audit(conn, actor, action, detail)
        except Exception:
            log.exception("audit write failed")


class Scheduler:
    """Runs one cycle shortly after each bar closes."""

    def __init__(self, ctx: AppContext, *, grace_s: int = 15) -> None:
        self.ctx = ctx
        self.grace_s = grace_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="oq-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _next_cycle_ms(self, now: int) -> int:
        step = tf_to_ms(self.ctx.config.data.timeframe)
        boundary = (now // step + 1) * step
        return boundary + self.grace_s * 1000

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = int(time.time() * 1000)
            target = self._next_cycle_ms(now)
            sleep_until(target)
            if self._stop.is_set():
                break
            try:
                self._cycle()
            except Exception as exc:  # one bad cycle must not kill the loop
                self.last_error = str(exc)
                log.exception("scheduled cycle failed")

    def _cycle(self) -> None:
        ctx = self.ctx
        orch = ctx.orchestrator
        if orch is None:
            return
        refresher = _Refresher(ctx)
        refresher.sync()
        report = orch.run_once()
        ctx.last_cycle_ms = report.ts_ms
        ctx.marks = dict(getattr(orch, "last_marks", {}))
        log.info(
            "cycle %s intents=%d approved=%d fills=%d skipped=%s errors=%s",
            report.cycle_id,
            report.intents,
            report.approved,
            report.fills,
            report.skipped,
            report.errors,
        )


class _Refresher:
    """Pulls the latest closed candles before a cycle.  Public endpoints only."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def sync(self) -> None:
        ctx = self.ctx
        venue = ctx.config.venues.get("okx")
        if venue is None or not venue.enabled:
            return
        try:
            rest = OkxRest(venue, demo=venue.demo)
            ingestor = Ingestor(
                ctx.config,
                ParquetStore(ctx.config.data.parquet_dir),
                rest,
            )
            for symbol in trading_symbols(ctx.config):
                try:
                    ingestor.sync_bars(symbol, ctx.config.data.timeframe, days=3)
                except Exception:
                    log.warning("bar sync failed for %s", symbol)
                if symbol.endswith("-SWAP"):
                    try:
                        ingestor.sync_funding(symbol, days=3)
                    except Exception:
                        log.warning("funding sync failed for %s", symbol)
            rest.close()
        except Exception:
            log.exception("data refresh failed; cycle will see stale bars and refuse to trade")


def build_app(config: Config | None = None, *, start_scheduler: bool | None = None) -> FastAPI:
    config = config or load_config(os.getenv("OQ_CONFIG_DIR", "config"))

    db = Database(config.app.db_path)
    db.migrate()
    store = Store(db, config_hash=config.hash)
    with db.tx() as conn:
        store.upsert_instruments(conn, config.instrument_map().values())

    orchestrator = _build_orchestrator(config, db, store)
    ctx = AppContext(config=config, db=db, store=store, orchestrator=orchestrator)

    app = FastAPI(
        title="Origin Quant",
        version=app_version(),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    from oq.app.auth import LoginLimiter

    app.state.login_limiter = LoginLimiter()
    app.state.oq = ctx
    app.include_router(router, prefix="/api")
    app.include_router(v1_router, prefix="/api/v1")
    from oq.app.market_api import router as market_router

    app.include_router(market_router, prefix="/api/v1")
    from oq.app.ai_api import router as ai_router

    app.include_router(ai_router, prefix="/api/v1")
    from oq.app.notifications_api import router as notifications_router

    app.include_router(notifications_router, prefix="/api/v1")
    # Login/logout/me are also exposed under v1 so the console has one base path.
    app.include_router(router, prefix="/api/v1", include_in_schema=False)

    # Hashed Vite assets are immutable, so they can be cached hard.
    assets_dir = WEB_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    def _spa_index() -> Path | None:
        page = WEB_DIR / "index.html"
        return page if page.exists() else None

    @app.get("/")
    async def index() -> Any:
        page = _spa_index()
        if page is not None:
            return FileResponse(page)
        return JSONResponse({"service": "origin-quant", "version": app_version()})

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str, request: Request) -> Any:
        """History-mode routing: unknown paths render the SPA, not a 404.

        API paths are excluded so a typo in a fetch still fails loudly instead
        of silently returning HTML.
        """
        if (
            full_path.startswith(("api/", "assets/"))
            or "\\" in full_path
            or any(part in {".", ".."} for part in full_path.split("/"))
            or Path(full_path).is_absolute()
        ):
            return JSONResponse({"error": "not found"}, status_code=404)
        root = WEB_DIR.resolve()
        candidate = (root / full_path).resolve()
        if not candidate.is_relative_to(root):
            return JSONResponse({"error": "not found"}, status_code=404)
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        page = _spa_index()
        if page is not None:
            return FileResponse(page)
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.get("/favicon.ico")
    async def favicon() -> Any:
        return JSONResponse({}, status_code=204)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> Any:
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"error": "internal", "detail": str(exc)[:200]}, status_code=500)

    @app.on_event("startup")
    async def _startup() -> None:
        from oq.app.ai_api import NewsWorker
        from oq.app.notifications_api import NotificationWorker

        ctx.news_worker = NewsWorker(ctx)
        ctx.news_worker.start()
        ctx.notification_worker = NotificationWorker(ctx)
        ctx.notification_worker.start()
        should_start = (
            start_scheduler
            if start_scheduler is not None
            else os.getenv("OQ_SCHEDULER", "0") == "1"
        )
        if should_start and config.app.mode in {"paper", "live"}:
            ctx.scheduler = Scheduler(ctx, grace_s=config.app.cycle_grace_seconds)
            ctx.scheduler.start()
            log.info("scheduler started for %s mode", config.app.mode)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if ctx.scheduler:
            ctx.scheduler.stop()
        if ctx.news_worker:
            ctx.news_worker.stop()
        if ctx.notification_worker:
            ctx.notification_worker.stop()
        db.close()

    return app


def _build_orchestrator(config: Config, db: Database, store: Store) -> Any:
    from oq.app.orchestrator import Orchestrator

    rest = None
    try:
        rest = build_rest(config)
    except Exception:
        log.warning("no venue credentials; orchestrator runs without a private feed")
    ingestor = Ingestor(config, ParquetStore(config.data.parquet_dir), rest) if rest else None
    if ingestor is None:
        return None

    if config.app.mode == "live":
        from oq.execution.okx_adapter import OkxAdapter
        from oq.storage.identity import bind_ledger

        assert rest is not None  # guarded above
        adapter = OkxAdapter(config, rest)
        if not adapter.allow_live:
            raise RuntimeError("live mode requires config.live_trading=true and OQ_LIVE=1")
        bind_ledger(db, store, config.app.mode, rest)
        broker = adapter
        return Orchestrator(config, db, store, ingestor, broker=broker)
    from oq.storage.identity import bind_ledger

    bind_ledger(db, store, config.app.mode, rest)
    return Orchestrator(config, db, store, ingestor, broker=InstantQuoteBroker())


__all__ = ["AppContext", "Scheduler", "build_app", "redacted_config"]
