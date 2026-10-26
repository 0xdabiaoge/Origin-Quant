"""Deterministic synthetic market generator.

Two uses:

* tests and CI, where no network is allowed;
* first-run demo, so ``oq backtest`` works before the user has synced real
  data.

The generator is explicit about being synthetic: prices are labelled in the
report, and nothing here is ever written over real data without ``--force``.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable
from dataclasses import dataclass

from oq.data.models import Bar, FundingPoint
from oq.types import D

# symbol -> (start price, annualised vol, beta to the market factor)
SYNTH_SPECS: dict[str, tuple[float, float, float]] = {
    "BTC-USDT-SWAP": (68000.0, 0.42, 1.00),
    "ETH-USDT-SWAP": (3200.0, 0.55, 1.20),
    "SOL-USDT-SWAP": (165.0, 0.80, 1.55),
    "XRP-USDT-SWAP": (0.58, 0.70, 1.25),
    "DOGE-USDT-SWAP": (0.13, 0.92, 1.70),
    "LINK-USDT-SWAP": (16.5, 0.72, 1.35),
    "ARB-USDT-SWAP": (0.92, 0.85, 1.45),
    "SUI-USDT-SWAP": (1.70, 0.95, 1.60),
    "BTC-USDT": (68000.0, 0.42, 1.00),
    "ETH-USDT": (3200.0, 0.55, 1.20),
}

_TF_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1H": 24, "4H": 6, "1D": 1}


@dataclass(frozen=True)
class SyntheticMarket:
    bars: dict[str, list[Bar]]
    funding: dict[str, list[FundingPoint]]


def generate(
    symbols: Iterable[str],
    timeframe: str,
    start_ms: int,
    days: int,
    *,
    seed: int = 7,
) -> SyntheticMarket:
    if timeframe not in _TF_BARS_PER_DAY:
        raise ValueError(f"unsupported timeframe {timeframe}")
    bars_per_day = _TF_BARS_PER_DAY[timeframe]
    total = days * bars_per_day
    step_ms = 86_400_000 // bars_per_day
    rng = random.Random(seed)

    # Market factor with slow regime changes: drift flips every 200-600 bars.
    market = _market_returns(total, rng)

    bars: dict[str, list[Bar]] = {}
    funding: dict[str, list[FundingPoint]] = {}
    per_base_cache: dict[str, list[Bar]] = {}

    for symbol in symbols:
        price, vol, beta = SYNTH_SPECS.get(symbol, (100.0, 0.6, 1.2))
        base = symbol.split("-")[0]
        # Spot and perp share the same underlying path; the perp carries basis.
        if base in per_base_cache and symbol.endswith("-USDT"):
            continue
        returns = _idiosyncratic(total, rng, vol, bars_per_day)
        combined = [beta * m + i for m, i in zip(market, returns, strict=True)]
        series = _paths(price, combined, rng)
        if symbol.endswith("-USDT-SWAP"):
            spot_symbol = f"{base}-USDT"
            basis = _basis_series(total, rng)
            spot_path = series
            perp_path = [p * (1.0 + b) for p, b in zip(series, basis, strict=True)]
            bars[symbol] = _to_bars(symbol, timeframe, start_ms, step_ms, perp_path, combined, rng)
            bars[spot_symbol] = _to_bars(
                spot_symbol, timeframe, start_ms, step_ms, spot_path, combined, rng
            )
            funding[symbol] = _funding(symbol, start_ms, days, basis, rng)
            per_base_cache[base] = bars[spot_symbol]
        elif symbol not in bars:
            bars[symbol] = _to_bars(symbol, timeframe, start_ms, step_ms, series, combined, rng)

    return SyntheticMarket(bars=bars, funding=funding)


def _market_returns(total: int, rng: random.Random) -> list[float]:
    out: list[float] = []
    regime = rng.choice([1, -1, 0])
    remaining = rng.randint(200, 600)
    drift = 0.0
    for _ in range(total):
        if remaining <= 0:
            regime = rng.choice([1, -1, 0])
            remaining = rng.randint(200, 600)
        remaining -= 1
        drift = {1: 0.00022, -1: -0.00018, 0: 0.0}[regime]
        out.append(rng.gauss(drift, 0.0012))
    return out


def _idiosyncratic(
    total: int, rng: random.Random, annual_vol: float, bars_per_day: int
) -> list[float]:
    per_bar = annual_vol / math.sqrt(bars_per_day * 365.0)
    return [rng.gauss(0.0, per_bar * 0.55) for _ in range(total)]


def _paths(start: float, returns: list[float], rng: random.Random) -> list[float]:
    price = start
    out = []
    for ret in returns:
        price = max(1e-9, price * (1.0 + ret))
        out.append(price)
    return out


def _basis_series(total: int, rng: random.Random) -> list[float]:
    """Ornstein-Uhlenbeck perp premium around +6 bps, clipped to +/- 25 bps."""
    value = 0.0006
    target = 0.0006
    out = []
    for _ in range(total):
        value += 0.05 * (target - value) + rng.gauss(0.0, 0.00006)
        out.append(max(-0.0025, min(0.0025, value)))
    return out


def _to_bars(
    symbol: str,
    timeframe: str,
    start_ms: int,
    step_ms: int,
    closes: list[float],
    returns: list[float],
    rng: random.Random,
) -> list[Bar]:
    out: list[Bar] = []
    prev_close = closes[0] / (1.0 + returns[0]) if returns else closes[0]
    for index, close in enumerate(closes):
        ts = start_ms + index * step_ms
        open_ = prev_close
        wick = abs(rng.gauss(0.0, 0.35)) * abs(returns[index]) if index < len(returns) else 0.0
        high = max(open_, close) * (1.0 + wick)
        low = min(open_, close) * (1.0 - wick)
        volume = abs(rng.gauss(1.0, 0.35)) * 1000.0
        out.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                ts_ms=ts,
                open=D(round(open_, 8)),
                high=D(round(high, 8)),
                low=D(round(low, 8)),
                close=D(round(close, 8)),
                volume=D(round(volume, 4)),
                confirmed=True,
            )
        )
        prev_close = close
    return out


def _funding(
    symbol: str, start_ms: int, days: int, basis: list[float], rng: random.Random
) -> list[FundingPoint]:
    """Funding every 8h, correlated with the perp premium."""
    out: list[FundingPoint] = []
    step = 8 * 3_600_000
    per_day = 3
    first = (start_ms // step) * step + step
    for day in range(days):
        for slot in range(per_day):
            ts = first + day * 86_400_000 + slot * step
            index = min(len(basis) - 1, max(0, (ts - start_ms) // 900_000))
            base = basis[index] * 0.35
            noise = rng.gauss(0.0, 0.00004)
            rate = max(-0.0015, min(0.0030, base + noise))
            out.append(FundingPoint(symbol=symbol, ts_ms=ts, rate=D(round(rate, 8))))
    return out
