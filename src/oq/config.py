"""Configuration: YAML files, validated by pydantic, hashed for audit.

Principles:

* Secrets never live in YAML.  They are read from the environment only.
* Every path is resolved against the project root captured at load time, so
  loading order and ``cwd`` cannot change behaviour.
* ``Config.hash`` is a canonical SHA-256 over the non-secret payload.  Every
  decision and order records it; a config edit is therefore visible in the
  audit trail even when nobody wrote a changelog entry.
"""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from oq.types import Book, D, Instrument, InstType

Dec = Annotated[Decimal, BeforeValidator(lambda v: D(v))]


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "Origin Quant"
    mode: Literal["backtest", "paper", "live"] = "paper"
    timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"
    data_dir: Path = Path("data")
    db_path: Path = Path("data/origin.db")
    base_currency: str = "USDT"
    live_trading: bool = False
    cycle_grace_seconds: int = 15
    # Paper accounts need a starting balance; live reads the venue instead.
    paper_cash: Decimal = Decimal(10000)


class VenueSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    demo: bool = True
    rest_base: str = "https://www.okx.com"
    ws_public: str = "wss://ws.okx.com:8443/ws/v5/public"
    ws_private: str = "wss://ws.okx.com:8443/ws/v5/private"
    timeout_s: float = 10.0
    max_retries: int = 3
    rate_limit_per_sec: int = 8


class InstrumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    inst_type: InstType = InstType.SWAP
    base: str
    quote: str = "USDT"
    tick_size: Dec
    lot_size: Dec
    min_size: Dec
    contract_value: Dec = D(1)
    maker_fee: Dec = D("0.0002")
    taker_fee: Dec = D("0.0005")
    max_leverage: Dec = D(10)

    def to_instrument(self) -> Instrument:
        return Instrument(
            symbol=self.symbol,
            inst_type=self.inst_type,
            base=self.base,
            quote=self.quote,
            tick_size=self.tick_size,
            lot_size=self.lot_size,
            min_size=self.min_size,
            contract_value=self.contract_value,
            maker_fee=self.maker_fee,
            taker_fee=self.taker_fee,
            max_leverage=self.max_leverage,
        )


class UniverseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    book: Book = Book.DIRECTIONAL
    books: list[Book] = Field(default_factory=list)
    enabled: bool = True
    cluster: str | None = None
    max_weight: Dec = D("1")

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _fill_books(self) -> UniverseItem:
        if not self.books:
            object.__setattr__(self, "books", [self.book])
        elif self.book not in self.books:
            object.__setattr__(self, "books", [self.book, *self.books])
        return self

    def allows(self, book: Book) -> bool:
        return book in self.books


class StrategySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    enabled: bool = True
    book: Book = Book.DIRECTIONAL
    timeframe: str = "15m"
    params: dict[str, Any] = Field(default_factory=dict)


class DataSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parquet_dir: Path = Path("data/parquet")
    timeframe: str = "15m"
    warmup_bars: int = 200
    staleness_seconds: int = 180
    max_spread_bps: int = 25
    min_quote_volume_usd: Dec = D(5_000_000)


class RiskSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_per_trade_pct: Dec = D("0.0025")
    max_portfolio_heat_pct: Dec = D("0.02")
    max_book_heat_pct: dict[Book, Dec] = Field(
        default_factory=lambda: {Book.CARRY: D("0.012"), Book.DIRECTIONAL: D("0.008")}
    )
    max_gross_leverage: Dec = D("2.0")
    max_symbol_exposure_pct: Dec = D("0.25")
    max_cluster_exposure_pct: Dec = D("0.40")
    max_open_positions: int = 8
    max_per_book: int = 6
    daily_loss_halt_pct: Dec = D("0.015")
    daily_loss_flatten_pct: Dec = D("0.025")
    weekly_loss_halve_pct: Dec = D("0.04")
    max_drawdown_pct: Dec = D("0.10")
    reentry_cooldown_minutes: int = 60
    stop_cooldown_minutes: int = 30
    min_stop_atr_mult: Dec = D("1.2")
    max_stop_atr_mult: Dec = D("4.0")
    min_rr: Dec = D("1.2")
    min_cost_edge_multiple: Dec = D("3.0")
    max_slippage_bps: int = 10
    max_order_notional_pct: Dec = D("0.35")
    reduce_only_on_exit: bool = True

    @field_validator("max_book_heat_pct", mode="before")
    @classmethod
    def _book_keys(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return {Book(k): D(val) for k, val in v.items()}
        return v


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: AppSettings = Field(default_factory=AppSettings)
    venues: dict[str, VenueSettings] = Field(default_factory=lambda: {"okx": VenueSettings()})
    instruments: list[InstrumentSpec] = Field(default_factory=list)
    universe: list[UniverseItem] = Field(default_factory=list)
    strategies: list[StrategySettings] = Field(default_factory=list)
    data: DataSettings = Field(default_factory=DataSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    root: Path = Field(default_factory=Path.cwd)
    hash: str = ""

    @model_validator(mode="after")
    def _check_refs(self) -> Config:
        symbols = {item.symbol for item in self.instruments}
        if not symbols:
            raise ValueError("config has no instruments")
        for item in self.universe:
            if item.symbol not in symbols:
                raise ValueError(f"universe symbol {item.symbol} has no instrument definition")
        ids = [s.id for s in self.strategies]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate strategy ids")
        for strategy in self.strategies:
            if strategy.timeframe not in {"1m", "5m", "15m", "30m", "1H", "4H", "1D"}:
                raise ValueError(f"strategy {strategy.id} has unsupported timeframe")
        return self

    # -- derived views -------------------------------------------------
    def instrument(self, symbol: str) -> Instrument:
        for spec in self.instruments:
            if spec.symbol == symbol:
                return spec.to_instrument()
        raise KeyError(f"unknown instrument {symbol}")

    def instrument_map(self) -> dict[str, Instrument]:
        return {spec.symbol: spec.to_instrument() for spec in self.instruments}

    def universe_map(self) -> dict[str, UniverseItem]:
        return {item.symbol: item for item in self.universe if item.enabled}

    def strategy(self, strategy_id: str) -> StrategySettings:
        for item in self.strategies:
            if item.id == strategy_id:
                return item
        raise KeyError(f"unknown strategy {strategy_id}")

    # -- persistence ---------------------------------------------------
    def canonical(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"root", "hash"})
        return _jsonable(payload)

    def compute_hash(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def with_hash(self) -> Config:
        return self.model_copy(update={"hash": self.compute_hash()})


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a mapping")
    return data


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _env_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    """Small, explicit allowlist.  No generic ``OQ_APP__MODE`` magic."""
    app = dict(payload.get("app", {}))
    if mode := os.getenv("OQ_MODE"):
        app["mode"] = mode
    if db := os.getenv("OQ_DB_PATH"):
        app["db_path"] = db
    if data_dir := os.getenv("OQ_DATA_DIR"):
        app["data_dir"] = data_dir
    if level := os.getenv("OQ_LOG_LEVEL"):
        app["log_level"] = level
    if os.getenv("OQ_LIVE") == "1":
        app["live_trading"] = True
    payload["app"] = app
    return payload


def load_config(config_dir: str | Path = "config", *, root: Path | None = None) -> Config:
    """Load, merge, validate and hash configuration.

    ``root`` defaults to the parent of ``config_dir``; all relative paths in
    the payload are resolved against it at load time.
    """
    cdir = Path(config_dir).resolve()
    project_root = (root or cdir.parent).resolve()

    base = _read_yaml(cdir / "base.yaml")
    risk = _read_yaml(cdir / "risk.yaml")
    payload = _merge(base, {"risk": risk} if risk else {})

    strategies: list[dict[str, Any]] = []
    strategy_dir = cdir / "strategies"
    if strategy_dir.is_dir():
        for path in sorted(strategy_dir.glob("*.yaml")):
            item = _read_yaml(path)
            item.setdefault("id", path.stem)
            strategies.append(item)
    if strategies:
        payload["strategies"] = strategies

    payload = _env_overrides(payload)
    payload["root"] = project_root

    config = Config.model_validate(payload)
    config = config.model_copy(
        update={
            "app": config.app.model_copy(
                update={
                    "data_dir": _resolve(project_root, config.app.data_dir),
                    "db_path": _resolve(project_root, config.app.db_path),
                }
            ),
            "data": config.data.model_copy(
                update={"parquet_dir": _resolve(project_root, config.data.parquet_dir)}
            ),
        }
    )
    return config.with_hash()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else (root / path)


def read_secret(name: str, default: str = "") -> str:
    """Secrets come from the environment, never from YAML."""
    return os.getenv(name, default)


def redacted_config(config: Config) -> dict[str, Any]:
    """Config payload safe for the control API (no secrets exist here anyway)."""
    payload = config.canonical()
    payload["hash"] = config.hash
    return payload
