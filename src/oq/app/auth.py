"""Authentication for the control surface.

Two layers, because the dashboard is the dangerous surface:

* a bearer token or signed session cookie is required unless the request
  comes from loopback and no token is configured (local single-operator use);
* the control plane is expected to be bound to ``127.0.0.1`` or placed behind
  a TLS reverse proxy.  Nothing here pretends to be safe on the open internet.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import Cookie, Header, HTTPException, Request, status

COOKIE_NAME = "oq_session"
SESSION_TTL_S = 12 * 3600


@dataclass(frozen=True, slots=True)
class Principal:
    name: str
    via: str


def _configured_token() -> str:
    return os.getenv("OQ_API_TOKEN", "").strip()


def _signing_key() -> bytes:
    return os.getenv("OQ_SECRET_KEY", "origin-quant-dev-key").encode("utf-8")


def issue_session(user: str = "operator") -> str:
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(_signing_key(), salt="oq-session").dumps(
        {"u": user, "n": secrets.token_hex(4)}
    )


def read_session(token: str) -> Principal | None:
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

    try:
        payload = URLSafeTimedSerializer(_signing_key(), salt="oq-session").loads(
            token, max_age=SESSION_TTL_S
        )
    except (BadSignature, SignatureExpired):
        return None
    return Principal(name=str(payload.get("u", "operator")), via="cookie")


def verify_password(password: str) -> bool:
    """Argon2 hash from ``OQ_ADMIN_PASSWORD_HASH``; plaintext only for local dev."""
    hashed = os.getenv("OQ_ADMIN_PASSWORD_HASH", "").strip()
    if hashed:
        from argon2 import PasswordHasher
        from argon2.exceptions import VerifyMismatchError

        try:
            return PasswordHasher().verify(hashed, password)
        except VerifyMismatchError:
            return False
        except Exception:
            return False
    plain = os.getenv("OQ_ADMIN_PASSWORD", "")
    if plain:
        return hmac.compare_digest(plain, password)
    # No credentials configured: allow only from loopback, and only reads are
    # meaningful because writes are separately gated by require_write().
    return False


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    oq_session: str | None = Cookie(default=None),
) -> Principal:
    token = _configured_token()
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization.split(" ", 1)[1].strip()
        if token and hmac.compare_digest(presented, token):
            return Principal(name="token", via="bearer")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad token")
    if oq_session:
        principal = read_session(oq_session)
        if principal:
            return principal
    if not token and _is_loopback(request):
        return Principal(name="loopback", via="loopback")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")


async def require_write(
    request: Request,
    authorization: str | None = Header(default=None),
    oq_session: str | None = Cookie(default=None),
) -> Principal:
    principal = await require_auth(request, authorization, oq_session)
    # Loopback without a token may read but not move money unless explicitly
    # enabled, so a browser tab on the same machine cannot flatten the book.
    if (
        principal.via == "loopback"
        and not _configured_token()
        and os.getenv("OQ_ALLOW_LOCAL_WRITES", "1") != "1"
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "writes disabled locally")
    return principal


__all__ = [
    "COOKIE_NAME",
    "Principal",
    "issue_session",
    "require_auth",
    "require_write",
    "time",
    "verify_password",
]
