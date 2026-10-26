"""Strategy registry.

Adding a strategy means adding a class and a YAML file; no switch statements
and no imports buried in the orchestrator.
"""

from __future__ import annotations

from collections.abc import Callable

from oq.config import Config
from oq.strategy.base import Strategy
from oq.strategy.carry_basis import CarryBasis
from oq.strategy.position_manager import PositionManager
from oq.strategy.trend_donchian import TrendDonchian

_REGISTRY: dict[str, Callable[[dict, str], object]] = {
    "trend_donchian": TrendDonchian,
    "carry_basis": CarryBasis,
}


def available() -> list[str]:
    return sorted(_REGISTRY)


def build_strategies(config: Config) -> list[Strategy]:
    strategies: list[Strategy] = []
    for settings in config.strategies:
        if not settings.enabled:
            continue
        factory = _REGISTRY.get(settings.id)
        if factory is None:
            raise KeyError(f"strategy {settings.id!r} is not registered")
        strategies.append(factory(dict(settings.params), settings.timeframe))  # type: ignore[arg-type]
    return strategies


def build_position_manager() -> PositionManager:
    return PositionManager()


__all__ = ["available", "build_position_manager", "build_strategies"]
