"""Shared fixtures.

The tests build a real config, a real SQLite file and real synthetic bars.
Nothing is mocked at the domain level: a test that passes here exercises the
same code the process runs.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from oq.config import load_config
from oq.storage import Database, Store
from oq.types import D


@pytest.fixture(scope="session")
def project_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def config(project_root):
    return load_config(project_root / "config").with_hash()


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.migrate()
    yield database
    database.close()


@pytest.fixture()
def store(db, config):
    return Store(db, config_hash=config.hash)


@pytest.fixture()
def portfolio():
    from oq.domain.portfolio import Portfolio

    return Portfolio(cash=D("10000"))


@pytest.fixture(scope="session")
def series(config):
    from oq.backtest import load_synthetic, trading_symbols

    return load_synthetic(
        config,
        symbols=trading_symbols(config),
        timeframe="15m",
        days=45,
        seed=3,
        start_ms=1_700_000_000_000,
    )


@pytest.fixture()
def instruments(config):
    return config.instrument_map()


@pytest.fixture()
def btc(instruments):
    return instruments["BTC-USDT-SWAP"]


@pytest.fixture()
def eth(instruments):
    return instruments["ETH-USDT-SWAP"]


@pytest.fixture()
def quotes():
    from oq.data.models import Quote

    def make(symbol: str, bid: str, ask: str):
        return Quote(symbol=symbol, bid=D(bid), ask=D(ask), ts_ms=1_700_000_000_000)

    return make


@pytest.fixture()
def dec():
    return Decimal
