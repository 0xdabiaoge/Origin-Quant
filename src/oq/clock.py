"""Clocks and timeframe arithmetic.

The decision kernel never calls these.  They exist at the edges: the live
loop, the simulator, and the API.  Passing ``now`` into the kernel is what
makes a replay byte-identical to the original run.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol, runtime_checkable

_TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1H": 3_600_000,
    "1h": 3_600_000,
    "2H": 7_200_000,
    "4H": 14_400_000,
    "4h": 14_400_000,
    "1D": 86_400_000,
    "1d": 86_400_000,
}


def tf_to_ms(timeframe: str) -> int:
    try:
        return _TF_MS[timeframe]
    except KeyError as exc:
        raise ValueError(f"unknown timeframe {timeframe!r}") from exc


def floor_ms(ts_ms: int, timeframe: str) -> int:
    step = tf_to_ms(timeframe)
    return (ts_ms // step) * step


def ceil_ms(ts_ms: int, timeframe: str) -> int:
    step = tf_to_ms(timeframe)
    return ((ts_ms + step - 1) // step) * step


def bar_index(ts_ms: int, timeframe: str, origin_ms: int = 0) -> int:
    return (ts_ms - origin_ms) // tf_to_ms(timeframe)


@runtime_checkable
class Clock(Protocol):
    def now_ms(self) -> int: ...


class SystemClock:
    """Wall clock.  Only used by the live loop and the API."""

    def now_ms(self) -> int:
        return int(time.time() * 1000)


class SimClock:
    """Deterministic clock driven by the backtest engine."""

    __slots__ = ("_now",)

    def __init__(self, start_ms: int = 0) -> None:
        self._now = int(start_ms)

    def now_ms(self) -> int:
        return self._now

    def set(self, ts_ms: int) -> None:
        if ts_ms < self._now:
            raise ValueError("sim clock cannot move backwards")
        self._now = int(ts_ms)

    def advance(self, delta_ms: int) -> int:
        if delta_ms < 0:
            raise ValueError("sim clock cannot move backwards")
        self._now += int(delta_ms)
        return self._now


def sleep_until(target_ms: int, clock: Clock | None = None) -> None:
    """Blocking sleep until ``target_ms``, waking early at most 1s.

    The scheduler runs in a plain thread, so it needs a blocking primitive.
    Using the async variant there would create a coroutine and return at once,
    turning the scheduler into a busy loop.
    """
    live = clock or SystemClock()
    while True:
        remaining = target_ms - live.now_ms()
        if remaining <= 0:
            return
        time.sleep(min(remaining / 1000.0, 1.0))


async def sleep_until_ms(target_ms: int, clock: Clock | None = None) -> None:
    """Sleep until wall-clock ``target_ms``, waking early at most 1s."""
    while True:
        now = (clock or SystemClock()).now_ms()
        remaining = target_ms - now
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining / 1000.0, 1.0))
