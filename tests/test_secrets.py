"""Loading keys from AWS Secrets Manager, with a fake AWS client."""
import json
import os
import unittest
from unittest import mock

from tracker import config


class FakeSecrets:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.asked = value, error, []

    def get_secret_value(self, SecretId):
        self.asked.append(SecretId)
        if self.error:
            raise self.error
        return {"SecretString": self.value}


SECRET = {"X_BEARER_TOKEN": "x-token", "ANTHROPIC_API_KEY": "sk-ant", "DB_CLUSTER_ARN": "arn:aws:rds:cluster",
          "DB_SECRET_ARN": "arn:aws:secretsmanager:db", "UNRELATED": "ignored"}


class SecretTests(unittest.TestCase):
    def setUp(self):
        # snapshot and restore everything load_secrets may change
        self.saved = {k: getattr(config, k) for k in config.SECRET_KEYS + ("APP_SECRET_ID",)}
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for k in config.SECRET_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        for k, v in self.saved.items():
            setattr(config, k, v)

    def test_nothing_configured(self):
        config.APP_SECRET_ID = ""
        self.assertEqual(config.load_secrets(FakeSecrets()), [])

    def test_loads_keys(self):
        config.APP_SECRET_ID = "sharps-leaderboard/app"
        fake = FakeSecrets(json.dumps(SECRET))
        loaded = config.load_secrets(fake)
        self.assertEqual(fake.asked, ["sharps-leaderboard/app"])
        self.assertEqual(sorted(loaded), ["ANTHROPIC_API_KEY", "DB_CLUSTER_ARN", "DB_SECRET_ARN", "X_BEARER_TOKEN"])
        self.assertEqual((config.X_BEARER_TOKEN, config.DB_CLUSTER_ARN), ("x-token", "arn:aws:rds:cluster"))
        self.assertFalse(hasattr(config, "UNRELATED"))

    def test_environment_wins(self):
        config.APP_SECRET_ID = "sharps-leaderboard/app"
        os.environ["X_BEARER_TOKEN"] = "local-override"
        config.X_BEARER_TOKEN = "local-override"
        loaded = config.load_secrets(FakeSecrets(json.dumps(SECRET)))
        self.assertNotIn("X_BEARER_TOKEN", loaded)
        self.assertEqual(config.X_BEARER_TOKEN, "local-override")

    def test_clear_errors(self):
        config.APP_SECRET_ID = "sharps-leaderboard/app"
        with self.assertRaisesRegex(RuntimeError, "Could not read secret"):
            config.load_secrets(FakeSecrets(error=Exception("AccessDeniedException")))
        with self.assertRaisesRegex(RuntimeError, "isn't valid JSON"):
            config.load_secrets(FakeSecrets("just-a-token"))


if __name__ == "__main__":
    unittest.main()
