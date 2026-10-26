"""SQLite access: WAL, explicit transactions, one writer.

Why SQLite: a single-operator system writes a few dozen rows per minute.
Postgres would add an operational dependency for no benefit; JSON files (the
choice made by both reference projects) cannot do multi-table atomicity and
that is exactly where their corruption bugs came from.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- connections ---------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.path),
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Write transaction.  ``BEGIN IMMEDIATE`` fails fast instead of deadlocking."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.read() as conn:
            return list(conn.execute(sql, params))

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self.read() as conn:
            return conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self.tx() as conn:
            conn.execute(sql, params)

    # -- lifecycle -----------------------------------------------------
    def migrate(self) -> int:
        schema_file = Path(__file__).with_name("schema.sql")
        with self.read() as conn:
            conn.executescript(schema_file.read_text(encoding="utf-8"))
            row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = int(row["v"]) if row and row["v"] is not None else 0
            if current < SCHEMA_VERSION:
                conn.execute("BEGIN")
                conn.execute(
                    "INSERT INTO schema_version(version, applied_ms) VALUES (?, ?)",
                    (SCHEMA_VERSION, _now_ms()),
                )
                conn.execute("COMMIT")
        return SCHEMA_VERSION

    def close(self) -> None:
        """No persistent connection is held; kept for a tidy call site."""
        return

    def verify(self) -> dict[str, Any]:
        """Integrity report used by `oq verify` and the API health page."""
        report: dict[str, Any] = {
            "path": str(self.path),
            "ok": True,
            "checks": {},
            "schema_version": SCHEMA_VERSION,
        }
        with self.read() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            report["checks"]["integrity"] = integrity
            if integrity != "ok":
                report["ok"] = False
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            report["checks"]["foreign_keys"] = len(fk)
            if fk:
                report["ok"] = False

            counts = {}
            for table in (
                "events",
                "orders",
                "fills",
                "positions",
                "decisions",
                "trades",
                "equity",
                "funding",
            ):
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            report["counts"] = counts

            # Sum of fills must equal filled order quantity.
            mismatch = conn.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT o.client_id,
                           CAST(o.filled_qty AS REAL) AS of,
                           COALESCE(SUM(CAST(f.qty AS REAL)), 0) AS ff
                    FROM orders o LEFT JOIN fills f ON f.order_client_id = o.client_id
                    GROUP BY o.client_id
                    HAVING ABS(of - ff) > 1e-9
                )
                """
            ).fetchone()[0]
            report["checks"]["fill_mismatch"] = mismatch
            if mismatch:
                report["ok"] = False

            orphan = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE CAST(qty AS REAL) = 0"
            ).fetchone()[0]
            report["checks"]["zero_positions"] = orphan
            if orphan:
                report["ok"] = False

            # Positions must equal the signed sum of their fills.  This is the
            # check that catches a projection written outside the transaction
            # that produced its events.
            position_mismatch = conn.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT p.symbol,
                           p.book,
                           CAST(p.qty AS REAL) AS pq,
                           COALESCE((
                               SELECT SUM(CASE WHEN o2.side = 'buy'
                                               THEN CAST(f2.qty AS REAL)
                                               ELSE -CAST(f2.qty AS REAL) END)
                               FROM fills f2
                               JOIN orders o2 ON o2.client_id = f2.order_client_id
                               WHERE f2.symbol = p.symbol AND o2.book = p.book
                           ), 0) AS fq
                    FROM positions p
                    WHERE ABS(CAST(p.qty AS REAL) - COALESCE((
                        SELECT SUM(CASE WHEN o3.side = 'buy'
                                        THEN CAST(f3.qty AS REAL)
                                        ELSE -CAST(f3.qty AS REAL) END)
                        FROM fills f3
                        JOIN orders o3 ON o3.client_id = f3.order_client_id
                        WHERE f3.symbol = p.symbol AND o3.book = p.book
                    ), 0)) > 1e-9
                )
                """
            ).fetchone()[0]
            report["checks"]["position_mismatch"] = position_mismatch
            if position_mismatch:
                report["ok"] = False
        return report


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
