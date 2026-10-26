"""Persistence gateway.

Every write path here is called inside a :class:`Database.tx` block together
with its event, so an event is never recorded for a projection that did not
land, and a projection never lands without its event.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from oq.domain.events import Event, EventType
from oq.domain.order import Fill, Order
from oq.domain.portfolio import Portfolio
from oq.domain.position import Position
from oq.storage.db import Database
from oq.types import ZERO, Book, D, InstType


@dataclass(frozen=True, slots=True)
class TradeRecord:
    id: str
    symbol: str
    book: Book
    strategy_id: str
    pos_side: str
    entry_ms: int
    exit_ms: int
    entry_px: Decimal
    exit_px: Decimal
    qty: Decimal
    pnl: Decimal
    fees: Decimal
    funding: Decimal
    r_multiple: Decimal | None
    reason: str

    @property
    def duration_ms(self) -> int:
        return max(0, self.exit_ms - self.entry_ms)

    def as_row(self) -> tuple[Any, ...]:
        return (
            self.id,
            self.symbol,
            self.book.value,
            self.strategy_id,
            self.pos_side,
            self.entry_ms,
            self.exit_ms,
            str(self.entry_px),
            str(self.exit_px),
            str(self.qty),
            str(self.pnl),
            str(self.fees),
            str(self.funding),
            None if self.r_multiple is None else str(self.r_multiple),
            self.reason,
            self.duration_ms,
        )


class Store:
    def __init__(self, db: Database, *, config_hash: str = "", code_version: str = "") -> None:
        self.db = db
        self.config_hash = config_hash
        self.code_version = code_version

    # -- events --------------------------------------------------------
    @staticmethod
    def append_event(
        conn: sqlite3.Connection,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        ts_ms: int,
        cycle_id: str | None = None,
        symbol: str | None = None,
        config_hash: str | None = None,
        code_version: str | None = None,
    ) -> str:
        event = Event(
            type=event_type,
            payload=payload,
            ts_ms=ts_ms,
            cycle_id=cycle_id,
            symbol=symbol,
            config_hash=config_hash,
            code_version=code_version,
        )
        conn.execute(
            "INSERT INTO events(id, ts_ms, cycle_id, type, symbol, payload,"
            " config_hash, code_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            event.to_row(),
        )
        return event.id

    def emit(
        self,
        conn: sqlite3.Connection,
        event_type: EventType,
        payload: Mapping[str, Any],
        *,
        ts_ms: int,
        cycle_id: str | None = None,
        symbol: str | None = None,
    ) -> str:
        return self.append_event(
            conn,
            event_type,
            payload,
            ts_ms=ts_ms,
            cycle_id=cycle_id,
            symbol=symbol,
            config_hash=self.config_hash,
            code_version=self.code_version,
        )

    # -- instruments ---------------------------------------------------
    def upsert_instruments(self, conn: sqlite3.Connection, instruments: Iterable[Any]) -> None:
        rows = [
            (
                i.symbol,
                i.inst_type.value,
                i.base,
                i.quote,
                str(i.tick_size),
                str(i.lot_size),
                str(i.min_size),
                str(i.contract_value),
                str(i.maker_fee),
                str(i.taker_fee),
                str(i.max_leverage),
                _now_ms(),
            )
            for i in instruments
        ]
        conn.executemany(
            """
            INSERT INTO instruments(symbol, inst_type, base, quote, tick_size, lot_size,
                                    min_size, contract_value, maker_fee, taker_fee,
                                    max_leverage, updated_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                inst_type=excluded.inst_type, base=excluded.base, quote=excluded.quote,
                tick_size=excluded.tick_size, lot_size=excluded.lot_size,
                min_size=excluded.min_size, contract_value=excluded.contract_value,
                maker_fee=excluded.maker_fee, taker_fee=excluded.taker_fee,
                max_leverage=excluded.max_leverage, updated_ms=excluded.updated_ms
            """,
            rows,
        )

    # -- orders / fills ------------------------------------------------
    def save_order(self, conn: sqlite3.Connection, order: Order) -> None:
        conn.execute(
            """
            INSERT INTO orders(client_id, venue_order_id, intent_id, cycle_id, symbol, inst_type,
                               side, order_type, tif, px, qty, filled_qty, avg_px, status,
                               reduce_only, fee, strategy_id, book, created_ms, updated_ms, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(client_id) DO UPDATE SET
                venue_order_id=excluded.venue_order_id, filled_qty=excluded.filled_qty,
                avg_px=excluded.avg_px, status=excluded.status, fee=excluded.fee,
                updated_ms=excluded.updated_ms, error=excluded.error
            """,
            (
                order.client_id,
                order.venue_order_id,
                order.intent_id,
                order.cycle_id,
                order.symbol,
                order.inst_type.value,
                order.side.value,
                order.order_type.value,
                order.tif.value,
                None if order.px is None else str(order.px),
                str(order.qty),
                str(order.filled_qty),
                None if order.avg_px is None else str(order.avg_px),
                order.status.value,
                int(order.reduce_only),
                str(order.fee),
                order.strategy_id,
                order.book.value,
                order.created_ms,
                order.updated_ms,
                order.error,
            ),
        )

    def save_fill(self, conn: sqlite3.Connection, fill: Fill) -> None:
        fill_id = fill.venue_fill_id or f"{fill.order_client_id}:{fill.ts_ms}:{fill.qty}"
        conn.execute(
            """
            INSERT OR IGNORE INTO fills(id, order_client_id, venue_fill_id, ts_ms, symbol, side,
                                        qty, px, fee, fee_ccy, liquidity)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                fill_id,
                fill.order_client_id,
                fill.venue_fill_id,
                fill.ts_ms,
                fill.symbol,
                fill.side.value,
                str(fill.qty),
                str(fill.px),
                str(fill.fee),
                fill.fee_ccy,
                fill.liquidity.value,
            ),
        )

    # -- positions -----------------------------------------------------
    def sync_positions(self, conn: sqlite3.Connection, portfolio: Portfolio) -> None:
        existing = {
            (row["symbol"], row["book"])
            for row in conn.execute("SELECT symbol, book FROM positions")
        }
        for position in portfolio.positions.values():
            self._upsert_position(conn, position)
            existing.discard((position.symbol, position.book.value))
        for symbol, book in existing:
            conn.execute("DELETE FROM positions WHERE symbol=? AND book=?", (symbol, book))

    @staticmethod
    def _upsert_position(conn: sqlite3.Connection, p: Position) -> None:
        conn.execute(
            """
            INSERT INTO positions(symbol, book, inst_type, strategy_id, qty, avg_px, stop_px,
                                  tp_px, leverage, realized_pnl, fees_paid, funding_net,
                                  initial_qty, tp1_done, tp2_done, time_stop_bars, opened_bar,
                                  opened_ms, updated_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol, book) DO UPDATE SET
                inst_type=excluded.inst_type, strategy_id=excluded.strategy_id,
                qty=excluded.qty, avg_px=excluded.avg_px, stop_px=excluded.stop_px,
                tp_px=excluded.tp_px, leverage=excluded.leverage,
                realized_pnl=excluded.realized_pnl, fees_paid=excluded.fees_paid,
                funding_net=excluded.funding_net, initial_qty=excluded.initial_qty,
                tp1_done=excluded.tp1_done, tp2_done=excluded.tp2_done,
                time_stop_bars=excluded.time_stop_bars, opened_bar=excluded.opened_bar,
                updated_ms=excluded.updated_ms
            """,
            (
                p.symbol,
                p.book.value,
                p.inst_type.value,
                p.strategy_id,
                str(p.qty),
                str(p.avg_px),
                None if p.stop_px is None else str(p.stop_px),
                None if p.tp_px is None else str(p.tp_px),
                str(p.leverage),
                str(p.realized_pnl),
                str(p.fees_paid),
                str(p.funding_net),
                str(p.initial_qty),
                int(p.tp1_done),
                int(p.tp2_done),
                p.time_stop_bars,
                p.opened_bar,
                p.opened_ms,
                p.updated_ms,
            ),
        )

    # -- decisions -----------------------------------------------------
    def save_decision(
        self,
        conn: sqlite3.Connection,
        *,
        decision_id: str,
        ts_ms: int,
        cycle_id: str | None,
        strategy_id: str,
        book: Book,
        action: str,
        symbols: Sequence[str],
        approved: bool,
        intent: Mapping[str, Any],
        risk: Mapping[str, Any],
        gates: Sequence[Mapping[str, Any]],
    ) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO decisions(id, ts_ms, cycle_id, strategy_id, book, action,
                                             symbols, approved, intent, risk, gates, config_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                decision_id,
                ts_ms,
                cycle_id,
                strategy_id,
                book.value,
                action,
                ",".join(symbols),
                int(approved),
                json.dumps(intent, default=str, sort_keys=True),
                json.dumps(risk, default=str, sort_keys=True),
                json.dumps(list(gates), default=str),
                self.config_hash,
            ),
        )

    # -- equity / trades / funding ------------------------------------
    def save_equity(
        self, conn: sqlite3.Connection, ts_ms: int, snapshot: Mapping[str, Any]
    ) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO equity(ts_ms, equity, cash, gross_notional, net_notional,
                                          margin_used, open_risk, heat, drawdown, day_pnl, week_pnl)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts_ms,
                str(snapshot["equity"]),
                str(snapshot.get("cash", 0)),
                str(snapshot.get("gross_notional", 0)),
                str(snapshot.get("net_notional", 0)),
                str(snapshot.get("margin_used", 0)),
                str(snapshot.get("open_risk", 0)),
                str(snapshot.get("heat", 0)),
                str(snapshot.get("drawdown", 0)),
                str(snapshot.get("day_pnl", 0)),
                str(snapshot.get("week_pnl", 0)),
            ),
        )

    def save_trade(self, conn: sqlite3.Connection, trade: TradeRecord) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO trades(id, symbol, book, strategy_id, pos_side, entry_ms,
                                          exit_ms, entry_px, exit_px, qty, pnl, fees, funding,
                                          r_multiple, reason, duration_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            trade.as_row(),
        )

    def save_funding(
        self,
        conn: sqlite3.Connection,
        *,
        ts_ms: int,
        symbol: str,
        book: Book,
        amount: Decimal,
        rate: Decimal,
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO funding(ts_ms, symbol, book, amount, rate) VALUES (?,?,?,?,?)",
            (ts_ms, symbol, book.value, str(amount), str(rate)),
        )

    # -- key/value runtime state --------------------------------------
    def set_kv(self, conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO kv(key, value, updated_ms) VALUES (?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ms=excluded.updated_ms",
            (key, json.dumps(value, default=str), _now_ms()),
        )

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self.db.query_one("SELECT value FROM kv WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    # -- audit ---------------------------------------------------------
    def audit(self, conn: sqlite3.Connection, actor: str, action: str, detail: Any = None) -> None:
        conn.execute(
            "INSERT INTO audit(ts_ms, actor, action, detail) VALUES (?,?,?,?)",
            (_now_ms(), actor, action, json.dumps(detail, default=str, sort_keys=True)),
        )

    # -- reads used by the API ----------------------------------------
    def equity_series(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM equity ORDER BY ts_ms DESC LIMIT ?", (limit,))
        return [
            {
                "ts_ms": r["ts_ms"],
                "equity": r["equity"],
                "drawdown": r["drawdown"],
                "heat": r["heat"],
                "day_pnl": r["day_pnl"],
            }
            for r in reversed(rows)
        ]

    def recent_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM decisions ORDER BY ts_ms DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "ts_ms": r["ts_ms"],
                    "cycle_id": r["cycle_id"],
                    "strategy_id": r["strategy_id"],
                    "book": r["book"],
                    "action": r["action"],
                    "symbols": r["symbols"],
                    "approved": bool(r["approved"]),
                    "gates": json.loads(r["gates"]),
                    "risk": json.loads(r["risk"]),
                    "intent": json.loads(r["intent"]),
                }
            )
        return out

    def recent_trades(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM trades ORDER BY exit_ms DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def open_orders(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM orders WHERE status IN ('submitting','acked','partial','unknown')"
            " ORDER BY created_ms DESC"
        )
        return [dict(r) for r in rows]

    def load_positions(self) -> list[Position]:
        rows = self.db.query("SELECT * FROM positions")
        out: list[Position] = []
        for r in rows:
            out.append(
                Position(
                    symbol=r["symbol"],
                    book=Book(r["book"]),
                    inst_type=InstType(r["inst_type"]),
                    strategy_id=r["strategy_id"] or "",
                    qty=D(r["qty"]),
                    avg_px=D(r["avg_px"]),
                    opened_ms=int(r["opened_ms"]),
                    updated_ms=int(r["updated_ms"]),
                    stop_px=None if r["stop_px"] is None else D(r["stop_px"]),
                    tp_px=None if r["tp_px"] is None else D(r["tp_px"]),
                    leverage=D(r["leverage"]),
                    realized_pnl=D(r["realized_pnl"]),
                    fees_paid=D(r["fees_paid"]),
                    funding_net=D(r["funding_net"]),
                    initial_qty=D(r["initial_qty"]),
                    tp1_done=bool(r["tp1_done"]),
                    tp2_done=bool(r["tp2_done"]),
                    time_stop_bars=r["time_stop_bars"],
                    opened_bar=r["opened_bar"],
                )
            )
        return out

    def load_portfolio(self, default_cash: Decimal = ZERO) -> Portfolio:
        """Rebuild the in-memory book from kv + positions.

        ``default_cash`` seeds a fresh paper account; without it a new database
        would report 0 equity and every position would be sized to nothing.
        """
        cash = D(self.get_kv("cash", str(default_cash)))
        portfolio = Portfolio(cash=cash)
        for position in self.load_positions():
            portfolio.positions[(position.symbol, position.book)] = position
        portfolio.realized_pnl = D(self.get_kv("realized_pnl", "0"))
        portfolio.fees_paid = D(self.get_kv("fees_paid", "0"))
        portfolio.funding_net = D(self.get_kv("funding_net", "0"))
        portfolio.peak_equity = D(self.get_kv("peak_equity", str(cash)))
        portfolio.day_key = self.get_kv("day_key", "")
        portfolio.week_key = self.get_kv("week_key", "")
        portfolio.day_start_equity = D(self.get_kv("day_start_equity", str(cash)))
        portfolio.week_start_equity = D(self.get_kv("week_start_equity", str(cash)))
        portfolio.initial_equity = D(self.get_kv("initial_equity", str(cash)))
        return portfolio

    def save_portfolio_state(self, conn: sqlite3.Connection, portfolio: Portfolio) -> None:
        for key, value in (
            ("cash", str(portfolio.cash)),
            ("realized_pnl", str(portfolio.realized_pnl)),
            ("fees_paid", str(portfolio.fees_paid)),
            ("funding_net", str(portfolio.funding_net)),
            ("peak_equity", str(portfolio.peak_equity)),
            ("day_key", portfolio.day_key),
            ("week_key", portfolio.week_key),
            ("day_start_equity", str(portfolio.day_start_equity)),
            ("week_start_equity", str(portfolio.week_start_equity)),
            ("initial_equity", str(portfolio.initial_equity)),
        ):
            self.set_kv(conn, key, value)

    def last_cycle_ms(self) -> int:
        row = self.db.query_one("SELECT MAX(ts_ms) AS t FROM events WHERE type='cycle_end'")
        return int(row["t"]) if row and row["t"] is not None else 0

    def stats(self) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(CAST(pnl AS REAL)),0) AS pnl,"
            " COALESCE(SUM(CAST(fees AS REAL)),0) AS fees,"
            " COALESCE(SUM(CAST(funding AS REAL)),0) AS funding FROM trades"
        )
        assert row is not None
        wins = self.db.query_one("SELECT COUNT(*) AS n FROM trades WHERE CAST(pnl AS REAL) > 0")
        total = int(row["n"])
        wins_n = int(wins["n"]) if wins else 0
        return {
            "trades": total,
            "net_pnl": row["pnl"],
            "fees": row["fees"],
            "funding": row["funding"],
            "win_rate": (wins_n / total) if total else 0.0,
        }


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


__all__ = ["ZERO", "Store", "TradeRecord"]
