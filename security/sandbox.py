"""A capability-gated process sandbox for running untrusted commands.

Security model (read this before changing behaviour)
------------------------------------------------------
* The only real security *boundary* is the signed capability token
  checked at the top of :meth:`Sandbox.execute`. A caller must present a
  token (see ``security.capability``) that verifies against the
  server-side secret, has not expired or been revoked, and carries the
  ``sandbox:execute`` scope. Everything else below is defense-in-depth
  applied to a command that has already been authorized.
* Command-string heuristics (:func:`detect_anomalies`) are **advisory
  telemetry only**. The flags they produce are attached to
  :class:`SandboxResult` for logging/alerting and are never used to
  allow or deny execution. Pattern-matching a command string is
  trivially bypassed (quoting, encoding, indirection, ...) and must
  never be relied on as a security control.
* Network access is denied by default (``SandboxPolicy.allow_network``).
  Turning it on requires *both* ``allow_network=True`` in the policy
  *and* a capability token carrying the ``sandbox:network`` scope --
  this dual check is the "clearly defined and testable mechanism" for
  allowing network access, and it is enforced purely in code, so it is
  testable without any special host privilege. As defense-in-depth,
  when the current process *does* have the privilege to create a
  network namespace (Linux, effective root, ``unshare`` available), a
  denied network is additionally isolated at the OS level. When it does
  not have that privilege, OS-level isolation is skipped and
  ``SandboxResult.network_isolation_enforced`` is ``False`` so callers
  can see that only the scope/flag gate applied -- this module never
  silently pretends to provide isolation it did not actually set up.
* Writable filesystem paths are restricted to an ephemeral per-execution
  directory. Paths listed in ``SandboxPolicy.writable_paths`` are the
  only host paths made available to the command (via symlinks into that
  directory, which also becomes the command's working directory and
  ``HOME``/``TMPDIR``). This module does not assume a container or
  mount-namespace runtime is available (it must keep working in
  environments without Docker), so it cannot make the rest of the host
  filesystem read-only the way a container would; a command that
  references absolute host paths outside ``writable_paths`` is not
  blocked by this module alone. Combine this with running as a
  dedicated non-root user with minimal filesystem permissions
  (``SandboxPolicy.run_as_uid``/``run_as_gid``) for real enforcement in
  production.
* Resource limits (CPU time, address space, open files) are applied via
  ``RLIMIT_*`` in a ``preexec_fn`` that also sets
  ``PR_SET_NO_NEW_PRIVS`` (Linux) so the child can never regain
  privileges via a setuid/setgid binary. Wall-clock timeouts are
  enforced independently of CPU limits by killing the whole process
  group if the command outlives ``wall_timeout_seconds``.
"""

from __future__ import annotations

import ctypes
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Sequence

from security.capability import CapabilityError, CapabilityManager

#: Substrings that are *suggestive* of risky command construction.
#: Advisory only -- see module docstring. Never used to deny execution.
_SUSPICIOUS_TOKENS: tuple[str, ...] = (
    "rm -rf",
    ";",
    "&&",
    "||",
    "`",
    "$(",
    "curl ",
    "wget ",
    "base64 -d",
    "nc -",
    "chmod 777",
    ">/dev/",
)

_MAX_ADVISORY_COMMAND_LENGTH = 2000


def detect_anomalies(command: Sequence[str]) -> tuple[str, ...]:
    """Return advisory-only telemetry flags for a command.

    This is heuristic, best-effort string matching intended purely for
    logging/alerting dashboards. It is **not** a security boundary: it
    must never be used to allow or deny execution. Callers that need an
    actual security boundary should rely on capability scopes instead.
    """
    joined = " ".join(command)
    flags: list[str] = []
    if len(joined) > _MAX_ADVISORY_COMMAND_LENGTH:
        flags.append("unusually_long_command")
    lowered = joined.lower()
    for token in _SUSPICIOUS_TOKENS:
        if token in lowered:
            flags.append(f"contains_token:{token.strip()}")
    return tuple(flags)


class SandboxExecutionError(Exception):
    """Raised when the sandbox itself fails to set up or launch the command.

    This is distinct from the command's own (possibly non-zero) exit
    code, which is reported via :class:`SandboxResult` instead of being
    raised.
    """


