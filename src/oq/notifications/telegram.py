"""Send a bounded notification to the configured Telegram recipient.

The only network destination is Telegram's fixed HTTPS host. The token and
recipient come solely from environment variables and are never returned to an
API caller or included in logs. TLS validation uses Python's default trust
store; system proxy variables and HTTP redirects are not followed.
"""

from __future__ import annotations

import http.client
import json
import os
import re

_TOKEN = re.compile(r"[0-9]{3,16}:[A-Za-z0-9_-]{20,100}\Z")
_CHAT = re.compile(r"-?[0-9]{1,20}\Z")


def credentials_available() -> bool:
    return bool(
        _TOKEN.fullmatch(os.getenv("OQ_NOTIFY_TELEGRAM_TOKEN", ""))
        and _CHAT.fullmatch(os.getenv("OQ_NOTIFY_TELEGRAM_CHAT_ID", ""))
    )


def send_telegram(text: str) -> None:
    """Deliver one message or raise a generic error without credential data.

    Delivery acknowledgment is not an exactly-once guarantee: a network
    timeout after Telegram accepted the request may result in a duplicate.
    """
    if not credentials_available():
        raise ValueError("notification credentials unavailable")
    if not text or len(text) > 1000:
        raise ValueError("invalid notification text")
    token = os.environ["OQ_NOTIFY_TELEGRAM_TOKEN"]
    chat_id = os.environ["OQ_NOTIFY_TELEGRAM_CHAT_ID"]
    payload = json.dumps({"chat_id": chat_id, "text": text}, ensure_ascii=False).encode("utf-8")
    connection: http.client.HTTPSConnection | None = None
    try:
        connection = http.client.HTTPSConnection("api.telegram.org", timeout=5)
        connection.request(
            "POST",
            f"/bot{token}/sendMessage",
            body=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read(65_537)
        if response.status != 200 or len(raw) > 65_536:
            raise RuntimeError("notification delivery failed")
        body = json.loads(raw)
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError("notification delivery not acknowledged")
    except Exception:
        # Transport errors can contain a URL/token. Do not retain the cause.
        raise RuntimeError("notification delivery failed") from None
    finally:
        if connection is not None:
            connection.close()
