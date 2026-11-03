from __future__ import annotations

import pandas as pd

from oq.backtest.data import load_parquet


def test_local_window_uses_latest_confirmed_bar_and_trims_funding(config):
    day = 86_400_000

    class Store:
        def read_bars(self, symbol, timeframe):
            return pd.DataFrame(
                {
                    "ts_ms": [i * day for i in range(6)],
                    "open": [1.0] * 6,
                    "high": [1.0] * 6,
                    "low": [1.0] * 6,
                    "close": [1.0] * 6,
                    "volume": [1.0] * 6,
                    "confirmed": [True] * 5 + [False],
                }
            )

        def read_funding(self, symbol):
            return pd.DataFrame({"ts_ms": [day, 3 * day, 4 * day], "rate": [0.1, 0.2, 0.3]})

    series = load_parquet(config, symbols=["BTC"], timeframe="15m", store=Store(), days=2)["BTC"]
    assert series.ts.tolist() == [3 * day, 4 * day]
    assert series.funding_ts.tolist() == [3 * day, 4 * day]


def test_malformed_funding_columns_are_not_associated(config):
    class Store:
        def read_bars(self, symbol, timeframe):
            return pd.DataFrame(
                {"ts_ms": [1], "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]}
            )

        def read_funding(self, symbol):
            return pd.DataFrame({"ts_ms": [1]})

    series = load_parquet(config, symbols=["BTC"], timeframe="15m", store=Store(), days=1)["BTC"]
    assert series.funding_ts.size == series.funding_rates.size == 0
