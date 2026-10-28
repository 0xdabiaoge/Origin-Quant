"""Users, roles and sessions for the control surface.

Two layers, because the dashboard is the dangerous surface:

* a bearer token, a signed session cookie or a database-backed user session is
  required unless the request comes from loopback and no credentials are
  configured (local single-operator use);
* the control plane is expected to be bound to ``127.0.0.1`` or placed behind a
  TLS reverse proxy.  Nothing here pretends to be safe on the open internet.

Roles are deliberately few.  This is a single-operator engine, not a SaaS:

    owner  everything, including user management and the danger zone
    trader can drive the engine (pause, resume, cycle) but not change config
    viewer read-only
"""

from __future__ import annotations

import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass

from fastapi import Cookie, Header, HTTPException, Request, status

COOKIE_NAME = "oq_session"
SESSION_TTL_S = 12 * 3600

ROLE_OWNER = "owner"
ROLE_TRADER = "trader"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_OWNER, ROLE_TRADER, ROLE_VIEWER)

# Ordered privileges: a role can do everything the roles to its left can.
_RANK = {ROLE_VIEWER: 0, ROLE_TRADER: 1, ROLE_OWNER: 2}


@dataclass(frozen=True, slots=True)
class Principal:
    name: str
    via: str
    role: str = ROLE_OWNER

    def at_least(self, role: str) -> bool:
        return _RANK.get(self.role, 0) >= _RANK.get(role, 0)


def _configured_token() -> str:
    return os.getenv("OQ_API_TOKEN", "").strip()


def _signing_key() -> bytes:
    return os.getenv("OQ_SECRET_KEY", "origin-quant-dev-key").encode("utf-8")


# -- password hashing -------------------------------------------------------
def hash_password(password: str) -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash(password)


def check_password(hashed: str, password: str) -> bool:
    from argon2 import PasswordHasher

    try:
        PasswordHasher().verify(hashed, password)
        return True
    except Exception:
        return False


def bootstrap_password_hash() -> str:
    """Hash from the environment, used when the users table is still empty."""
    hashed = os.getenv("OQ_ADMIN_PASSWORD_HASH", "").strip()
    if hashed:
        return hashed
    plain = os.getenv("OQ_ADMIN_PASSWORD", "")
    if plain:
        return hash_password(plain)
    return ""


# -- sessions ---------------------------------------------------------------
def issue_session(user: str = "operator", role: str = ROLE_OWNER) -> str:
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(_signing_key(), salt="oq-session").dumps(
        {"u": user, "r": role, "n": secrets.token_hex(4)}
    )


def read_session(token: str) -> Principal | None:
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

    try:
        payload = URLSafeTimedSerializer(_signing_key(), salt="oq-session").loads(
            token, max_age=SESSION_TTL_S
        )
    except (BadSignature, SignatureExpired):
        return None
    return Principal(
        name=str(payload.get("u", "operator")),
        via="cookie",
        role=str(payload.get("r", ROLE_OWNER)),
    )


# -- user store -------------------------------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)


def list_users(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT id, username, role, created_ms, last_login_ms FROM users ORDER BY id"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "username": r["username"],
            "role": r["role"],
            "created_ms": r["created_ms"],
            "last_login_ms": r["last_login_ms"],
        }
        for r in rows
    ]


def get_user(conn: sqlite3.Connection, username: str) -> dict[str, object] | None:
    row = conn.execute(
        "SELECT id, username, role, password_hash, created_ms, last_login_ms"
        " FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    return dict(row) if row else None


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    role: str = ROLE_OWNER,
) -> int:
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    cur = conn.execute(
        "INSERT INTO users(username, password_hash, role, created_ms) VALUES (?,?,?,?)",
        (username, hash_password(password), role, _now_ms()),
    )
    return int(cur.lastrowid or 0)


def set_password(conn: sqlite3.Connection, username: str, password: str) -> None:
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?",
        (hash_password(password), username),
    )


def set_role(conn: sqlite3.Connection, username: str, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))


def delete_user(conn: sqlite3.Connection, username: str) -> None:
    conn.execute("DELETE FROM users WHERE username = ?", (username,))


def touch_login(conn: sqlite3.Connection, username: str) -> None:
    conn.execute("UPDATE users SET last_login_ms = ? WHERE username = ?", (_now_ms(), username))


def user_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


# -- request guards ---------------------------------------------------------
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
            return Principal(name="token", via="bearer", role=ROLE_OWNER)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad token")
    if oq_session:
        principal = read_session(oq_session)
        if principal:
            return principal
    if not token and _is_loopback(request):
        return Principal(name="loopback", via="loopback", role=ROLE_OWNER)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")


def _local_writes_blocked(principal: Principal) -> bool:
    return (
        principal.via == "loopback"
        and not _configured_token()
        and os.getenv("OQ_ALLOW_LOCAL_WRITES", "1") != "1"
    )


async def require_write(
    request: Request,
    authorization: str | None = Header(default=None),
    oq_session: str | None = Cookie(default=None),
) -> Principal:
    principal = await require_auth(request, authorization, oq_session)
    # Loopback without a token may read but not move money unless explicitly
    # enabled, so a browser tab on the same machine cannot flatten the book.
    if _local_writes_blocked(principal):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "writes disabled locally")
    if not principal.at_least(ROLE_TRADER):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"role {principal.role} cannot write")
    return principal


async def require_owner(
    request: Request,
    authorization: str | None = Header(default=None),
    oq_session: str | None = Cookie(default=None),
) -> Principal:
    principal = await require_auth(request, authorization, oq_session)
    if _local_writes_blocked(principal):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "writes disabled locally")
    if not principal.at_least(ROLE_OWNER):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner role required")
    return principal


__all__ = [
    "COOKIE_NAME",
    "ROLES",
    "ROLE_OWNER",
    "ROLE_TRADER",
    "ROLE_VIEWER",
    "SESSION_TTL_S",
    "Principal",
    "bootstrap_password_hash",
    "check_password",
    "create_user",
    "delete_user",
    "get_user",
    "hash_password",
    "issue_session",
    "list_users",
    "read_session",
    "require_auth",
    "require_owner",
    "require_write",
    "set_password",
    "set_role",
    "time",
    "touch_login",
    "user_count",
]
