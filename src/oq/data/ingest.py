"""Data ingestion: OKX history pagination and synthetic seeding."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from oq.config import Config
from oq.data.models import Bar, FundingPoint
from oq.data.okx_rest import OkxError, OkxRest
from oq.data.store import ParquetStore
from oq.data.synthetic import generate

log = logging.getLogger(__name__)

_SWAP_SYMBOLS: list[str] = []


@dataclass
class SyncReport:
    symbol: str
    timeframe: str
    added: int
    total: int
    first_ms: int
    last_ms: int
    source: str


class Ingestor:
    def __init__(self, config: Config, store: ParquetStore, rest: OkxRest | None) -> None:
        self.config = config
        self.store = store
        self.rest = rest

    # -- live ----------------------------------------------------------
    def sync_bars(
        self,
        symbol: str,
        timeframe: str,
        *,
        days: int = 365,
        max_requests: int = 400,
    ) -> SyncReport:
        if self.rest is None:
            raise RuntimeError("no REST client configured")
        existing = self.store.last_bar_ms(symbol, timeframe)
        now = int(time.time() * 1000)
        target = now - days * 86_400_000
        cursor = max(existing, target) if existing else target
        rows: list[Bar] = []
        requests = 0
        # history-candles walks backwards via `after` = oldest - 1
        while requests < max_requests:
            batch = self.rest.candles(
                symbol,
                timeframe,
                limit=100,
                after_ms=cursor if cursor > target else None,
                history=True,
            )
            if not batch:
                break
            fresh = [b for b in batch if b.ts_ms >= target]
            rows.extend(fresh)
            requests += 1
            oldest = min(b.ts_ms for b in batch)
            if oldest <= target:
                break
            cursor = oldest - 1
        if rows:
            self.store.write_bars(symbol, timeframe, rows)
        frame = self.store.read_bars(symbol, timeframe)
        return SyncReport(
            symbol=symbol,
            timeframe=timeframe,
            added=len(rows),
            total=len(frame),
            first_ms=int(frame["ts_ms"].iloc[0]) if not frame.empty else 0,
            last_ms=int(frame["ts_ms"].iloc[-1]) if not frame.empty else 0,
            source="okx",
        )

    def sync_funding(self, symbol: str, *, days: int = 365) -> int:
        if self.rest is None:
            raise RuntimeError("no REST client configured")
        target = int(time.time() * 1000) - days * 86_400_000
        points: list[FundingPoint] = []
        cursor: int | None = None
        for _ in range(400):
            batch = self.rest.funding_history(symbol, limit=100, before_ms=cursor)
            if not batch:
                break
            points.extend(point for point in batch if point.ts_ms >= target)
            oldest = min(point.ts_ms for point in batch)
            if oldest <= target:
                break
            cursor = oldest
        return self.store.write_funding(symbol, points) if points else 0

    def sync_instruments(self) -> int:
        """Refresh instrument metadata from the venue; returns rows written."""
        from oq.storage import Database

        if self.rest is None:
            return 0
        specs = []
        for inst_type in ("SWAP", "SPOT"):
            try:
                specs.extend(self.rest.instruments(inst_type))  # type: ignore[arg-type]
            except OkxError as exc:
                log.warning("instrument sync failed for %s: %s", inst_type, exc)
        if not specs:
            return 0
        db = Database(self.config.app.db_path)
        db.migrate()
        from oq.storage import Store

        with db.tx() as conn:
            Store(db).upsert_instruments(conn, [s.to_instrument() for s in specs])
        return len(specs)

    # -- synthetic -----------------------------------------------------
    def seed_synthetic(
        self,
        *,
        days: int = 365,
        symbols: Iterable[str] | None = None,
        timeframe: str | None = None,
        seed: int = 7,
        force: bool = False,
    ) -> dict[str, SyncReport]:
        tf = timeframe or self.config.data.timeframe
        wanted = list(symbols or [item.symbol for item in self.config.universe])
        now = int(time.time() * 1000)
        start = (now // 900_000) * 900_000 - days * 86_400_000
        market = generate(wanted, tf, start, days, seed=seed)
        reports: dict[str, SyncReport] = {}
        for symbol, bars in market.bars.items():
            existing = self.store.read_bars(symbol, tf)
            if not existing.empty and not force:
                reports[symbol] = SyncReport(
                    symbol=symbol,
                    timeframe=tf,
                    added=0,
                    total=len(existing),
                    first_ms=int(existing["ts_ms"].iloc[0]),
                    last_ms=int(existing["ts_ms"].iloc[-1]),
                    source="existing",
                )
                continue
            if force:
                path = self.store.bars_path(symbol, tf)
                if path.exists():
                    path.unlink()
            total = self.store.write_bars(symbol, tf, bars)
            reports[symbol] = SyncReport(
                symbol=symbol,
                timeframe=tf,
                added=len(bars),
                total=total,
                first_ms=bars[0].ts_ms if bars else 0,
                last_ms=bars[-1].ts_ms if bars else 0,
                source="synthetic",
            )
        for symbol, points in market.funding.items():
            self.store.write_funding(symbol, points)
        return reports


def bars_from_frame(frame, symbol: str, timeframe: str) -> list[Bar]:
    out = []
    for row in frame.itertuples(index=False):
        out.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                ts_ms=int(row.ts_ms),
                open=_dec(row.open),
                high=_dec(row.high),
                low=_dec(row.low),
                close=_dec(row.close),
                volume=_dec(row.volume),
                confirmed=bool(row.confirmed),
            )
        )
    return out


def _dec(value: object) -> Decimal:
    from oq.types import D as _D

    return _D(value)
