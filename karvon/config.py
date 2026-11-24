"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("KARVON_DATABASE_URL", "sqlite:///./karvon.db")
    )
    seed_demo: bool = field(default_factory=lambda: _bool(os.environ.get("KARVON_SEED_DEMO"), True))
    default_language: str = field(
        default_factory=lambda: os.environ.get("KARVON_DEFAULT_LANGUAGE", "en")
    )
    # Terminal's local time zone (IANA name, e.g. Asia/Tashkent). Times are stored
    # in UTC and shown, and grouped into days, in this zone.
    timezone: str = field(default_factory=lambda: os.environ.get("KARVON_TIMEZONE", "UTC"))
    currency: str = field(default_factory=lambda: os.environ.get("KARVON_CURRENCY", "USD"))
    terminal_name: str = field(
        default_factory=lambda: os.environ.get("KARVON_TERMINAL_NAME", "Demo Border Terminal")
    )
    # Public demo: shows a banner and blocks changes to users and passwords.
    public_demo: bool = field(
        default_factory=lambda: _bool(os.environ.get("KARVON_PUBLIC_DEMO"), False)
    )
    # Creates this admin on startup if no user has that name yet.
    admin_username: str = field(
        default_factory=lambda: os.environ.get("KARVON_ADMIN_USERNAME", "admin")
    )
    admin_password: str | None = field(
        default_factory=lambda: os.environ.get("KARVON_ADMIN_PASSWORD") or None, repr=False
    )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)
