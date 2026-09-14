import asyncio
import os
import unittest
from unittest.mock import patch

from app.analysis.ai import analyze
from app.config import settings
from app.sports.models import Event


class AiConfigurationTests(unittest.TestCase):
    def test_telegram_token_is_never_sent_as_ai_key(self):
        event = Event("khl", "prematch", "Авангард — СКА")
        with patch.object(settings, "openai_api_key", settings.bot_token):
            result = asyncio.run(analyze([event]))
        self.assertEqual(result["verdict"], "SKIP")
        self.assertIn("совпадает с BOT_TOKEN", result["risks"][0])

    def test_process_token_collision_does_not_replace_dotenv_key(self):
        from app import config

        self.assertEqual(
            config.recover_openai_key(settings.bot_token, "sk-valid-test-key", settings.bot_token),
            "sk-valid-test-key",
        )
        self.assertEqual(
            config.recover_openai_key("sk-process-key", "sk-file-key", settings.bot_token),
            "sk-process-key",
        )


if __name__ == "__main__":
    unittest.main()
