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


if __name__ == "__main__":
    unittest.main()
