"""Command line entry point.

Every command is safe by default: backtest and inspection need no keys, and
anything that could reach a live venue refuses unless the environment says
``OQ_LIVE=1`` and the config says ``live_trading: true``.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from oq.backtest import (
    BacktestEngine,
    BacktestParams,
    load_parquet,
    load_synthetic,
    trading_symbols,
)
from oq.backtest.report import format_report
from oq.config import Config, load_config, redacted_config
from oq.data.ingest import Ingestor
from oq.data.store import ParquetStore
from oq.execution.okx_adapter import OkxAdapter, build_rest
from oq.storage import Database, Store
from oq.strategy.registry import build_position_manager, build_strategies
from oq.types import D
from oq.version import app_version, code_version

log = logging.getLogger("oq")


def _setup_logging(level: str, log_dir: Path | None = None) -> None:
    """Log to stdout, and to a file when a directory is available.

    The admin console tails that file, so without it the "运行日志" page is
    permanently empty. Failure to open the file is not fatal: a read-only
    filesystem should degrade the console, not stop the engine.
    """
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "engine.log", maxBytes=8 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        root.warning("file logging disabled: %s is not writable", log_dir)


def _open(config: Config) -> tuple[Database, Store]:
    db = Database(config.app.db_path)
    db.migrate()
    store = Store(db, config_hash=config.hash)
    with db.tx() as conn:
        store.upsert_instruments(conn, config.instrument_map().values())
    return db, store


def cmd_init_db(args: argparse.Namespace, config: Config) -> int:
    db, store = _open(config)
    print(f"database  {db.path}")
    print(f"schema    v{db.migrate()}")
    print(f"integrity {store.stats()}")
    db.close()
    return 0


def cmd_verify(args: argparse.Namespace, config: Config) -> int:
    db = Database(config.app.db_path)
    db.migrate()
    report = db.verify()
    print(json.dumps(report, indent=2, default=str))
    db.close()
    return 0 if report.get("ok") else 1


def cmd_config(args: argparse.Namespace, config: Config) -> int:
    print(json.dumps(redacted_config(config), indent=2, default=str))
    print(f"\nconfig_hash {config.hash}")
    return 0


def cmd_seed(args: argparse.Namespace, config: Config) -> int:
    db, _store = _open(config)
    parquet = ParquetStore(config.data.parquet_dir)
    symbols = trading_symbols(config)
    if args.source == "synthetic":
        rest = None
        ingestor = Ingestor(config, parquet, rest)
        seeded = ingestor.seed_synthetic(
            days=args.days,
            symbols=symbols,
            timeframe=config.data.timeframe,
            seed=args.seed,
            force=True,
        )
        reports = list(seeded.values())
    else:
        rest = build_rest(config)
        ingestor = Ingestor(config, parquet, rest)
        reports = [
            ingestor.sync_bars(symbol, config.data.timeframe, days=args.days) for symbol in symbols
        ]
        for symbol in symbols:
            if symbol.endswith("-SWAP"):
                try:
                    ingestor.sync_funding(symbol, days=args.days)
                except Exception as exc:
                    log.warning("funding sync failed for %s: %s", symbol, exc)
    for report in reports:
        print(
            f"{report.symbol:<20} {report.timeframe:<5} "
            f"{report.total:>7} bars  last {report.last_ms}"
        )
    db.close()
    return 0


def cmd_backtest(args: argparse.Namespace, config: Config) -> int:
    symbols = trading_symbols(config)
    if args.source == "synthetic":
        series = load_synthetic(
            config, symbols=symbols, timeframe=config.data.timeframe, days=args.days, seed=args.seed
        )
    else:
        series = load_parquet(config, symbols=symbols, timeframe=config.data.timeframe)
    if not series:
        print(
            "no data; run `oq seed --source synthetic` or `oq seed --source okx` first",
            file=sys.stderr,
        )
        return 2

    engine = BacktestEngine(config, build_strategies(config), build_position_manager())
    result = engine.run(
        series,
        BacktestParams(
            initial_cash=D(str(args.cash)),
            timeframe=config.data.timeframe,
            label=args.label or f"{args.source}-{args.days}d",
        ),
    )
    print(format_report(result))
    if args.json:
        Path(args.json).write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    if args.monte_carlo:
        _monte_carlo(result)
    return 0


def _monte_carlo(result) -> None:
    """Resample trade order to show the dispersion hidden by one equity curve."""
    import random

    pnls = [float(t.pnl) for t in result.trades]
    if len(pnls) < 20:
        print("\nmonte carlo: need at least 20 trades")
        return
    start = float(result.initial_cash)
    finals = []
    rng = random.Random(11)
    for _ in range(2000):
        sample = rng.choices(pnls, k=len(pnls))
        finals.append(start + sum(sample))
    finals.sort()
    print("\nmonte carlo (2000 reshuffles of the same trades):")
    p05 = finals[int(0.05 * len(finals))]
    p50 = finals[len(finals) // 2]
    p95 = finals[int(0.95 * len(finals))]
    print(f"  p05 {p05:,.0f}   p50 {p50:,.0f}   p95 {p95:,.0f}")
    print(f"  P(loss) {sum(1 for v in finals if v < start) / len(finals):.1%}")


def cmd_serve(args: argparse.Namespace, config: Config) -> int:
    import uvicorn

    from oq.app.main import build_app

    app = build_app(config, start_scheduler=args.scheduler)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=config.app.log_level.lower(),
        access_log=False,
        # uvicorn's default dictConfig replaces the root handlers, which would
        # throw away the file handler and leave the console's log page empty.
        log_config=None,
    )
    return 0


def cmd_cycle(args: argparse.Namespace, config: Config) -> int:
    from oq.app.main import AppContext, _build_orchestrator

    db, store = _open(config)
    orchestrator = _build_orchestrator(config, db, store)
    if orchestrator is None:
        print("no venue configured", file=sys.stderr)
        return 2
    report = orchestrator.run_once()
    print(
        json.dumps(
            {
                "cycle_id": report.cycle_id,
                "equity": str(report.equity),
                "intents": report.intents,
                "approved": report.approved,
                "rejected": report.rejected,
                "fills": report.fills,
                "errors": report.errors,
                "skipped": report.skipped,
            },
            indent=2,
        )
    )
    del AppContext
    db.close()
    return 0 if report.ok or report.skipped == "no_data" else 1


def cmd_status(args: argparse.Namespace, config: Config) -> int:
    db = Database(config.app.db_path)
    db.migrate()
    store = Store(db, config_hash=config.hash)
    print(
        json.dumps(
            {"stats": store.stats(), "last_cycle_ms": store.last_cycle_ms()}, indent=2, default=str
        )
    )
    db.close()
    return 0


def cmd_reconcile(args: argparse.Namespace, config: Config) -> int:
    from oq.execution.reconcile import reconcile

    db, store = _open(config)
    rest = build_rest(config)
    adapter = OkxAdapter(config, rest, allow_live=False)
    report = reconcile(store.load_portfolio(), adapter)
    print(json.dumps(report.as_dict(), indent=2, default=str))
    db.close()
    return 0 if report.ok else 1


def cmd_version(args: argparse.Namespace, config: Config | None) -> int:
    print(f"origin-quant {app_version()}")
    print(f"build        {code_version()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oq", description="Origin Quant trading engine")
    parser.add_argument("--config-dir", default=os.getenv("OQ_CONFIG_DIR", "config"))
    parser.add_argument("--log-level", default=os.getenv("OQ_LOG_LEVEL", "INFO"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create or migrate the database")
    sub.add_parser("verify", help="check database integrity")
    sub.add_parser("config-show", help="print the effective config (secrets redacted)")
    sub.add_parser("status", help="print ledger statistics")
    sub.add_parser("reconcile", help="compare local positions with the venue")
    sub.add_parser("version", help="print version")

    seed = sub.add_parser("seed", help="download bars or generate synthetic ones")
    seed.add_argument("--source", choices=["okx", "synthetic"], default="okx")
    seed.add_argument("--days", type=int, default=180)
    seed.add_argument("--seed", type=int, default=7)

    bt = sub.add_parser("backtest", help="run a backtest")
    bt.add_argument("--source", choices=["okx", "synthetic"], default="parquet")
    bt.add_argument("--days", type=int, default=180)
    bt.add_argument("--cash", type=float, default=10_000)
    bt.add_argument("--seed", type=int, default=7)
    bt.add_argument("--label", default="")
    bt.add_argument("--json", default="")
    bt.add_argument("--monte-carlo", action="store_true")

    serve = sub.add_parser("serve", help="run the API and dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--scheduler", action="store_true")

    sub.add_parser("cycle", help="run exactly one trading cycle")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log_dir = os.getenv("OQ_LOG_DIR", "")
    _setup_logging(args.log_level, Path(log_dir) if log_dir else None)
    if args.command == "version":
        return cmd_version(args, None)

    try:
        config = load_config(args.config_dir)
    except Exception as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    handlers = {
        "init-db": cmd_init_db,
        "verify": cmd_verify,
        "config-show": cmd_config,
        "status": cmd_status,
        "seed": cmd_seed,
        "backtest": cmd_backtest,
        "serve": cmd_serve,
        "cycle": cmd_cycle,
        "reconcile": cmd_reconcile,
    }
    return handlers[args.command](args, config)


if __name__ == "__main__":
    raise SystemExit(main())
