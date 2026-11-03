"""Owner-only research and public news controls.

All outputs are research records.  The router cannot execute trades and does
not start background network jobs. Credentials are environment references only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from oq.ai.news import refresh_news
from oq.ai.proposals import proposal_to_intent, snapshot_hash
from oq.ai.settings import AISettings, public_settings
from oq.ai.transport import request_proposal
from oq.app.auth import Principal, require_owner
from oq.clock import tf_to_ms
from oq.data.store import ParquetStore
from oq.domain.intent import Intent
from oq.types import Action, Book, D, Side

router = APIRouter()
CONFIG_KEY = "ai.research.config.v1"
NEWS_KEY = "ai.research.news.v1"
HISTORY_KEY = "ai.research.history.v1"
_NEWS_LOCK = threading.Lock()
log = logging.getLogger(__name__)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=40)


@dataclass(frozen=True, slots=True)
class PaperConfirmation:
    """A model review is evidence, never risk approval or an order."""

    confirmed: bool
    reason: str
    record_id: str | None = None


def paper_confirmation_applies(settings: AISettings, intent: Intent) -> bool:
    """Scope opt-in confirmation to one configured directional instrument."""
    return (
        settings.paper_confirmation_enabled
        and intent.action is Action.OPEN
        and intent.book is Book.DIRECTIONAL
        and len(intent.legs) == 1
        and intent.legs[0].symbol == settings.auto_symbol
        and intent.strategy_id != "ai_research"
    )


def _state(request: Request) -> Any:
    return request.app.state.oq


def _settings(ctx: Any) -> AISettings:
    # Invalid persisted JSON must not silently become disabled confirmation.
    row = ctx.db.query_one("SELECT value FROM kv WHERE key=?", (CONFIG_KEY,))
    return AISettings() if row is None else AISettings.model_validate_json(row["value"])


def _append_history(ctx: Any, entry: dict) -> None:
    # Read/update under the same writer lock: concurrent research requests must
    # not silently overwrite each other's records.
    with ctx.db.tx() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (HISTORY_KEY,)).fetchone()
        try:
            history = json.loads(row["value"]) if row else []
        except (ValueError, TypeError):
            history = []
        if not isinstance(history, list):
            history = []
        ctx.store.set_kv(conn, HISTORY_KEY, [entry, *history][:100])


def _decide(settings: AISettings, snapshot: dict) -> tuple[Any, dict, list[dict]]:
    """Run configured seats; entry requires every independent request to agree.

    Failed, invalid or conflicting seats produce WAIT. Each seat is validated
    against the same local snapshot. No account data or secrets enter records.
    """
    if not settings.council_enabled:
        proposal, usage = request_proposal(settings, snapshot)
        return proposal, usage, []
    from oq.ai.proposals import Proposal

    members = [("market", settings)]
    for seat in settings.council_seats:
        member = settings.model_copy(
            update={
                "provider": seat.provider,
                "model": seat.model,
                "endpoint": seat.endpoint,
                "api_key_env": seat.api_key_env,
                "timeout_s": seat.timeout_s,
                "council_enabled": False,
                "council_seats": [],
            }
        )
        members.append((seat.role, member))

    def ask(member_pair: tuple[str, AISettings]) -> tuple[dict, Any, dict]:
        role, member = member_pair
        record: dict[str, Any] = {
            "role": role,
            "provider": member.provider,
            "model": member.model,
            "status": "failed",
            "proposal": None,
            "usage": {},
        }
        try:
            proposal, usage = request_proposal(member, {**snapshot, "analysis_role": role})
            proposal_to_intent(
                proposal, snapshot, now_ms=int(time.time() * 1000), max_age_ms=60_000
            )
            record.update(
                status="completed", proposal=proposal.model_dump(mode="json"), usage=usage
            )
            return record, proposal, usage
        except Exception:
            record["error"] = "model request failed or response invalid"
            return record, None, {}

    # Independent requests run concurrently so a council need not multiply
    # decision latency by the number of seats. The caller still enforces the
    # cycle deadline and discards late results.
    with ThreadPoolExecutor(max_workers=len(members), thread_name_prefix="oq-ai-seat") as pool:
        results = list(pool.map(ask, members))
    records = [record for record, _, _ in results]
    proposals = [proposal for _, proposal, _ in results if proposal is not None]
    totals: dict[str, int] = {}
    for _, _, usage in results:
        for key, value in usage.items():
            if type(value) is int and 0 <= value <= 10_000_000:
                totals[key] = totals.get(key, 0) + value
    if len(proposals) != len(members) or len({p.action for p in proposals}) != 1:
        return (
            Proposal(
                action="wait", symbol=snapshot["symbol"], rationale="Council failed or disagreed"
            ),
            totals,
            records,
        )
    action = proposals[0].action
    if action == "wait":
        return (
            Proposal(action="wait", symbol=snapshot["symbol"], rationale="Council chose wait"),
            totals,
            records,
        )
    stops = [p.stop_px for p in proposals if p.stop_px is not None]
    stop = max(stops) if action == "long" else min(stops)
    return (
        Proposal(
            action=action,
            symbol=snapshot["symbol"],
            stop_px=stop,
            rationale="Council agreed; all recommendations remain subject to risk controls",
        ),
        totals,
        records,
    )


def build_snapshot(ctx: Any, symbol: str, *, now_ms: int) -> dict:
    item = ctx.config.universe_map().get(symbol)
    if item is None or not item.enabled or not item.allows(Book.DIRECTIONAL):
        raise ValueError("symbol is not an enabled directional instrument")
    tf = ctx.config.data.timeframe
    step = tf_to_ms(tf)
    frame = ParquetStore(ctx.config.data.parquet_dir).read_bars(symbol, tf)
    if frame.empty or "confirmed" not in frame:
        raise ValueError("confirmed candles unavailable")
    frame = frame[(frame["ts_ms"] + step <= now_ms) & (frame["confirmed"] == True)].tail(30)
    if frame.empty:
        raise ValueError("confirmed candles unavailable")
    newest = int(frame["ts_ms"].iloc[-1])
    expected = (now_ms // step) * step - step
    if newest < expected:
        raise ValueError("latest closed candle is stale")
    candles = []
    for row in frame.itertuples(index=False):
        candles.append(
            {
                "ts_ms": int(row.ts_ms),
                "open": str(row.open),
                "high": str(row.high),
                "low": str(row.low),
                "close": str(row.close),
                "volume": str(row.volume),
            }
        )
    cached = ctx.store.get_kv(NEWS_KEY, {})
    items = cached.get("items", []) if isinstance(cached, dict) else []
    news = [
        {"title": n["title"], "source": n["source"], "published_ms": n["published_ms"]}
        for n in items
        if isinstance(n, dict)
        and n.get("title")
        and n.get("source")
        and isinstance(n.get("published_ms"), int)
        and 0 <= now_ms - n["published_ms"] <= 86_400_000
        and isinstance(n.get("fetched_ms"), int)
        and 0 <= now_ms - n["fetched_ms"] <= 86_400_000
    ][:10]
    return {
        "symbol": symbol,
        "timeframe": tf,
        "captured_ms": now_ms,
        "last_candle_open_ms": newest,
        "reference_price": candles[-1]["close"],
        "candles": candles,
        "news": news,
        "news_available": bool(news),
        "data_source": "local_parquet_unverified",
    }


@router.get("/admin/ai/config")
def get_config(request: Request, principal: Principal = Depends(require_owner)) -> dict:
    return public_settings(_settings(_state(request)))


@router.put("/admin/ai/config")
def put_config(
    request: Request, body: AISettings, principal: Principal = Depends(require_owner)
) -> dict:
    ctx = _state(request)
    with ctx.db.tx() as conn:
        ctx.store.set_kv(conn, CONFIG_KEY, body.model_dump())
        ctx.store.audit(
            conn, principal.name, "ai.config", {"provider": body.provider, "enabled": body.enabled}
        )
    return public_settings(body)


@router.get("/admin/ai/history")
def history(request: Request, principal: Principal = Depends(require_owner)) -> dict:
    return {"items": _state(request).store.get_kv(HISTORY_KEY, [])}


@router.post("/admin/ai/test")
def test_model(request: Request, principal: Principal = Depends(require_owner)) -> dict:
    """Explicit connectivity test; the only input is a fictitious empty snapshot."""
    ctx = _state(request)
    settings = _settings(ctx)
    if not settings.enabled:
        raise HTTPException(409, "AI research is disabled")
    snapshot = {"symbol": "TEST", "news": [], "candles": [], "data_available": False}
    try:
        _, usage = request_proposal(settings, snapshot)
    except Exception as exc:
        ctx.audit(principal.name, "ai.test", {"status": "failed"})
        raise HTTPException(502, "model connection or response validation failed") from exc
    ctx.audit(principal.name, "ai.test", {"status": "ok"})
    return {"ok": True, "provider": settings.provider, "model": settings.model, "usage": usage}


@router.get("/admin/news")
def get_news(request: Request, principal: Principal = Depends(require_owner)) -> dict:
    return _state(request).store.get_kv(NEWS_KEY, {"items": [], "fetched_ms": 0, "errors": {}})


@router.post("/admin/news/refresh")
def news_refresh(request: Request, principal: Principal = Depends(require_owner)) -> dict:
    try:
        return _refresh_and_store(_state(request), principal.name)
    except RuntimeError as exc:
        raise HTTPException(409, "news refresh already running") from exc


def _refresh_and_store(ctx: Any, actor: str, stop_event: threading.Event | None = None) -> dict:
    if not _NEWS_LOCK.acquire(blocking=False):
        raise RuntimeError("news refresh already running")
    try:
        data = refresh_news()
        if stop_event is not None and stop_event.is_set():
            return data
        return _save_news(ctx, actor, data)
    finally:
        _NEWS_LOCK.release()


def _save_news(ctx: Any, actor: str, data: dict) -> dict:
    previous = ctx.store.get_kv(NEWS_KEY, {})
    # When an upstream fails, preserve its older headlines with their original
    # fetch timestamps, so they cannot masquerade as fresh data.
    if isinstance(previous, dict):
        old = previous.get("items", [])
        if isinstance(old, list):
            seen = {row["id"] for row in data["items"]}
            data["items"].extend(
                row
                for row in old
                if isinstance(row, dict)
                and row.get("source") in data["errors"]
                and row.get("id") not in seen
            )
            data["items"] = data["items"][:50]
    with ctx.db.tx() as conn:
        ctx.store.set_kv(conn, NEWS_KEY, data)
        ctx.store.audit(conn, actor, "news.refresh", {"errors": list(data["errors"])})
    return data


class NewsWorker:
    """Independently refresh public news after explicit administrator opt-in."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_attempt = float("-inf")

    def run_once(self) -> None:
        settings = _settings(self.ctx)
        if not settings.auto_news_enabled or self._stop.is_set():
            return
        now = time.monotonic()
        if now - self._last_attempt < settings.news_interval_s:
            return
        self._last_attempt = now
        try:
            _refresh_and_store(self.ctx, "news-worker", self._stop)
        except Exception:
            log.warning("scheduled public news refresh failed")

    def _loop(self) -> None:
        while not self._stop.wait(10):
            try:
                self.run_once()
            except Exception:
                log.exception("news worker failed")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self._stop.is_set():
            return
        self._thread = threading.Thread(target=self._loop, name="oq-news", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)


