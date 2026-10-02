import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import create_app
from core.config import Settings


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(environment="test", api_token="x" * 32)
        self.client = TestClient(create_app(self.settings))

    def tearDown(self):
        self.client.close()

    def test_liveness_is_public(self):
        response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})

    def test_readiness_is_unavailable_without_dependency_urls(self):
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")

    @patch("api.main.probe_dependencies")
    def test_readiness_reports_dependency_failures(self, probe):
        probe.return_value = {"database": True, "redis": False}
        self.settings = Settings(
            environment="test",
            database_url="postgresql://localhost/test",
            redis_url="redis://localhost",
        )
        self.client.close()
        self.client = TestClient(create_app(self.settings))

        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"], {"database": True, "redis": False})

    def test_api_requires_a_valid_bearer_token(self):
        self.assertEqual(self.client.get("/api/status").status_code, 401)
        response = self.client.get(
            "/api/status",
            headers={"Authorization": "Bearer " + self.settings.api_token},
        )
        self.assertEqual(response.status_code, 200)

    def test_api_fails_closed_when_token_is_missing(self):
        client = TestClient(create_app(Settings(environment="test")))
        try:
            self.assertEqual(client.get("/api/status").status_code, 503)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
