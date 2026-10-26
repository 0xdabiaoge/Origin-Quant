"""Parquet storage for bars and funding.

Layout::

    data/parquet/bars/<SYMBOL>/<TIMEFRAME>.parquet
    data/parquet/funding/<SYMBOL>.parquet

One file per symbol/timeframe keeps the write path simple (read, concat,
dedupe, atomic replace) and is fast enough at 15m resolution: two years of
15m bars is ~70k rows, a few MB.  Daily partitions would be needed at tick
resolution, which is not v0.1 scope.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from oq.data.models import Bar, FundingPoint

BAR_COLUMNS = ["ts_ms", "open", "high", "low", "close", "volume", "confirmed"]


class ParquetStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.bars_dir = self.root / "bars"
        self.funding_dir = self.root / "funding"

    # -- paths ---------------------------------------------------------
    def bars_path(self, symbol: str, timeframe: str) -> Path:
        return self.bars_dir / symbol / f"{timeframe}.parquet"

    def funding_path(self, symbol: str) -> Path:
        return self.funding_dir / f"{symbol}.parquet"

    # -- bars ----------------------------------------------------------
    def write_bars(self, symbol: str, timeframe: str, bars: Iterable[Bar]) -> int:
        rows = [
            {
                "ts_ms": b.ts_ms,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
                "confirmed": bool(b.confirmed),
            }
            for b in bars
        ]
        if not rows:
            return 0
        frame = pd.DataFrame(rows).drop_duplicates(subset=["ts_ms"], keep="last")
        path = self.bars_path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            old = pd.read_parquet(path)
            frame = pd.concat([old, frame], ignore_index=True)
            frame = frame.drop_duplicates(subset=["ts_ms"], keep="last")
        frame = frame.sort_values("ts_ms").reset_index(drop=True)
        _atomic_write_parquet(frame, path)
        return len(frame)

    def read_bars(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self.bars_path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame(columns=BAR_COLUMNS)
        frame = pd.read_parquet(path)
        return frame.sort_values("ts_ms").reset_index(drop=True)

    def bar_arrays(self, symbol: str, timeframe: str) -> dict[str, np.ndarray]:
        frame = self.read_bars(symbol, timeframe)
        if frame.empty:
            empty = np.array([], dtype=float)
            return {
                "ts_ms": np.array([], dtype=np.int64),
                "open": empty,
                "high": empty,
                "low": empty,
                "close": empty,
                "volume": empty,
            }
        return {
            "ts_ms": frame["ts_ms"].to_numpy(dtype=np.int64),
            "open": frame["open"].to_numpy(dtype=float),
            "high": frame["high"].to_numpy(dtype=float),
            "low": frame["low"].to_numpy(dtype=float),
            "close": frame["close"].to_numpy(dtype=float),
            "volume": frame["volume"].to_numpy(dtype=float),
        }

    def last_bar_ms(self, symbol: str, timeframe: str) -> int:
        frame = self.read_bars(symbol, timeframe)
        return int(frame["ts_ms"].iloc[-1]) if not frame.empty else 0

    # -- funding -------------------------------------------------------
    def write_funding(self, symbol: str, points: Iterable[FundingPoint]) -> int:
        rows = [{"ts_ms": p.ts_ms, "rate": float(p.rate)} for p in points]
        if not rows:
            return 0
        frame = pd.DataFrame(rows).drop_duplicates(subset=["ts_ms"], keep="last")
        path = self.funding_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            old = pd.read_parquet(path)
            frame = pd.concat([old, frame], ignore_index=True)
            frame = frame.drop_duplicates(subset=["ts_ms"], keep="last")
        frame = frame.sort_values("ts_ms").reset_index(drop=True)
        _atomic_write_parquet(frame, path)
        return len(frame)

    def read_funding(self, symbol: str) -> pd.DataFrame:
        path = self.funding_path(symbol)
        if not path.exists():
            return pd.DataFrame(columns=["ts_ms", "rate"])
        return pd.read_parquet(path).sort_values("ts_ms").reset_index(drop=True)


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".oq-", suffix=".parquet", dir=path.parent)
    os.close(fd)
    try:
        frame.to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