@router.post("/admin/ai/analyze")
def analyze(
    request: Request, body: AnalyzeRequest, principal: Principal = Depends(require_owner)
) -> dict:
    ctx = _state(request)
    settings = _settings(ctx)
    if not settings.enabled:
        raise HTTPException(409, "AI research is disabled")
    now = int(time.time() * 1000)
    try:
        snapshot = build_snapshot(ctx, body.symbol.strip().upper(), now_ms=now)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    entry = {
        "id": uuid.uuid4().hex,
        "ts_ms": now,
        "symbol": snapshot["symbol"],
        "provider": settings.provider,
        "model": settings.model,
        "status": "failed",
        "proposal": None,
        "error": None,
        "snapshot": snapshot,
        "snapshot_hash": snapshot_hash(snapshot),
        "execution": "research_only",
    }
    try:
        proposal, usage, council = _decide(settings, snapshot)
        # Validates direction and freshness; this does not run risk/OMS.
        intent = proposal_to_intent(proposal, snapshot, now_ms=int(time.time() * 1000))
        entry.update(
            {
                "status": "completed",
                "proposal": proposal.model_dump(mode="json"),
                "usage": usage,
                "council": council,
                "intent": None if intent is None else intent.as_dict(),
            }
        )
    except Exception:
        entry["error"] = "model request failed or returned an invalid or expired proposal"
    _append_history(ctx, entry)
    ctx.audit(principal.name, "ai.analyze", {"id": entry["id"], "status": entry["status"]})
    return entry


