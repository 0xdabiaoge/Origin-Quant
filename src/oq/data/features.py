"""Indicator library.

Rules learned the hard way from the reference implementations:

* No fabricated values.  EMA with too few samples returns ``NaN`` rather than
  the last price; RSI returns ``NaN`` rather than 50.
* Only closed bars.  Callers pass arrays whose last element is confirmed.
* Wilder smoothing for ATR/RSI, not a plain moving average.
"""

from __future__ import annotations

import numpy as np

NAN = float("nan")


def sma(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.shape, NAN)
    if period <= 0 or values.size < period:
        return out
    cum = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1 :] = (cum[period:] - cum[:-period]) / period
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.shape, NAN)
    if period <= 0 or values.size < period:
        return out
    alpha = 2.0 / (period + 1.0)
    seed = float(np.mean(values[:period]))
    out[period - 1] = seed
    for i in range(period, values.size):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    out = np.full(close.shape, NAN)
    if close.size == 0:
        return out
    out[0] = high[0] - low[0]
    for i in range(1, close.size):
        prev = close[i - 1]
        out[i] = max(high[i] - low[i], abs(high[i] - prev), abs(low[i] - prev))
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    tr = true_range(high, low, close)
    out = np.full(close.shape, NAN)
    if period <= 0 or close.size < period:
        return out
    out[period - 1] = float(np.mean(tr[:period]))
    for i in range(period, close.size):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(close.shape, NAN)
    if period <= 0 or close.size <= period:
        return out
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, close.size):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_gain <= 0 and avg_loss <= 0:
        return 50.0
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rolling_max_prev(values: np.ndarray, period: int) -> np.ndarray:
    """Highest value over ``[i-period, i)`` — excludes the current bar."""
    out = np.full(values.shape, NAN)
    for i in range(period, values.size):
        out[i] = float(np.max(values[i - period : i]))
    return out


def rolling_min_prev(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.shape, NAN)
    for i in range(period, values.size):
        out[i] = float(np.min(values[i - period : i]))
    return out


def rolling_std(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.shape, NAN)
    if values.size < period:
        return out
    for i in range(period - 1, values.size):
        out[i] = float(np.std(values[i - period + 1 : i + 1], ddof=1))
    return out


def realized_vol(close: np.ndarray, period: int, bars_per_year: float) -> np.ndarray:
    """Annualised close-to-close volatility in return units."""
    out = np.full(close.shape, NAN)
    if close.size < 3:
        return out
    rets = np.full(close.shape, NAN)
    rets[1:] = np.diff(close) / close[:-1]
    for i in range(period, close.size):
        window = rets[i - period + 1 : i + 1]
        window = window[~np.isnan(window)]
        if window.size < 3:
            continue
        out[i] = float(np.std(window, ddof=1) * np.sqrt(bars_per_year))
    return out


def zscore(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.shape, NAN)
    if values.size < period:
        return out
    for i in range(period - 1, values.size):
        window = values[i - period + 1 : i + 1]
        mean = float(np.mean(window))
        std = float(np.std(window, ddof=1))
        out[i] = 0.0 if std == 0 else (values[i] - mean) / std
    return out


def slope(values: np.ndarray, period: int) -> np.ndarray:
    """Least-squares slope over the trailing window, normalised by price."""
    out = np.full(values.shape, NAN)
    if values.size < period:
        return out
    x = np.arange(period, dtype=float)
    x_centered = x - x.mean()
    denom = float(np.sum(x_centered**2))
    for i in range(period - 1, values.size):
        window = values[i - period + 1 : i + 1]
        if window[0] <= 0:
            continue
        beta = float(np.sum(x_centered * (window - window.mean())) / denom)
        out[i] = beta / window.mean()
    return out


def bars_per_year(timeframe: str) -> float:
    per_day = {
        "1m": 1440,
        "5m": 288,
        "15m": 96,
        "30m": 48,
        "1H": 24,
        "4H": 6,
        "1D": 1,
    }.get(timeframe)
    if per_day is None:
        raise ValueError(f"unknown timeframe {timeframe}")
    return per_day * 365.0
