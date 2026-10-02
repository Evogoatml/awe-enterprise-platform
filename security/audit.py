"""Structured audit events for capability and sandbox security decisions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


_LOGGER = logging.getLogger("security.audit")


def audit_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one JSON audit record without secrets or token contents."""
    record = {
        **fields,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _LOGGER.log(level, json.dumps(record, sort_keys=True, separators=(",", ":")))