def confirm_paper_strategy_intent(
    ctx: Any, views: dict, intent: Intent, *, now_ms: int
) -> PaperConfirmation:
    """Review a scoped strategy entry; the caller still enforces risk and OMS.

    Call only in an isolated worker, and discard any result received after the
    cycle deadline. A result persisted after that deadline is merely the model
    review, not evidence that the signal was executed. Stops and sizes come
    from the strategy and risk engine, not the model's proposal.
    """
    if ctx.config.app.mode != "paper":
        return PaperConfirmation(False, "paper_mode_required")
    try:
        settings = _settings(ctx)
    except Exception:
        return PaperConfirmation(False, "settings_unavailable")
    if not paper_confirmation_applies(settings, intent):
        return PaperConfirmation(False, "out_of_scope")
    settings = settings.model_copy(
        update={
            "timeout_s": min(settings.timeout_s, 8),
            "council_seats": [
                seat.model_copy(update={"timeout_s": min(seat.timeout_s, 8)})
                for seat in settings.council_seats
            ],
        }
    )
    symbol = settings.auto_symbol
    record_id = uuid.uuid4().hex
    entry: dict[str, Any] = {
        "id": record_id,
        "ts_ms": now_ms,
        "symbol": symbol,
        "provider": settings.provider,
        "model": settings.model,
        "status": "failed",
        "proposal": None,
        "error": None,
        "execution": "paper_strategy_confirmation_only",
        "strategy_intent_id": intent.id,
        "strategy_id": intent.strategy_id,
        "strategy_side": intent.legs[0].side.value,
        "confirmed": False,
    }
    reason = "review_unavailable"
    confirmed = False
    try:
        if not settings.enabled:
            raise ValueError("AI is disabled")
        view = views.get(symbol)
        if view is None:
            raise ValueError("cycle view unavailable")
        snapshot = build_snapshot(ctx, symbol, now_ms=now_ms)
        if (
            int(view.ts_ms) != snapshot["last_candle_open_ms"]
            or D(str(view.price)) != D(snapshot["reference_price"])
            or D(str(intent.ref_px.get(symbol, 0))) != D(snapshot["reference_price"])
            or intent.ts_ms > now_ms
            or now_ms - intent.ts_ms > 60_000
        ):
            raise ValueError("strategy and snapshot differ")
        entry["snapshot"] = snapshot
        entry["snapshot_hash"] = snapshot_hash(snapshot)
        proposal, usage, council = _decide(settings, snapshot)
        candidate = proposal_to_intent(
            proposal, snapshot, now_ms=int(time.time() * 1000), max_age_ms=60_000
        )
        expected_action = "long" if intent.legs[0].side is Side.BUY else "short"
        confirmed = candidate is not None and proposal.action == expected_action
        reason = "confirmed" if confirmed else "model_wait_or_disagreed"
        entry.update(
            {
                "status": "completed",
                "proposal": proposal.model_dump(mode="json"),
                "usage": usage,
                "council": council,
                "confirmed": confirmed,
                "confirmation_reason": reason,
            }
        )
    except Exception:
        # Includes timeout, invalid response, mismatch and expired snapshots.
        entry["error"] = "model request or strategy confirmation failed"
    try:
        _append_history(ctx, entry)
    except Exception:
        # An unrecorded review cannot authorize a strategy entry.
        return PaperConfirmation(False, "audit_unavailable")
    return PaperConfirmation(confirmed, reason, record_id)


