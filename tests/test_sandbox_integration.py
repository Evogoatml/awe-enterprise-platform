import os
import shutil
import subprocess
import sys
import unittest

from security.capability import CapabilityManager
from security.sandbox import Sandbox


class SandboxRuntimeIntegrationTests(unittest.TestCase):
    def test_network_namespace_is_created_by_real_sandbox_execution(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux network namespaces are required")
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            self.skipTest("effective root privileges are required for unshare --net")

        unshare = shutil.which("unshare")
        if unshare is None:
            self.skipTest("the unshare executable is not installed")

        try:
            probe = subprocess.run(
                [unshare, "--net", "--", "true"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.skipTest("unshare --net capability probe timed out")
        if probe.returncode != 0:
            reason = (probe.stderr or probe.stdout).strip() or f"exit code {probe.returncode}"
            self.skipTest(f"network namespace creation is unavailable: {reason}")

        host_namespace = os.stat("/proc/self/ns/net").st_ino
        manager = CapabilityManager(secret="i" * 32)
        token = manager.issue("runtime-integration", ["sandbox:execute"])
        result = Sandbox(manager).execute(
            [
                sys.executable,
                "-c",
                "import os; print(os.stat('/proc/self/ns/net').st_ino)",
            ],
            token,
        )

        self.assertTrue(result.succeeded, result.stderr)
        self.assertTrue(result.network_isolation_enforced)
        self.assertNotEqual(int(result.stdout.strip()), host_namespace)


if __name__ == "__main__":
    unittest.main()
