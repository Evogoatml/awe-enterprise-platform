import json
import logging
import sys
import unittest

from security.capability import CapabilityError, CapabilityManager
from security.sandbox import Sandbox, SandboxPolicy


def _events(records):
    return [json.loads(record.getMessage()) for record in records]


class AuditLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = CapabilityManager(secret="a" * 32)
        self.sandbox = Sandbox(self.manager)

    def test_capability_issuance_verification_failure_and_revocation_are_audited(self) -> None:
        with self.assertLogs("security.audit", level=logging.INFO) as captured:
            token = self.manager.issue("audit-subject", ["sandbox:execute"])
            capability = self.manager.verify(token, required_scope="sandbox:execute")
            self.manager.revoke(capability.jti)
            with self.assertRaises(CapabilityError):
                self.manager.verify(token)

        events = _events(captured.records)
        self.assertEqual(
            [event["event"] for event in events],
            [
                "capability_issued",
                "capability_revoked",
                "capability_verification_failed",
            ],
        )
        self.assertEqual(events[0]["subject"], "audit-subject")
        self.assertEqual(events[1]["jti"], capability.jti)
        self.assertNotIn(token, "\n".join(record.getMessage() for record in captured.records))

    def test_denied_execution_and_network_request_are_audited(self) -> None:
        policy = SandboxPolicy(allow_network=True)
        with self.assertLogs("security.audit", level=logging.WARNING) as captured:
            with self.assertRaises(CapabilityError):
                self.sandbox.execute(["true"], "invalid-token", policy)
            token = self.manager.issue("audit-subject", ["sandbox:execute"])
            with self.assertRaises(CapabilityError):
                self.sandbox.execute([sys.executable, "-c", "pass"], token, policy)

        events = _events(captured.records)
        names = [event["event"] for event in events]
        self.assertEqual(names.count("sandbox_execution_denied"), 2)
        network_events = [event for event in events if event["event"] == "sandbox_network_attempt"]
        self.assertEqual(len(network_events), 2)
        self.assertTrue(all(event["outcome"] == "denied" for event in network_events))

    def test_authorized_network_request_is_audited(self) -> None:
        token = self.manager.issue(
            "audit-subject", ["sandbox:execute", "sandbox:network"]
        )
        with self.assertLogs("security.audit", level=logging.INFO) as captured:
            result = self.sandbox.execute(
                [sys.executable, "-c", "pass"],
                token,
                SandboxPolicy(allow_network=True),
            )

        self.assertTrue(result.succeeded)
        events = _events(captured.records)
        self.assertEqual(
            [event["event"] for event in events], ["sandbox_network_attempt"]
        )
        self.assertEqual(events[0]["outcome"], "permitted")

    def test_timeout_kill_is_audited(self) -> None:
        token = self.manager.issue("audit-subject", ["sandbox:execute"])
        with self.assertLogs("security.audit", level=logging.WARNING) as captured:
            result = self.sandbox.execute(
                [sys.executable, "-c", "import time; time.sleep(2)"],
                token,
                SandboxPolicy(wall_timeout_seconds=0.1),
            )

        self.assertTrue(result.timed_out)
        events = _events(captured.records)
        self.assertEqual([event["event"] for event in events], ["sandbox_timeout_kill"])
        self.assertEqual(events[0]["subject"], "audit-subject")


if __name__ == "__main__":
    unittest.main()