def collect_paper_intent(ctx: Any, views: dict, *, now_ms: int) -> Any:
    """Optionally produce ONE paper intent from the exact current bar snapshot.

    The caller MUST run the returned intent through its ordinary risk engine
    and OMS. Live/backtest paths cannot use this adapter. A failure, missing
    view, changed bar, wrong stop, or expired response results in no intent.
    The model never receives a broker, account credentials, or order access.
    """
    if ctx.config.app.mode != "paper":
        return None
    try:
        settings = _settings(ctx)
    except Exception:
        return None
    if not settings.enabled or not settings.auto_paper_enabled:
        return None
    # The trading cycle only accepts results for 15 seconds. Bound each HTTP
    # read/connect timeout more tightly; late results are still discarded by
    # the caller, since DNS and repeated reads need not obey a total deadline.
    settings = settings.model_copy(
        update={
            "timeout_s": min(settings.timeout_s, 8),
            "council_seats": [
                seat.model_copy(update={"timeout_s": min(seat.timeout_s, 8)})
                for seat in settings.council_seats
            ],
        }
    )
    symbol = settings.auto_symbol
    view = views.get(symbol)
    if view is None:
        return None
    entry: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "ts_ms": now_ms,
        "symbol": symbol,
        "provider": settings.provider,
        "model": settings.model,
        "effective_timeout_s": settings.timeout_s,
        "status": "failed",
        "proposal": None,
        "error": None,
        "execution": "paper_candidate_only",
    }
    try:
        snapshot = build_snapshot(ctx, symbol, now_ms=now_ms)
        if int(view.ts_ms) != snapshot["last_candle_open_ms"] or D(str(view.price)) != D(
            snapshot["reference_price"]
        ):
            raise ValueError("snapshot does not match cycle view")
        entry["snapshot"] = snapshot
        entry["snapshot_hash"] = snapshot_hash(snapshot)
        proposal, usage, council = _decide(settings, snapshot)
        intent = proposal_to_intent(
            proposal, snapshot, now_ms=int(time.time() * 1000), max_age_ms=60_000
        )
        entry.update(
            {
                "status": "completed",
                "proposal": proposal.model_dump(mode="json"),
                "usage": usage,
                "council": council,
                "intent": None if intent is None else intent.as_dict(),
            }
        )
    except Exception:
        entry["error"] = "model request failed or returned an invalid or expired proposal"
        intent = None
    try:
        _append_history(ctx, entry)
    except Exception:
        # An unrecorded AI proposal cannot safely be traced or executed.
        return None
    return intent
