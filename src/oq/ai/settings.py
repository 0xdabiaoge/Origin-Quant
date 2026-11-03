"""Bounded, non-secret settings for the optional intelligence service."""

from __future__ import annotations

import os
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
}


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "deepseek", "anthropic"] = "openai"
    model: str = Field(default="", max_length=120)
    api_key_env: str = "OQ_AI_KEY_PRIMARY"
    endpoint: str = Field(default=DEFAULT_ENDPOINTS["openai"], max_length=500)
    timeout_s: int = Field(default=20, ge=2, le=60)

    @field_validator("api_key_env")
    @classmethod
    def key_name(cls, value: str) -> str:
        # Never allow administrators to forward unrelated process secrets.
        if not re.fullmatch(r"OQ_AI_KEY_[A-Z0-9_]{1,48}", value):
            raise ValueError("key environment name must start with OQ_AI_KEY_")
        return value

    @field_validator("model")
    @classmethod
    def model_name(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value):
            raise ValueError("invalid model name")
        return value

    @model_validator(mode="after")
    def validate_endpoint(self) -> ModelSettings:
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
            or parsed.port not in (None, 443)
        ):
            raise ValueError("endpoint must be a credential-free HTTPS URL on port 443")
        if self.endpoint != DEFAULT_ENDPOINTS[self.provider]:
            raise ValueError("built-in provider endpoint cannot be changed")
        return self


class CouncilSeat(ModelSettings):
    role: Literal["market", "news", "risk"] = "risk"


class AISettings(ModelSettings):
    enabled: bool = False
    auto_paper_enabled: bool = False
    paper_confirmation_enabled: bool = False
    auto_symbol: str = "BTC-USDT-SWAP"
    auto_news_enabled: bool = False
    news_interval_s: int = Field(default=1800, ge=300, le=86_400)
    council_enabled: bool = False
    council_seats: list[CouncilSeat] = Field(default_factory=list, max_length=2)

    @field_validator("auto_symbol")
    @classmethod
    def symbol_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z0-9-]{1,40}", value):
            raise ValueError("invalid auto symbol")
        return value

    @model_validator(mode="after")
    def validate_council(self) -> AISettings:
        if self.auto_paper_enabled and self.paper_confirmation_enabled:
            raise ValueError("paper AI candidate and strategy confirmation cannot both be enabled")
        if self.council_enabled and not self.council_seats:
            raise ValueError("at least one council seat is required")
        if self.council_enabled and any(not seat.model for seat in self.council_seats):
            raise ValueError("each council seat needs a model")
        return self


def public_settings(settings: AISettings) -> dict:
    return {**settings.model_dump(), "has_key": bool(os.getenv(settings.api_key_env, ""))}
