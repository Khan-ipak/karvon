"""Users, roles, login sessions and permission checks.

Passwords are hashed with scrypt from the standard library. A login creates a
random token; the browser keeps it in a cookie and the database keeps only its
SHA-256, so a leaked database does not leak sessions. API clients can also use
HTTP Basic auth.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from karvon.models import Role, User, UserSession, as_utc, utcnow
from karvon.services import DomainError, audit

SESSION_COOKIE = "karvon_session"
SESSION_TTL = timedelta(days=14)

# Permission -> roles that have it. Admin has every permission.
# Viewing visits, the yard, stock and tariffs only needs a login.
PERMISSIONS: dict[str, frozenset[Role]] = {
    "gate": frozenset({Role.operator}),  # gate in, request exit, gate out
    "park": frozenset({Role.operator, Role.yard}),
    "weigh": frozenset({Role.operator, Role.yard}),
    "plan_operation": frozenset({Role.operator}),
    "run_operation": frozenset({Role.warehouse}),  # start and complete
    "cancel_operation": frozenset({Role.operator, Role.warehouse}),
    "customs": frozenset({Role.customs}),
    "pay": frozenset({Role.accountant}),
    "manage_users": frozenset(),
}


class NotAuthenticated(DomainError):
    def __init__(self) -> None:
        super().__init__("not_authenticated", "Log in first")


class Forbidden(DomainError):
    def __init__(self, permission: str) -> None:
        super().__init__("forbidden", f"Your role does not allow this ({permission})")


class LoginRequired(Exception):
    """Raised by web pages; the app turns it into a redirect to /login."""

    def __init__(self, next_url: str) -> None:
        self.next_url = next_url


def can(user: User | None, permission: str) -> bool:
    if user is None or not user.active:
        return False
    return user.role == Role.admin or user.role in PERMISSIONS[permission]


def check(user: User | None, permission: str) -> None:
    if not can(user, permission):
        raise Forbidden(permission)


# --- passwords ---------------------------------------------------------------

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, key = stored.split("$")
        if algo != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, bytes.fromhex(key))


# --- users -------------------------------------------------------------------

MIN_PASSWORD = 8


def create_user(
    session: Session,
    username: str,
    password: str,
    role: Role,
    full_name: str | None = None,
    *,
    check_length: bool = True,
) -> User:
    username = username.strip().lower()
    if not username:
        raise DomainError("invalid_username", "Username is required")
    if session.scalar(select(User).where(User.username == username)) is not None:
        raise DomainError("username_taken", f"User {username} already exists")
    if check_length and len(password) < MIN_PASSWORD:
        raise DomainError("weak_password", f"Password needs at least {MIN_PASSWORD} characters")
    user = User(
        username=username,
        full_name=(full_name or "").strip() or username,
        role=role,
        password_hash=hash_password(password),
    )
    session.add(user)
    session.flush()
    audit(session, "user_created", details=f"{username} ({role.value})")
    return user


def set_active(session: Session, user: User, active: bool) -> None:
    user.active = active
    if not active:
        session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    audit(session, "user_activated" if active else "user_deactivated", details=user.username)
    session.flush()


def set_password(session: Session, user: User, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise DomainError("weak_password", f"Password needs at least {MIN_PASSWORD} characters")
    user.password_hash = hash_password(password)
    session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    audit(session, "password_reset", details=user.username)
    session.flush()


def count_users(session: Session) -> int:
    return session.scalar(select(func.count(User.id))) or 0


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None:
        verify_password(password, _DUMMY_HASH)  # same timing for unknown users
        return None
    if not user.active or not verify_password(password, user.password_hash):
        return None
    return user


_DUMMY_HASH = hash_password(secrets.token_hex(8))


# --- sessions ----------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(session: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    session.add(UserSession(user=user, token_hash=_token_hash(token), expires_at=now + SESSION_TTL))
    session.execute(delete(UserSession).where(UserSession.expires_at < now))
    session.flush()
    return token


def end_session(session: Session, token: str) -> None:
    session.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(token)))


def user_for_token(session: Session, token: str) -> User | None:
    login = session.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if login is None or as_utc(login.expires_at) < utcnow() or not login.user.active:
        return None
    return login.user


def _basic_user(session: Session, header: str) -> User | None:
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        username, _, password = base64.b64decode(value).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return authenticate(session, username, password)


def request_user(request: Request, session: Session) -> User | None:
    """The logged-in user: session cookie first, then HTTP Basic."""
    user = None
    if token := request.cookies.get(SESSION_COOKIE):
        user = user_for_token(session, token)
    if user is None and (header := request.headers.get("authorization")):
        user = _basic_user(session, header)
    if user is not None:
        request.state.user = user
        session.info["actor"] = user  # read by services.audit
    return user


def safe_next(url: str | None) -> str:
    """Only allow redirects within this site after login."""
    if not url or not url.startswith("/") or url.startswith("//") or "\\" in url:
        return "/"
    return url
