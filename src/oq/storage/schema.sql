-- Origin Quant schema.
-- All monetary columns are TEXT holding exact decimals; SQLite has no
-- decimal type and REAL would corrupt tick-level accounting.

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_ms  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT NOT NULL UNIQUE,
    ts_ms       INTEGER NOT NULL,
    cycle_id    TEXT,
    type        TEXT NOT NULL,
    symbol      TEXT,
    payload     TEXT NOT NULL,
    config_hash TEXT,
    code_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type, ts_ms);

CREATE TABLE IF NOT EXISTS instruments (
    symbol          TEXT PRIMARY KEY,
    inst_type       TEXT NOT NULL,
    base            TEXT NOT NULL,
    quote           TEXT NOT NULL,
    tick_size       TEXT NOT NULL,
    lot_size        TEXT NOT NULL,
    min_size        TEXT NOT NULL,
    contract_value  TEXT NOT NULL,
    maker_fee       TEXT NOT NULL,
    taker_fee       TEXT NOT NULL,
    max_leverage    TEXT NOT NULL,
    updated_ms      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    client_id       TEXT PRIMARY KEY,
    venue_order_id  TEXT,
    intent_id       TEXT,
    cycle_id        TEXT,
    symbol          TEXT NOT NULL,
    inst_type       TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    tif             TEXT NOT NULL,
    px              TEXT,
    qty             TEXT NOT NULL,
    filled_qty      TEXT NOT NULL DEFAULT '0',
    avg_px          TEXT,
    status          TEXT NOT NULL,
    reduce_only     INTEGER NOT NULL DEFAULT 0,
    fee             TEXT NOT NULL DEFAULT '0',
    strategy_id     TEXT,
    book            TEXT NOT NULL,
    created_ms      INTEGER NOT NULL,
    updated_ms      INTEGER NOT NULL,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, updated_ms);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol, created_ms);

CREATE TABLE IF NOT EXISTS fills (
    id              TEXT PRIMARY KEY,
    order_client_id TEXT NOT NULL,
    venue_fill_id   TEXT,
    ts_ms           INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    qty             TEXT NOT NULL,
    px              TEXT NOT NULL,
    fee             TEXT NOT NULL,
    fee_ccy         TEXT NOT NULL DEFAULT 'USDT',
    liquidity       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts_ms);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_client_id);

CREATE TABLE IF NOT EXISTS positions (
    symbol          TEXT NOT NULL,
    book            TEXT NOT NULL,
    inst_type       TEXT NOT NULL,
    strategy_id     TEXT,
    qty             TEXT NOT NULL,
    avg_px          TEXT NOT NULL,
    stop_px         TEXT,
    tp_px           TEXT,
    leverage        TEXT NOT NULL,
    realized_pnl    TEXT NOT NULL DEFAULT '0',
    fees_paid       TEXT NOT NULL DEFAULT '0',
    funding_net     TEXT NOT NULL DEFAULT '0',
    initial_qty     TEXT NOT NULL DEFAULT '0',
    tp1_done        INTEGER NOT NULL DEFAULT 0,
    tp2_done        INTEGER NOT NULL DEFAULT 0,
    time_stop_bars  INTEGER,
    opened_bar      INTEGER,
    opened_ms       INTEGER NOT NULL,
    updated_ms      INTEGER NOT NULL,
    PRIMARY KEY (symbol, book)
);

CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,
    ts_ms           INTEGER NOT NULL,
    cycle_id        TEXT,
    strategy_id     TEXT NOT NULL,
    book            TEXT NOT NULL,
    action          TEXT NOT NULL,
    symbols         TEXT NOT NULL,
    approved        INTEGER NOT NULL,
    intent          TEXT NOT NULL,
    risk            TEXT NOT NULL,
    gates           TEXT NOT NULL,
    config_hash     TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts_ms);

CREATE TABLE IF NOT EXISTS trades (
    id              TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    book            TEXT NOT NULL,
    strategy_id     TEXT,
    pos_side        TEXT NOT NULL,
    entry_ms        INTEGER NOT NULL,
    exit_ms         INTEGER NOT NULL,
    entry_px        TEXT NOT NULL,
    exit_px         TEXT NOT NULL,
    qty             TEXT NOT NULL,
    pnl             TEXT NOT NULL,
    fees            TEXT NOT NULL,
    funding         TEXT NOT NULL,
    r_multiple      TEXT,
    reason          TEXT,
    duration_ms     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_exit ON trades(exit_ms);

CREATE TABLE IF NOT EXISTS equity (
    ts_ms           INTEGER PRIMARY KEY,
    equity          TEXT NOT NULL,
    cash            TEXT NOT NULL,
    gross_notional  TEXT NOT NULL,
    net_notional    TEXT NOT NULL,
    margin_used     TEXT NOT NULL,
    open_risk       TEXT NOT NULL,
    heat            TEXT NOT NULL,
    drawdown        TEXT NOT NULL,
    day_pnl         TEXT NOT NULL,
    week_pnl        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS funding (
    ts_ms           INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    book            TEXT NOT NULL,
    amount          TEXT NOT NULL,
    rate            TEXT NOT NULL,
    PRIMARY KEY (ts_ms, symbol, book)
);

CREATE TABLE IF NOT EXISTS backtests (
    id              TEXT PRIMARY KEY,
    created_ms      INTEGER NOT NULL,
    status          TEXT NOT NULL,
    params          TEXT NOT NULL,
    metrics         TEXT,
    equity_curve    TEXT,
    trades          TEXT,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'owner',
    created_ms      INTEGER NOT NULL,
    last_login_ms   INTEGER
);

CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms           INTEGER NOT NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    detail          TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_ms      INTEGER NOT NULL
);
