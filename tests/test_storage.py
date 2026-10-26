from __future__ import annotations

from decimal import Decimal

from oq.domain.events import EventType, new_event_id
from oq.domain.order import make_client_order_id
from oq.storage import Database, Store
from oq.types import Book, D, InstType, OrderStatus, Side


def test_schema_migrates_once_and_is_idempotent(db: Database):
    assert db.migrate() == 1
    assert db.migrate() == 1
    report = db.verify()
    assert report["ok"] is True
    assert report["schema_version"] == 1


def test_event_and_projection_commit_together(db: Database, store: Store):
    event_id = new_event_id()
    with db.tx() as conn:
        store.emit(conn, EventType.CYCLE_START, {"x": 1}, ts_ms=1_700_000_000_000)
    rows = store.db.query("SELECT COUNT(*) AS n FROM events")
    assert rows[0]["n"] == 1
    assert event_id  # ids are generated even when not passed explicitly


def test_a_failed_transaction_rolls_back_both_sides(db: Database, store: Store):
    try:
        with db.tx() as conn:
            store.emit(conn, EventType.ERROR, {"boom": True}, ts_ms=1)
            raise RuntimeError("simulated failure")
    except RuntimeError:
        pass
    assert store.db.query("SELECT COUNT(*) AS n FROM events")[0]["n"] == 0


def test_kv_roundtrip(db: Database, store: Store):
    with db.tx() as conn:
        store.set_kv(conn, "answer", {"value": 42})
    assert store.get_kv("answer")["value"] == 42
    assert store.get_kv("missing", "fallback") == "fallback"


def test_position_sync_and_load(db: Database, store: Store, portfolio):
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("60000"),
        fee=D("0.3"),
        ts_ms=1_700_000_000_000,
        stop_px=D("59000"),
        strategy_id="trend_donchian",
    )
    with db.tx() as conn:
        store.sync_positions(conn, portfolio)
        store.save_portfolio_state(conn, portfolio)
    loaded = store.load_positions()
    assert len(loaded) == 1
    assert loaded[0].symbol == "BTC-USDT-SWAP"
    assert loaded[0].stop_px == D("59000")
    restored = store.load_portfolio()
    assert restored.cash == portfolio.cash


def test_equity_series_is_ordered(db: Database, store: Store):
    for i in range(5):
        with db.tx() as conn:
            store.save_equity(conn, 1_700_000_000_000 + i * 60_000, {"equity": str(10000 + i)})
    series = store.equity_series(limit=3)
    assert len(series) == 3
    assert float(series[-1]["equity"]) == 10004.0


def test_trade_persistence_roundtrip(db: Database, store: Store):
    from oq.storage import TradeRecord

    trade = TradeRecord(
        id=new_event_id(),
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        strategy_id="trend_donchian",
        pos_side="long",
        entry_ms=1,
        exit_ms=2,
        entry_px=D("60000"),
        exit_px=D("61000"),
        qty=D("0.01"),
        pnl=D("10"),
        fees=D("0.6"),
        funding=D("0"),
        r_multiple=D("1.6"),
        reason="tp2",
    )
    with db.tx() as conn:
        store.save_trade(conn, trade)
    rows = store.recent_trades(limit=5)
    assert rows[0]["symbol"] == "BTC-USDT-SWAP"
    assert Decimal(str(rows[0]["pnl"])) == D("10")


def test_order_and_fill_persist_with_client_id(db: Database, store: Store):
    from oq.domain.order import Order

    order = Order(
        client_id=make_client_order_id("intent-1", 0),
        symbol="BTC-USDT-SWAP",
        inst_type=InstType.SWAP,
        side=Side.BUY,
        order_type=__import__("oq.types", fromlist=["OrderType"]).OrderType.MARKET,
        qty=D("0.01"),
        status=OrderStatus.SUBMITTING,
        created_ms=1_700_000_000_000,
        updated_ms=1_700_000_000_000,
    )
    assert order.client_id.startswith("oq")
    with db.tx() as conn:
        store.save_order(conn, order)
    assert store.open_orders()[0]["client_id"] == order.client_id


def test_instrument_specs_are_stored(db: Database, store: Store, instruments):
    with db.tx() as conn:
        store.upsert_instruments(conn, instruments.values())
    rows = db.query("SELECT symbol, inst_type FROM instruments ORDER BY symbol")
    assert len(rows) == len(instruments)
    assert {row["symbol"] for row in rows} == set(instruments)


def test_verify_detects_a_fill_mismatch(db: Database, store: Store, portfolio):
    portfolio.apply_fill(
        symbol="BTC-USDT-SWAP",
        book=Book.DIRECTIONAL,
        inst_type=InstType.SWAP,
        side=Side.BUY,
        qty=D("0.01"),
        px=D("60000"),
        fee=D("0"),
        ts_ms=1,
    )
    with db.tx() as conn:
        store.sync_positions(conn, portfolio)
    # Corrupt the projection on purpose.
    with db.tx() as conn:
        conn.execute("UPDATE positions SET qty = '0.5'")
    report = db.verify()
    assert report["ok"] is False
