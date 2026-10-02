import time
import unittest

from security.capability import CapabilityError, CapabilityManager


class CapabilityManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = CapabilityManager(secret="x" * 32)

    def test_issue_and_verify_round_trip(self) -> None:
        token = self.manager.issue("agent-1", ["sandbox:execute"], ttl_seconds=30)
        capability = self.manager.verify(token, required_scope="sandbox:execute")
        self.assertEqual(capability.subject, "agent-1")
        self.assertIn("sandbox:execute", capability.scopes)

    def test_tampered_payload_is_rejected(self) -> None:
        token = self.manager.issue("agent-1", ["sandbox:execute"])
        payload_b64, _, signature_b64 = token.partition(".")
        tampered = payload_b64 + "A." + signature_b64
        with self.assertRaisesRegex(CapabilityError, "invalid capability signature|malformed"):
            self.manager.verify(tampered)

    def test_signature_from_different_secret_is_rejected(self) -> None:
        other_manager = CapabilityManager(secret="y" * 32)
        token = other_manager.issue("agent-1", ["sandbox:execute"])
        with self.assertRaisesRegex(CapabilityError, "invalid capability signature"):
            self.manager.verify(token)

    def test_expired_token_is_rejected(self) -> None:
        token = self.manager.issue("agent-1", ["sandbox:execute"], ttl_seconds=0.01)
        time.sleep(0.05)
        with self.assertRaisesRegex(CapabilityError, "expired"):
            self.manager.verify(token)

    def test_revoked_token_is_rejected(self) -> None:
        token = self.manager.issue("agent-1", ["sandbox:execute"], ttl_seconds=30)
        capability = self.manager.verify(token)
        self.manager.revoke(capability.jti)
        with self.assertRaisesRegex(CapabilityError, "revoked"):
            self.manager.verify(token)

    def test_missing_required_scope_is_rejected(self) -> None:
        token = self.manager.issue("agent-1", ["sandbox:execute"], ttl_seconds=30)
        with self.assertRaisesRegex(CapabilityError, "missing required scope"):
            self.manager.verify(token, required_scope="sandbox:network")

    def test_malformed_token_is_rejected(self) -> None:
        with self.assertRaisesRegex(CapabilityError, "malformed"):
            self.manager.verify("not-a-real-token")
        with self.assertRaisesRegex(CapabilityError, "malformed"):
            self.manager.verify("")

    def test_short_secret_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CapabilityManager(secret="too-short")

    def test_ttl_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.issue("agent-1", ["sandbox:execute"], ttl_seconds=0)


if __name__ == "__main__":
    unittest.main()
