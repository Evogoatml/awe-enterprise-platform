"""Security primitives for gating and running untrusted commands.

See ``security.capability`` for signed capability tokens and
``security.sandbox`` for the process sandbox that consumes them.
"""

from security.capability import Capability, CapabilityError, CapabilityManager
from security.sandbox import (
    Sandbox,
    SandboxExecutionError,
    SandboxPolicy,
    SandboxResult,
    detect_anomalies,
)

__all__ = [
    "Capability",
    "CapabilityError",
    "CapabilityManager",
    "Sandbox",
    "SandboxExecutionError",
    "SandboxPolicy",
    "SandboxResult",
    "detect_anomalies",
]
