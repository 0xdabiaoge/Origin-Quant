"""The optional AI council cannot turn incomplete or contradictory votes into orders."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from oq.ai.proposals import Proposal
from oq.ai.settings import AISettings
from oq.ai.transport import request_proposal
from oq.app import ai_api


def settings():
    return AISettings.model_validate(
        {
            "enabled": True,
            "model": "primary",
            "council_enabled": True,
            "council_seats": [
                {
                    "role": "risk",
                    "provider": "anthropic",
                    "model": "reviewer",
                    "endpoint": "https://api.anthropic.com/v1/messages",
                    "api_key_env": "OQ_AI_KEY_REVIEW",
                }
            ],
        }
    )


def snapshot():
    return {
        "captured_ms": int(time.time() * 1000),
        "symbol": "BTC-USDT-SWAP",
        "reference_price": "100",
    }


def test_agreement_requires_all_seats_and_selects_tighter_valid_stop(monkeypatch):
    def fake(member, data):
        stop = "95" if member.model == "primary" else "97"
        return Proposal(action="long", symbol=data["symbol"], stop_px=stop), {"total_tokens": 3}

    monkeypatch.setattr(ai_api, "request_proposal", fake)
    result, usage, council = ai_api._decide(settings(), snapshot())
    assert result.action == "long" and str(result.stop_px) == "97"
    assert usage["total_tokens"] == 6
    assert [row["status"] for row in council] == ["completed", "completed"]
    assert council[1]["role"] == "risk"
    intent = ai_api.proposal_to_intent(result, snapshot(), now_ms=int(time.time() * 1000))
    assert intent is not None and str(intent.tp_ladder[0][0]) == "1.5"
    assert intent.time_stop_bars == 32


@pytest.mark.parametrize("failure", ["error", "disagreement", "wrong_symbol"])
def test_failure_or_disagreement_abstains(monkeypatch, failure):
    def fake(member, data):
        if member.model == "reviewer":
            if failure == "error":
                raise RuntimeError("private provider error")
            if failure == "wrong_symbol":
                return Proposal(action="long", symbol="ETH-USDT-SWAP", stop_px="95"), {}
            return Proposal(action="short", symbol=data["symbol"], stop_px="105"), {}
        return Proposal(action="long", symbol=data["symbol"], stop_px="95"), {}

    monkeypatch.setattr(ai_api, "request_proposal", fake)
    result, _, council = ai_api._decide(settings(), snapshot())
    assert result.action == "wait"
    assert ai_api.proposal_to_intent(result, snapshot(), now_ms=int(time.time() * 1000)) is None
    assert "private provider error" not in json.dumps(council)


def test_anthropic_transport_parses_response_without_exposing_key(monkeypatch):
    from oq.ai import transport

    monkeypatch.setenv("OQ_AI_KEY_REVIEW", "private-secret")
    monkeypatch.setattr(transport, "_check_public_dns", lambda host: None)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": '{"action":"wait","symbol":"BTC-USDT-SWAP"}'}],
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        )

    client_class = httpx.Client
    monkeypatch.setattr(
        transport.httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handle)),
    )
    cfg = AISettings(
        enabled=True,
        provider="anthropic",
        model="test",
        endpoint="https://api.anthropic.com/v1/messages",
        api_key_env="OQ_AI_KEY_REVIEW",
    )
    result, usage = request_proposal(cfg, snapshot())
    assert result.action == "wait" and usage == {"prompt_tokens": 12, "completion_tokens": 4}
    assert requests[0].headers["x-api-key"] == "private-secret"
    assert "private-secret" not in requests[0].content.decode()


def test_openai_transport_uses_reasoning_compatible_token_limit(monkeypatch):
    from oq.ai import transport

    monkeypatch.setenv("OQ_AI_KEY_PRIMARY", "test-secret")
    monkeypatch.setattr(transport, "_check_public_dns", lambda host: None)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"action":"wait","symbol":"BTC-USDT-SWAP"}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    client_class = httpx.Client
    monkeypatch.setattr(
        transport.httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handle)),
    )
    result, _ = request_proposal(AISettings(enabled=True, model="test"), snapshot())
    payload = json.loads(requests[0].content)
    assert result.action == "wait"
    assert payload["max_completion_tokens"] == 600
    assert "max_tokens" not in payload and "temperature" not in payload
