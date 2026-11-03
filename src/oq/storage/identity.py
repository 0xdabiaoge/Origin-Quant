"""Bind an execution ledger to one environment without guessing legacy history."""

import hashlib
import json
from typing import Any


def bind_ledger(db: Any, store: Any, mode: str, rest: Any) -> None:
    if mode == "backtest":
        return
    identity = {"mode": mode}
    if mode == "live":
        if not rest.has_credentials:
            raise RuntimeError("private venue credentials required before binding a live ledger")
        identity.update(
            environment="okx-demo" if rest.demo else "okx-live",
            credential_fingerprint=hashlib.sha256(rest.api_key.encode()).hexdigest(),
        )
    with db.tx() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key='ledger.identity.v1'").fetchone()
        if row:
            if json.loads(row["value"]) != identity:
                raise RuntimeError(
                    "ledger environment or credential identity differs; use a separate database"
                )
            return
        tables = ("orders", "fills", "positions", "trades", "equity", "funding", "events")
        if any(conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in tables):
            raise RuntimeError(
                "legacy ledger has trading history without environment identity; preserve and back up "
                "this database, verify its origin offline, and use a separate empty execution database"
            )
        store.set_kv(conn, "ledger.identity.v1", identity)
