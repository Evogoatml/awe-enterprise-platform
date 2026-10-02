import unittest

from core.config import Settings


class SettingsTests(unittest.TestCase):
    def test_production_requires_dependencies_and_long_token(self):
        settings = Settings(environment="production")
        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            settings.validate()

    def test_production_accepts_valid_configuration(self):
        Settings(
            environment="production",
            database_url="postgresql://localhost/awe",
            redis_url="rediss://localhost",
            api_token="x" * 32,
        ).validate()

    def test_rejects_invalid_database_url(self):
        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            Settings(database_url="http://localhost/db").validate()

    def test_rejects_invalid_port(self):
        with self.assertRaisesRegex(ValueError, "PORT"):
            Settings(port=70000).validate()

    def test_rejects_invalid_environment_and_host(self):
        with self.assertRaisesRegex(ValueError, "APP_ENV"):
            Settings(environment="staging").validate()
        for host in ("", " bad-host", "bad host", "bad..host", "bad_host"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "HOST"):
                Settings(host=host).validate()
        Settings(host="localhost").validate()
        Settings(host="::1").validate()

    def test_production_requires_every_dependency_and_a_long_token(self):
        valid = {
            "environment": "production",
            "database_url": "postgresql://localhost/awe",
            "redis_url": "redis://localhost",
            "api_token": "x" * 32,
        }
        for field in ("database_url", "redis_url", "api_token"):
            with self.subTest(field=field):
                config = {**valid, field: None}
                with self.assertRaisesRegex(ValueError, field.upper()):
                    Settings(**config).validate()
        with self.assertRaisesRegex(ValueError, "32 characters"):
            Settings(**{**valid, "api_token": "short"}).validate()
        with self.assertRaisesRegex(ValueError, "REDIS_URL"):
            Settings(**{**valid, "redis_url": "http://localhost"}).validate()


if __name__ == "__main__":
    unittest.main()