@dataclass(frozen=True)
class SandboxPolicy:
    """Resource and access limits applied to a single sandboxed run."""

    allow_network: bool = False
    writable_paths: tuple[str, ...] = ()
    cpu_seconds: int = 5
    wall_timeout_seconds: float = 10.0
    memory_bytes: int = 256 * 1024 * 1024
    max_output_bytes: int = 64 * 1024
    run_as_uid: int | None = None
    run_as_gid: int | None = None

    def __post_init__(self) -> None:
        if self.cpu_seconds <= 0:
            raise ValueError("cpu_seconds must be positive")
        if self.wall_timeout_seconds <= 0:
            raise ValueError("wall_timeout_seconds must be positive")
        if self.memory_bytes <= 0:
            raise ValueError("memory_bytes must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        if (self.run_as_uid is None) != (self.run_as_gid is None):
            raise ValueError("run_as_uid and run_as_gid must be set together")


@dataclass(frozen=True)
class SandboxResult:
    """The outcome of a single :meth:`Sandbox.execute` call."""

    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_seconds: float
    cpu_time_seconds: float
    max_memory_kb: int | None
    timed_out: bool
    network_allowed: bool
    network_isolation_enforced: bool
    # Advisory-only telemetry -- see module docstring. Never a security gate.
    anomaly_flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and self.exit_code == 0


def _set_no_new_privs() -> None:
    """Best-effort ``PR_SET_NO_NEW_PRIVS`` so the child can never gain
    privileges (e.g. via a setuid binary). Linux-only; no-op elsewhere."""
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_NO_NEW_PRIVS = 38
        libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    except OSError:
        pass


def _build_preexec(policy: SandboxPolicy):
    def _apply() -> None:
        # Drop privileges before anything else, if requested and possible.
        if policy.run_as_gid is not None:
            try:
                os.setgid(policy.run_as_gid)
            except OSError:
                pass
        if policy.run_as_uid is not None:
            try:
                os.setuid(policy.run_as_uid)
            except OSError:
                pass

        _set_no_new_privs()

        for rlimit, value in (
            (resource.RLIMIT_CPU, policy.cpu_seconds),
            (resource.RLIMIT_AS, policy.memory_bytes),
            (resource.RLIMIT_NOFILE, 64),
        ):
            try:
                resource.setrlimit(rlimit, (value, value))
            except (ValueError, OSError):
                # Best-effort: some limits cannot be lowered further on some
                # platforms/containers; execution still proceeds under the
                # remaining enforced limits and the wall-clock timeout.
                pass

    return _apply


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _read_capped(fileobj, limit: int) -> tuple[str, bool]:
    fileobj.seek(0, os.SEEK_END)
    size = fileobj.tell()
    fileobj.seek(0)
    data = fileobj.read(limit)
    truncated = size > limit
    return data.decode("utf-8", errors="replace"), truncated


def _can_isolate_network() -> bool:
    """Whether this process can plausibly create a network namespace."""
    if sys.platform != "linux":
        return False
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    return shutil.which("unshare") is not None


class Sandbox:
    """Executes commands on behalf of callers holding a valid capability."""

    def __init__(self, capability_manager: CapabilityManager) -> None:
        self._capabilities = capability_manager

    def execute(
        self,
        command: Sequence[str],
        token: str,
        policy: SandboxPolicy | None = None,
    ) -> SandboxResult:
        """Verify ``token`` and, if authorized, run ``command`` under ``policy``.

        Raises :class:`security.capability.CapabilityError` if the token
        is invalid, expired, revoked, or missing a required scope.
        Raises :class:`SandboxExecutionError` if the sandbox fails to
        launch the command at all (e.g. the binary does not exist).
        """
        if not command:
            raise ValueError("command must not be empty")
        policy = policy or SandboxPolicy()

        # --- Security boundary: capability verification -------------------
        capability = self._capabilities.verify(token, required_scope="sandbox:execute")
        if policy.allow_network and not capability.has_scope("sandbox:network"):
            raise CapabilityError(
                "network access requires the 'sandbox:network' capability scope"
            )

        # --- Advisory-only telemetry, never a security gate ----------------
        anomaly_flags = detect_anomalies(command)

        sandbox_dir = tempfile.mkdtemp(prefix="awe-sandbox-")
        try:
            for index, path in enumerate(policy.writable_paths):
                os.makedirs(path, exist_ok=True)
                link_name = f"writable_{index}_{os.path.basename(path.rstrip('/')) or 'path'}"
                link_path = os.path.join(sandbox_dir, link_name)
                if not os.path.exists(link_path):
                    os.symlink(os.path.abspath(path), link_path)

            network_isolation_enforced = not policy.allow_network and _can_isolate_network()
            launch_command = list(command)
            if network_isolation_enforced:
                launch_command = ["unshare", "--net", "--", *launch_command]

            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": sandbox_dir,
                "TMPDIR": sandbox_dir,
                "LANG": "C",
            }

            with tempfile.TemporaryFile() as stdout_f, tempfile.TemporaryFile() as stderr_f:
                ru_before = resource.getrusage(resource.RUSAGE_CHILDREN)
                start = time.monotonic()
                try:
                    proc = subprocess.Popen(
                        launch_command,
                        cwd=sandbox_dir,
                        env=env,
                        stdout=stdout_f,
                        stderr=stderr_f,
                        preexec_fn=_build_preexec(policy),
                        start_new_session=True,
                    )
                except OSError as exc:
                    raise SandboxExecutionError(
                        f"failed to launch sandboxed command: {exc}"
                    ) from exc

                timed_out = False
                try:
                    exit_code: int | None = proc.wait(timeout=policy.wall_timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _kill_process_group(proc)
                    try:
                        exit_code = proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        exit_code = None

                duration = time.monotonic() - start
                ru_after = resource.getrusage(resource.RUSAGE_CHILDREN)

                stdout, stdout_truncated = _read_capped(stdout_f, policy.max_output_bytes)
                stderr, stderr_truncated = _read_capped(stderr_f, policy.max_output_bytes)

            cpu_time = max(
                0.0,
                (ru_after.ru_utime + ru_after.ru_stime)
                - (ru_before.ru_utime + ru_before.ru_stime),
            )
            # ru_maxrss is a high-water mark (KB on Linux, bytes on macOS),
            # not an incremental counter, so a delta is only meaningful when
            # a single sandboxed command runs at a time per process -- true
            # for this synchronous API. Concurrent executions in the same
            # process should use a process pool or cgroup accounting instead.
            max_memory_kb = max(0, ru_after.ru_maxrss - ru_before.ru_maxrss)

            return SandboxResult(
                command=tuple(command),
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                duration_seconds=duration,
                cpu_time_seconds=cpu_time,
                max_memory_kb=max_memory_kb or None,
                timed_out=timed_out,
                network_allowed=policy.allow_network,
                network_isolation_enforced=network_isolation_enforced,
                anomaly_flags=anomaly_flags,
            )
        finally:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
