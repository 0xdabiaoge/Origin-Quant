"""Read models for the admin console and the trading workspace.

Everything here is a thin projection over tables the engine already writes, so
the console can never disagree with the ledger: no derived state is cached.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from oq.types import D


def _now_ms() -> int:
    return int(time.time() * 1000)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# -- audit ------------------------------------------------------------------
def record_audit(
    conn: sqlite3.Connection,
    actor: str,
    action: str,
    detail: Any = None,
) -> None:
    conn.execute(
        "INSERT INTO audit(ts_ms, actor, action, detail) VALUES (?,?,?,?)",
        (_now_ms(), actor, action, json.dumps(detail, default=str) if detail is not None else None),
    )


def list_audit(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    offset: int = 0,
    actor: str | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    if actor:
        where.append("actor = ?")
        params.append(actor)
    if action:
        where.append("action LIKE ?")
        params.append(f"{action}%")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = conn.execute(f"SELECT COUNT(*) AS n FROM audit {clause}", tuple(params)).fetchone()
    rows = _rows(
        conn,
        f"SELECT id, ts_ms, actor, action, detail FROM audit {clause}"
        f" ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    for row in rows:
        if row["detail"]:
            try:
                row["detail"] = json.loads(row["detail"])
            except (TypeError, ValueError):
                pass
    return {"items": rows, "total": int(total["n"]) if total else 0}


# -- overview ---------------------------------------------------------------
def system_overview(conn: sqlite3.Connection, *, uptime_s: float, version: str) -> dict[str, Any]:
    def count(table: str) -> int:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"]) if row else 0

    first = conn.execute("SELECT MIN(ts_ms) AS t FROM events").fetchone()
    last = conn.execute("SELECT MAX(ts_ms) AS t FROM events").fetchone()
    by_type = _rows(
        conn,
        "SELECT type, COUNT(*) AS n FROM events GROUP BY type ORDER BY n DESC LIMIT 12",
    )
    return {
        "version": version,
        "uptime_s": round(uptime_s, 1),
        "counts": {
            "events": count("events"),
            "orders": count("orders"),
            "fills": count("fills"),
            "trades": count("trades"),
            "positions": count("positions"),
            "decisions": count("decisions"),
            "users": count("users"),
            "audit": count("audit"),
        },
        "first_event_ms": first["t"] if first else None,
        "last_event_ms": last["t"] if last else None,
        "events_by_type": by_type,
    }


# -- workspace read models --------------------------------------------------
def open_orders(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    return _rows(
        conn,
        "SELECT client_id, venue_order_id, symbol, side, order_type, qty, filled_qty, avg_px,"
        " status, fee, reduce_only, created_ms FROM orders"
        " WHERE status NOT IN ('filled','cancelled','rejected')"
        " ORDER BY created_ms DESC LIMIT ?",
        (limit,),
    )


def recent_fills(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    return _rows(
        conn,
        "SELECT ts_ms, symbol, side, qty, px, fee, liquidity, order_client_id"
        " FROM fills ORDER BY ts_ms DESC LIMIT ?",
        (limit,),
    )


def recent_decisions(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    return _rows(
        conn,
        "SELECT id, ts_ms, cycle_id, strategy_id, book, action, symbols, approved, gates"
        " FROM decisions ORDER BY ts_ms DESC LIMIT ?",
        (limit,),
    )


def equity_series(conn: sqlite3.Connection, limit: int = 600) -> list[dict]:
    return _rows(
        conn,
        "SELECT ts_ms, equity, cash, gross_notional, net_notional, open_risk, heat, drawdown,"
        " day_pnl, week_pnl FROM equity ORDER BY ts_ms DESC LIMIT ?",
        (limit,),
    )[::-1]


def trade_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) AS n,"
        " COALESCE(SUM(pnl),0) AS net,"
        " COALESCE(SUM(fees),0) AS fees,"
        " COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),0) AS wins,"
        " COALESCE(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),0) AS gross_win,"
        " COALESCE(SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END),0) AS gross_loss"
        " FROM trades"
    ).fetchone()
    if not row or not row["n"]:
        return {"trades": 0, "net_pnl": "0", "fees": "0", "win_rate": 0.0, "profit_factor": None}
    wins = int(row["wins"])
    gross_loss = D(str(row["gross_loss"] or 0))
    gross_win = D(str(row["gross_win"] or 0))
    return {
        "trades": int(row["n"]),
        "net_pnl": str(D(str(row["net"]))),
        "fees": str(D(str(row["fees"]))),
        "win_rate": round(wins / int(row["n"]), 4),
        "profit_factor": str(gross_win / gross_loss) if gross_loss > 0 else None,
    }


def event_feed(conn: sqlite3.Connection, limit: int = 200, since_id: int = 0) -> list[dict]:
    return _rows(
        conn,
        "SELECT id, ts_ms, type, symbol, payload FROM events WHERE id > ? ORDER BY id DESC LIMIT ?",
        (since_id, limit),
    )


def decision_reason_histogram(conn: sqlite3.Connection, limit: int = 2000) -> list[dict]:
    return _rows(
        conn,
        "SELECT gate, n FROM ("
        "  SELECT gate, COUNT(*) AS n FROM ("
        "    SELECT j.value->>'code' AS gate, approved"
        "    FROM decisions, json_each(decisions.gates) AS j"
        "    ORDER BY ts_ms DESC LIMIT ?"
        "  ) WHERE approved = 0 GROUP BY gate ORDER BY n DESC LIMIT 20"
        ")",
        (limit,),
    )


__all__ = [
    "decision_reason_histogram",
    "equity_series",
    "event_feed",
    "list_audit",
    "open_orders",
    "recent_decisions",
    "recent_fills",
    "record_audit",
    "system_overview",
    "trade_stats",
]
