"""OKX v5 REST client: public market data and signed private endpoints.

Deliberately synchronous.  A 15-minute system with a handful of instruments
does not need an async stack, and synchronous code makes the live loop,
backtests and the CLI share one code path.  The live loop runs cycles in a
worker thread; the API never touches this class directly.

Live trading is *not* enabled by this module.  It only speaks the protocol;
:class:`oq.execution.okx_adapter.OkxAdapter` refuses to place orders unless
the config says ``live_trading`` and ``OQ_LIVE=1``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import random
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Self
from urllib.parse import urlencode

import httpx

from oq.config import InstrumentSpec, VenueSettings
from oq.data.models import Bar, FundingPoint, Quote
from oq.types import D, InstType

log = logging.getLogger(__name__)


class OkxError(RuntimeError):
    def __init__(self, code: str, message: str, endpoint: str = "") -> None:
        super().__init__(f"okx {code}: {message} ({endpoint})")
        self.code = code
        self.message = message
        self.endpoint = endpoint

    @property
    def retryable(self) -> bool:
        return self.code in {"50011", "50013", "50026", "50004"} or self.code.startswith("5")


class RateLimiter:
    def __init__(self, per_second: int) -> None:
        self.capacity = max(1, per_second)
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self.capacity, self._tokens + elapsed * self.capacity)
            if self._tokens < 1.0:
                sleep_for = (1.0 - self._tokens) / self.capacity
                time.sleep(sleep_for)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


def okx_timestamp(ts_ms: int | None = None) -> str:
    moment = (
        datetime.fromtimestamp(ts_ms / 1000, tz=UTC) if ts_ms is not None else datetime.now(tz=UTC)
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def sign_request(secret: str, timestamp: str, method: str, request_path: str, body: str) -> str:
    prehash = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class OkxRest:
    def __init__(
        self,
        settings: VenueSettings,
        *,
        api_key: str = "",
        secret: str = "",
        passphrase: str = "",
        demo: bool | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.demo = settings.demo if demo is None else demo
        self._limiter = RateLimiter(settings.rate_limit_per_sec)
        # trust_env=False: a machine-wide HTTP(S)_PROXY must not silently
        # reroute signed requests, and a malformed proxy variable must not
        # make the client unconstructable.
        self._client = httpx.Client(
            base_url=settings.rest_base,
            timeout=settings.timeout_s,
            transport=transport,
            headers={"User-Agent": "OriginQuant/0.1"},
            trust_env=False,
        )

    # -- plumbing ------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.secret and self.passphrase)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        private: bool = False,
    ) -> list[dict[str, Any]]:
        query = ""
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                query = "?" + urlencode(clean)
        request_path = path + query
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if private:
            if not self.has_credentials:
                raise OkxError("auth", "private endpoint called without credentials", request_path)
            timestamp = okx_timestamp()
            headers.update(
                {
                    "OK-ACCESS-KEY": self.api_key,
                    "OK-ACCESS-SIGN": sign_request(
                        self.secret, timestamp, method, request_path, payload
                    ),
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": self.passphrase,
                }
            )
            if self.demo:
                headers["x-simulated-trading"] = "1"

        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            self._limiter.acquire()
            try:
                response = self._client.request(
                    method,
                    request_path,
                    content=payload or None,
                    headers=headers,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise OkxError(str(response.status_code), "transient http error", request_path)
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                last_error = exc
                self._backoff(attempt)
                continue
            except OkxError as exc:
                last_error = exc
                self._backoff(attempt)
                continue

            code = str(data.get("code", "0"))
            if code != "0":
                error = OkxError(code, str(data.get("msg", "")), request_path)
                if error.retryable and attempt < self.settings.max_retries:
                    last_error = error
                    self._backoff(attempt)
                    continue
                raise error
            rows = data.get("data", [])
            return rows if isinstance(rows, list) else []

        assert last_error is not None
        raise last_error

    @staticmethod
    def _backoff(attempt: int) -> None:
        delay = min(2.0, 0.25 * (2**attempt)) + random.random() * 0.1
        time.sleep(delay)

    # -- public --------------------------------------------------------
    def server_time_ms(self) -> int:
        rows = self._request("GET", "/api/v5/public/time")
        return int(rows[0]["ts"]) if rows else 0

    def instruments(self, inst_type: InstType) -> list[InstrumentSpec]:
        rows = self._request(
            "GET", "/api/v5/public/instruments", params={"instType": inst_type.value}
        )
        return [parse_instrument(row) for row in rows if row.get("state") == "live"]

    def ticker(self, symbol: str) -> Quote:
        rows = self._request("GET", "/api/v5/market/ticker", params={"instId": symbol})
        if not rows:
            raise OkxError("empty", f"no ticker for {symbol}", "/api/v5/market/ticker")
        row = rows[0]
        return Quote(
            symbol=symbol,
            ts_ms=int(row["ts"]),
            bid=D(row.get("bidPx") or row["last"]),
            ask=D(row.get("askPx") or row["last"]),
            last=D(row["last"]),
            bid_size=D(row.get("bidSz") or 0),
            ask_size=D(row.get("askSz") or 0),
        )

    def candles(
        self,
        symbol: str,
        bar: str,
        *,
        limit: int = 300,
        after_ms: int | None = None,
        history: bool = False,
    ) -> list[Bar]:
        path = "/api/v5/market/history-candles" if history else "/api/v5/market/candles"
        rows = self._request(
            "GET", path, params={"instId": symbol, "bar": bar, "limit": limit, "after": after_ms}
        )
        out = [parse_bar(symbol, bar, list(row)) for row in rows]
        out.sort(key=lambda item: item.ts_ms)
        return out

    def funding_history(
        self, symbol: str, *, limit: int = 100, before_ms: int | None = None
    ) -> list[FundingPoint]:
        rows = self._request(
            "GET",
            "/api/v5/public/funding-rate-history",
            params={"instId": symbol, "limit": limit, "before": before_ms},
        )
        out = [
            FundingPoint(symbol=symbol, ts_ms=int(row["fundingTime"]), rate=D(row["fundingRate"]))
            for row in rows
        ]
        out.sort(key=lambda item: item.ts_ms)
        return out

    def funding_current(self, symbol: str) -> FundingPoint:
        rows = self._request("GET", "/api/v5/public/funding-rate", params={"instId": symbol})
        if not rows:
            raise OkxError("empty", f"no funding for {symbol}", "/api/v5/public/funding-rate")
        row = rows[0]
        return FundingPoint(
            symbol=symbol, ts_ms=int(row.get("fundingTime") or 0), rate=D(row["fundingRate"])
        )

    # -- private -------------------------------------------------------
    def balance(self) -> dict[str, Any]:
        rows = self._request("GET", "/api/v5/account/balance", private=True)
        return rows[0] if rows else {}

    def positions(self, inst_type: str = "SWAP") -> list[dict[str, Any]]:
        return self._request(
            "GET", "/api/v5/account/positions", params={"instType": inst_type}, private=True
        )

    def place_order(self, body: Mapping[str, Any]) -> dict[str, Any]:
        rows = self._request("POST", "/api/v5/trade/order", body=body, private=True)
        if not rows:
            raise OkxError("empty", "order endpoint returned no rows", "/api/v5/trade/order")
        row = rows[0]
        if row.get("sCode") not in (None, "0"):
            raise OkxError(str(row.get("sCode")), str(row.get("sMsg")), "/api/v5/trade/order")
        return row

    def cancel_order(
        self, symbol: str, *, client_id: str | None = None, venue_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"instId": symbol}
        if client_id:
            body["clOrdId"] = client_id
        if venue_id:
            body["ordId"] = venue_id
        rows = self._request("POST", "/api/v5/trade/cancel-order", body=body, private=True)
        return rows[0] if rows else {}

    def order(
        self, symbol: str, *, client_id: str | None = None, venue_id: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"instId": symbol}
        if client_id:
            params["clOrdId"] = client_id
        if venue_id:
            params["ordId"] = venue_id
        rows = self._request("GET", "/api/v5/trade/order", params=params, private=True)
        return rows[0] if rows else {}

    def pending_orders(self, inst_type: str = "SWAP") -> list[dict[str, Any]]:
        return self._request(
            "GET", "/api/v5/trade/orders-pending", params={"instType": inst_type}, private=True
        )

    def fills(self, *, inst_type: str = "SWAP", limit: int = 100) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/api/v5/trade/fills",
            params={"instType": inst_type, "limit": limit},
            private=True,
        )

    def set_leverage(self, symbol: str, leverage: int, *, margin_mode: str = "cross") -> None:
        self._request(
            "POST",
            "/api/v5/account/set-leverage",
            body={"instId": symbol, "lever": str(leverage), "mgnMode": margin_mode},
            private=True,
        )

    def algo_orders(self, symbol: str) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/api/v5/trade/orders-algo-pending",
            params={"instId": symbol, "ordType": "oco"},
            private=True,
        )


def parse_bar(symbol: str, timeframe: str, row: Sequence[str]) -> Bar:
    return Bar(
        symbol=symbol,
        timeframe=timeframe,
        ts_ms=int(row[0]),
        open=D(row[1]),
        high=D(row[2]),
        low=D(row[3]),
        close=D(row[4]),
        volume=D(row[5]),
        confirmed=len(row) < 9 or str(row[8]) == "1",
    )


def parse_instrument(row: Mapping[str, Any]) -> InstrumentSpec:
    inst_type = InstType(str(row.get("instType", "SWAP")))
    ct_val = D(row.get("ctVal") or 1)
    if inst_type is InstType.SWAP:
        lot = D(row.get("lotSz") or "0.01") * ct_val
        minimum = D(row.get("minSz") or "0.01") * ct_val
    else:
        lot = D(row.get("lotSz") or "0.00000001")
        minimum = D(row.get("minSz") or "0.00001")
    return InstrumentSpec(
        symbol=str(row["instId"]),
        inst_type=inst_type,
        base=str(row.get("baseCcy") or row["instId"].split("-")[0]),
        quote=str(row.get("quoteCcy") or "USDT"),
        tick_size=D(row.get("tickSz") or "0.0001"),
        lot_size=lot,
        min_size=minimum,
        contract_value=ct_val,
        maker_fee=D("0.0002") if inst_type is InstType.SWAP else D("0.0008"),
        taker_fee=D("0.0005") if inst_type is InstType.SWAP else D("0.001"),
        max_leverage=D(row.get("lever") or ("20" if inst_type is InstType.SWAP else "1")),
    )
