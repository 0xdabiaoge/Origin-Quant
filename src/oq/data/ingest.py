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
        cutoff = max(existing, target) if existing else target
        cursor: int | None = None
        rows: list[Bar] = []
        requests = 0
        # Start at the latest page and walk backwards. OKX `after` is an
        # exclusive older-than cursor, so passing last_bar_ms initially would
        # fetch older data and never refresh the latest candles.
        while requests < max_requests:
            batch = self.rest.candles(
                symbol,
                timeframe,
                limit=100,
                after_ms=cursor,
                history=True,
            )
            if not batch:
                if existing and cursor is not None and cursor > cutoff:
                    raise RuntimeError(
                        f"incomplete candle history for {symbol}: gap before {cursor}"
                    )
                break
            fresh = [b for b in batch if b.ts_ms >= cutoff]
            rows.extend(fresh)
            requests += 1
            oldest = min(b.ts_ms for b in batch)
            if cursor is not None and oldest >= cursor:
                raise RuntimeError(f"candle pagination did not advance for {symbol}")
            if oldest <= cutoff:
                break
            cursor = oldest
        else:
            raise RuntimeError(f"candle pagination limit reached for {symbol}")
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

    def sync_funding(self, symbol: str, *, days: int = 365, max_requests: int = 400) -> int:
        if self.rest is None:
            raise RuntimeError("no REST client configured")
        target = int(time.time() * 1000) - days * 86_400_000
        points: list[FundingPoint] = []
        cursor: int | None = None
        for _ in range(max_requests):
            batch = self.rest.funding_history(symbol, limit=100, after_ms=cursor)
            if not batch:
                if cursor is not None and cursor > target:
                    # The venue only retains a limited history. The returned
                    # data must not be mistaken for the full requested range.
                    log.warning("funding history for %s ends before requested range", symbol)
                break
            points.extend(point for point in batch if point.ts_ms >= target)
            oldest = min(point.ts_ms for point in batch)
            if cursor is not None and oldest >= cursor:
                raise RuntimeError(f"funding pagination did not advance for {symbol}")
            if oldest <= target:
                break
            cursor = oldest
        else:
            raise RuntimeError(f"funding pagination limit reached for {symbol}")
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
