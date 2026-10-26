from __future__ import annotations

import time

from oq.app.main import Scheduler
from oq.clock import tf_to_ms


class _Ctx:
    """Minimal stand-in: the scheduler only reads the timeframe and nothing else."""

    def __init__(self, timeframe: str) -> None:
        data = type("Data", (), {"timeframe": timeframe})()
        self.config = type("Config", (), {"data": data})()


def _scheduler(timeframe: str, grace_s: int = 15) -> Scheduler:
    sched = Scheduler.__new__(Scheduler)
    sched.ctx = _Ctx(timeframe)
    sched.grace_s = grace_s
    return sched


def test_next_cycle_lands_on_the_bar_boundary_plus_grace():
    sched = _scheduler("15m", grace_s=15)
    step = tf_to_ms("15m")
    now = 1_700_000_000_000
    target = sched._next_cycle_ms(now)
    assert target % step == 15_000
    assert target > now
    assert target - now <= step


def test_next_cycle_is_strictly_in_the_future_even_on_a_boundary():
    sched = _scheduler("1h", grace_s=5)
    step = tf_to_ms("1h")
    # Exactly on a boundary: the scheduler must wait for the next one, never
    # fire twice for the same bar.
    now = 1_700_000_000_000 - (1_700_000_000_000 % step)
    assert sched._next_cycle_ms(now) == now + step + 5_000


def test_cycle_intervals_follow_the_configured_timeframe():
    for timeframe in ("5m", "15m", "1h", "4h"):
        sched = _scheduler(timeframe)
        step = tf_to_ms(timeframe)
        now = int(time.time() * 1000)
        assert sched._next_cycle_ms(now) - now <= step + 15_000
