import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    database_url: str | None = None
    redis_url: str | None = None
    api_token: str | None = None
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        raw_port = os.getenv("PORT", "8000")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("PORT must be an integer") from exc

        settings = cls(
            environment=os.getenv("APP_ENV", "development").strip().lower(),
            database_url=os.getenv("DATABASE_URL") or None,
            redis_url=os.getenv("REDIS_URL") or None,
            api_token=os.getenv("API_TOKEN") or None,
            host=os.getenv("HOST", "0.0.0.0"),
            port=port,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test, or production")
        if not 1 <= self.port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        self._validate_host(self.host)

        self._validate_url(self.database_url, {"postgres", "postgresql"}, "DATABASE_URL")
        self._validate_url(self.redis_url, {"redis", "rediss"}, "REDIS_URL")

        if self.environment == "production":
            missing = [
                name for name, value in (
                    ("DATABASE_URL", self.database_url),
                    ("REDIS_URL", self.redis_url),
                    ("API_TOKEN", self.api_token),
                ) if not value
            ]
            if missing:
                raise ValueError(f"Missing required production settings: {', '.join(missing)}")
            if len(self.api_token or "") < 32:
                raise ValueError("API_TOKEN must be at least 32 characters in production")

    @staticmethod
    def _validate_url(value: str | None, schemes: set[str], name: str) -> None:
        if value is None:
            return
        parsed = urlparse(value)
        if parsed.scheme not in schemes or not parsed.hostname:
            expected = " or ".join(sorted(schemes))
            raise ValueError(f"{name} must be a valid {expected} URL")

    @staticmethod
    def _validate_host(value: str) -> None:
        if not value or value != value.strip() or any(char.isspace() for char in value):
            raise ValueError("HOST must be a valid IP address or hostname")
        try:
            ipaddress.ip_address(value)
            return
        except ValueError:
            pass

        hostname = value[:-1] if value.endswith(".") else value
        if (
            len(hostname) > 253
            or not hostname
            or any(
                not label
                or len(label) > 63
                or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                for label in hostname.split(".")
            )
        ):
            raise ValueError("HOST must be a valid IP address or hostname")
