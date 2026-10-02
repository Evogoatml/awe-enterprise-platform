"""Signed capability tokens for gating sandboxed execution.

Security model
---------------
A capability token authorizes a specific *subject* to perform a specific
set of *scopes* (for example ``sandbox:execute`` or ``sandbox:network``)
until an expiry timestamp. Tokens are HMAC-SHA256 signed with a
server-side secret that never leaves the issuing process, so a caller
cannot forge or extend a token without that secret. This is the
replacement for an "insecure capability token" scheme that trusted an
unsigned, caller-supplied string as proof of authorization -- an
unsigned token is not a security boundary, because any caller can mint
one.

Tokens are intentionally short-lived (see ``ttl_seconds``) and are
additionally checked against an in-memory revocation set keyed by a
random token id (``jti``), so a token can be invalidated before it
expires (for example, when a session ends). The revocation set is
per-process; deployments that run multiple worker processes should back
``CapabilityManager`` with a shared store (e.g. Redis) so revocation is
visible across processes -- ``revoke``/``is_revoked`` are written as
small, overridable hooks for exactly that purpose.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Iterable

from security.audit import audit_event


class CapabilityError(Exception):
    """Raised when a capability token is missing, malformed, expired, revoked,
    or lacks a required scope."""


@dataclass(frozen=True)
class Capability:
    """A verified capability, decoded from a token."""

    subject: str
    scopes: tuple[str, ...]
    issued_at: float
    expires_at: float
    jti: str

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class CapabilityManager:
    """Issues and verifies HMAC-signed capability tokens.

    The secret is read from the ``AWE_SANDBOX_SECRET`` environment
    variable by default. If it is not set, an ephemeral per-process
    secret is generated -- this is safe for local/single-process use
    (tokens issued and verified by the same process still work) but
    means tokens will *not* verify across separate processes/restarts.
    Any multi-process or production deployment must set
    ``AWE_SANDBOX_SECRET`` (or pass ``secret=`` explicitly) to a stable,
    sufficiently random value.
    """

    MIN_SECRET_LENGTH = 32

    def __init__(self, secret: bytes | str | None = None) -> None:
        if secret is None:
            secret = os.getenv("AWE_SANDBOX_SECRET")
        if secret is None:
            secret = secrets.token_bytes(32)
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        if len(secret) < self.MIN_SECRET_LENGTH:
            raise ValueError(
                f"Capability secret must be at least {self.MIN_SECRET_LENGTH} bytes"
            )
        self._secret = secret
        self._revoked: set[str] = set()

    def issue(self, subject: str, scopes: Iterable[str], ttl_seconds: float = 60.0) -> str:
        """Mint a new signed token for ``subject`` carrying ``scopes``."""
        if not subject:
            raise ValueError("subject must not be empty")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        now = time.time()
        payload = {
            "sub": subject,
            "scopes": sorted(set(scopes)),
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": secrets.token_hex(16),
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        token = f"{_b64encode(payload_bytes)}.{_b64encode(signature)}"
        audit_event(
            "capability_issued",
            subject=subject,
            scopes=payload["scopes"],
            jti=payload["jti"],
            expires_at=payload["exp"],
        )
        return token

    def verify(self, token: str, required_scope: str | None = None) -> Capability:
        """Verify ``token`` and return the decoded :class:`Capability`.

        Raises :class:`CapabilityError` if the token is malformed, the
        signature does not match, it has expired, it has been revoked,
        or it lacks ``required_scope`` (when given).
        """
        try:
            return self._verify(token, required_scope)
        except CapabilityError as exc:
            audit_event(
                "capability_verification_failed",
                level=logging.WARNING,
                reason=str(exc),
                required_scope=required_scope,
            )
            raise

    def _verify(self, token: str, required_scope: str | None = None) -> Capability:
        if not token or "." not in token:
            raise CapabilityError("malformed capability token")

        payload_b64, _, signature_b64 = token.partition(".")
        try:
            payload_bytes = _b64decode(payload_b64)
            signature = _b64decode(signature_b64)
        except Exception as exc:
            raise CapabilityError("malformed capability token") from exc

        expected_signature = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise CapabilityError("invalid capability signature")

        try:
            payload = json.loads(payload_bytes)
            capability = Capability(
                subject=payload["sub"],
                scopes=tuple(payload["scopes"]),
                issued_at=payload["iat"],
                expires_at=payload["exp"],
                jti=payload["jti"],
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CapabilityError("malformed capability token") from exc

        if time.time() > capability.expires_at:
            raise CapabilityError("capability token expired")
        if self.is_revoked(capability.jti):
            raise CapabilityError("capability token revoked")
        if required_scope is not None and not capability.has_scope(required_scope):
            raise CapabilityError(f"capability missing required scope: {required_scope}")

        return capability

    def revoke(self, jti: str) -> None:
        """Invalidate a token by id, even if it has not expired yet."""
        self._revoked.add(jti)
        audit_event("capability_revoked", jti=jti)

    def is_revoked(self, jti: str) -> bool:
        return jti in self._revoked
