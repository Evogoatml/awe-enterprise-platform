import os
import sys
import tempfile
import unittest

from security.capability import CapabilityError, CapabilityManager
from security.sandbox import Sandbox, SandboxExecutionError, SandboxPolicy, detect_anomalies


class SandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = CapabilityManager(secret="z" * 32)
        self.sandbox = Sandbox(self.manager)

    def _token(self, scopes=("sandbox:execute",), ttl_seconds: float = 30) -> str:
        return self.manager.issue("test-subject", scopes, ttl_seconds=ttl_seconds)

    def test_denies_execution_without_valid_token(self) -> None:
        with self.assertRaises(CapabilityError):
            self.sandbox.execute([sys.executable, "-c", "print(1)"], "bogus-token")

    def test_denies_execution_with_expired_token(self) -> None:
        token = self._token(ttl_seconds=0.01)
        import time

        time.sleep(0.05)
        with self.assertRaises(CapabilityError):
            self.sandbox.execute([sys.executable, "-c", "print(1)"], token)

    def test_denies_execution_with_revoked_token(self) -> None:
        token = self._token()
        capability = self.manager.verify(token)
        self.manager.revoke(capability.jti)
        with self.assertRaises(CapabilityError):
            self.sandbox.execute([sys.executable, "-c", "print(1)"], token)

    def test_denies_execution_with_wrong_scope(self) -> None:
        token = self._token(scopes=("some:other:scope",))
        with self.assertRaises(CapabilityError):
            self.sandbox.execute([sys.executable, "-c", "print(1)"], token)

    def test_allows_execution_with_valid_token_and_captures_stdout(self) -> None:
        token = self._token()
        result = self.sandbox.execute(
            [sys.executable, "-c", "print('hello-from-sandbox')"], token
        )
        self.assertTrue(result.succeeded)
        self.assertIn("hello-from-sandbox", result.stdout)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)

    def test_network_disabled_by_default(self) -> None:
        token = self._token()
        result = self.sandbox.execute([sys.executable, "-c", "print(1)"], token)
        self.assertFalse(result.network_allowed)

    def test_network_requires_explicit_scope_even_if_policy_allows_it(self) -> None:
        token = self._token(scopes=("sandbox:execute",))  # no sandbox:network scope
        policy = SandboxPolicy(allow_network=True)
        with self.assertRaisesRegex(CapabilityError, "sandbox:network"):
            self.sandbox.execute([sys.executable, "-c", "print(1)"], token, policy)

    def test_network_allowed_with_scope_and_policy(self) -> None:
        token = self._token(scopes=("sandbox:execute", "sandbox:network"))
        policy = SandboxPolicy(allow_network=True)
        result = self.sandbox.execute([sys.executable, "-c", "print(1)"], token, policy)
        self.assertTrue(result.network_allowed)

    def test_timeout_is_enforced_and_process_is_killed(self) -> None:
        token = self._token()
        policy = SandboxPolicy(wall_timeout_seconds=0.5, cpu_seconds=5)
        result = self.sandbox.execute(
            [sys.executable, "-c", "import time; time.sleep(5)"], token, policy
        )
        self.assertTrue(result.timed_out)

    def test_output_is_capped(self) -> None:
        token = self._token()
        policy = SandboxPolicy(max_output_bytes=16)
        result = self.sandbox.execute(
            [sys.executable, "-c", "print('x' * 1000)"], token, policy
        )
        self.assertTrue(result.stdout_truncated)
        self.assertLessEqual(len(result.stdout), 16)

    def test_anomaly_flags_are_advisory_and_do_not_block_execution(self) -> None:
        token = self._token()
        # A command containing a "suspicious" token should still run --
        # the heuristic is telemetry only, never a security gate.
        result = self.sandbox.execute(
            [sys.executable, "-c", "print('curl simulated; harmless')"], token
        )
        self.assertTrue(result.succeeded)
        self.assertTrue(any("contains_token" in flag for flag in result.anomaly_flags))

    def test_detect_anomalies_is_pure_and_never_raises(self) -> None:
        flags = detect_anomalies(["echo", "rm -rf /", "&&", "curl evil.example"])
        self.assertIsInstance(flags, tuple)
        self.assertTrue(len(flags) > 0)

    def test_nonexistent_binary_raises_execution_error(self) -> None:
        token = self._token()
        with self.assertRaises(SandboxExecutionError):
            self.sandbox.execute(["/no/such/binary-xyz"], token)

    def test_empty_command_rejected(self) -> None:
        token = self._token()
        with self.assertRaises(ValueError):
            self.sandbox.execute([], token)

    def test_writable_path_is_reachable_from_sandbox(self) -> None:
        token = self._token()
        with tempfile.TemporaryDirectory() as writable_dir:
            marker = os.path.join(writable_dir, "marker.txt")
            policy = SandboxPolicy(writable_paths=(writable_dir,))
            script = (
                "import glob, os\n"
                "paths = glob.glob('writable_*')\n"
                "assert paths, 'no writable path linked'\n"
                "with open(os.path.join(paths[0], 'marker.txt'), 'w') as f:\n"
                "    f.write('ok')\n"
            )
            result = self.sandbox.execute([sys.executable, "-c", script], token, policy)
            self.assertTrue(result.succeeded, result.stderr)
            self.assertTrue(os.path.exists(marker))
            with open(marker) as f:
                self.assertEqual(f.read(), "ok")

    def test_resource_usage_is_reported(self) -> None:
        token = self._token()
        result = self.sandbox.execute([sys.executable, "-c", "print(1)"], token)
        self.assertGreaterEqual(result.duration_seconds, 0)
        self.assertGreaterEqual(result.cpu_time_seconds, 0)


if __name__ == "__main__":
    unittest.main()
