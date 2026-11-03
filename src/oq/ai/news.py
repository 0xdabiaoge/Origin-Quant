"""Bounded opt-in retrieval of public RSS headlines; no account data is sent."""

from __future__ import annotations

import hashlib
import html
import re
import time
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from oq.ai.transport import _check_public_dns

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
}
MAX_FEED_BYTES = 1_000_000


def _clean(text: str, limit: int) -> str:
    return html.unescape(re.sub(r"<[^>]*>", " ", text or "")).strip()[:limit]


def parse_rss(payload: bytes, *, source: str, fetched_ms: int) -> list[dict]:
    if (
        len(payload) > MAX_FEED_BYTES
        # Reject UTF-16/32 XML. Without this, NUL-interleaved DTD declarations
        # bypass the byte checks below while ElementTree still parses them.
        or b"\x00" in payload
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        raise ValueError("invalid RSS document")
    root = ElementTree.fromstring(payload)
    results = []
    for node in root.findall(".//item")[:30]:
        title = _clean(node.findtext("title") or "", 250)
        link = (node.findtext("link") or "").strip()[:1000]
        parsed = urlsplit(link)
        if not title or parsed.scheme != "https" or not parsed.hostname or parsed.username:
            continue
        try:
            published = parsedate_to_datetime(node.findtext("pubDate") or "")
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            published_ms = int(published.timestamp() * 1000)
        except (ValueError, TypeError, OverflowError):
            published_ms = 0
        identity = hashlib.sha256(f"{source}|{link}".encode()).hexdigest()[:24]
        results.append(
            {
                "id": identity,
                "title": title,
                "summary": _clean(node.findtext("description") or "", 600),
                "url": link,
                "source": source,
                "published_ms": published_ms,
                "fetched_ms": fetched_ms,
            }
        )
    return results


def refresh_news() -> dict:
    fetched = int(time.time() * 1000)
    entries: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for name, url in FEEDS.items():
        try:
            _check_public_dns(urlsplit(url).hostname or "")
            with (
                httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client,
                client.stream(
                    "GET", url, headers={"Accept": "application/rss+xml, application/xml"}
                ) as response,
            ):
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_FEED_BYTES:
                        raise ValueError("feed too large")
            for entry in parse_rss(bytes(raw), source=name, fetched_ms=fetched):
                entries[entry["id"]] = entry
        except Exception:
            # Do not leak response bodies, credentials, or exception internals.
            errors[name] = "unavailable or invalid"
    items = sorted(entries.values(), key=lambda item: item["published_ms"], reverse=True)[:50]
    return {"items": items, "fetched_ms": fetched, "errors": errors}
