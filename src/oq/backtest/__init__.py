"""Backtesting: data adapters, engine, metrics, report."""

from oq.backtest.data import SymbolSeries, from_bars, load_parquet, load_synthetic, trading_symbols
from oq.backtest.engine import BacktestEngine, BacktestParams, BacktestResult
from oq.backtest.metrics import EquityPoint, compute_metrics

__all__ = [
    "BacktestEngine",
    "BacktestParams",
    "BacktestResult",
    "EquityPoint",
    "SymbolSeries",
    "compute_metrics",
    "from_bars",
    "load_parquet",
    "load_synthetic",
    "trading_symbols",
]
